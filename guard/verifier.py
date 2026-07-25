"""
SafeExec V1 Guard Verifier
Lease 验签 + 防重放
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Optional

from runtime.contracts import ActionLease, ActionIntent
from runtime.lease_authority import LeaseStore


class LeaseVerifier:
    """
    Guard 端 Lease 验证器

    验证顺序:
    1. JSON 结构
    2. Lease 签名
    3. audience
    4. 时间与 1 秒时钟容差
    5. Intent 哈希
    6. Principal 与 Request ID
    7. SQLite 原子消费 (防重放)
    """

    def __init__(
        self,
        public_key_b64: str,
        expected_audience: str = "joy-guard-01",
        db_path: str = ":memory:",
    ):
        import nacl.signing

        self._verify_key = nacl.signing.VerifyKey(base64.b64decode(public_key_b64))
        self.expected_audience = expected_audience
        self._lease_store = LeaseStore(db_path)

        # 验证统计
        self._stats = {
            "total": 0,
            "valid": 0,
            "invalid_signature": 0,
            "expired": 0,
            "audience_mismatch": 0,
            "hash_mismatch": 0,
            "replay": 0,
        }

    def verify_and_consume(
        self,
        intent_dict: dict,
        lease_dict: dict,
    ) -> tuple[bool, str, Optional[dict]]:
        """
        验证 Lease 并原子消费

        Returns:
            (is_valid, reason, receipt_or_none)
        """
        self._stats["total"] += 1

        # 1. JSON 结构验证
        try:
            lease = self._lease_from_dict(lease_dict)
            intent = self._intent_from_dict(intent_dict)
        except ValueError as e:
            return False, f"INVALID_STRUCTURE: {e}", None

        # 2. 签名验证
        is_valid, reason = self._verify_signature(lease)
        if not is_valid:
            self._stats["invalid_signature" if "signature" in reason.lower() else "invalid_signature"] += 1
            return False, reason, None

        # 3. audience 验证
        if lease.audience != self.expected_audience:
            self._stats["audience_mismatch"] += 1
            return False, f"AUDIENCE_MISMATCH: expected={self.expected_audience}, got={lease.audience}", None

        # 4. 时间验证 (1 秒容差)
        now_ms = int(time.time() * 1000)
        if now_ms > lease.expires_at_ms + 1000:
            self._stats["expired"] += 1
            return False, "LEASE_EXPIRED", None

        if now_ms < lease.issued_at_ms - 1000:
            self._stats["expired"] += 1
            return False, "LEASE_NOT_YET_VALID", None

        # 5. Intent 哈希验证
        intent_normalized = self._normalize_intent(intent_dict)
        expected_hash = "sha256:" + hashlib.sha256(intent_normalized).hexdigest()
        if lease.request_hash != expected_hash:
            self._stats["hash_mismatch"] += 1
            return False, "REQUEST_HASH_MISMATCH", None

        # 6. Request ID 验证
        if lease.request_id != intent.get("request_id"):
            self._stats["hash_mismatch"] += 1
            return False, "REQUEST_ID_MISMATCH", None
        if (
            intent.get("schema_version") == "safeexec.action.v2"
            and lease.mission_id != intent.get("work_order_id")
        ):
            self._stats["hash_mismatch"] += 1
            return False, "WORK_ORDER_BINDING_MISMATCH", None

        # 7. SQLite 原子消费 (防重放)
        if not self._lease_store.try_consume(lease.lease_id):
            self._stats["replay"] += 1
            return False, "LEASE_ALREADY_CONSUMED", None

        # 验证通过
        self._stats["valid"] += 1

        receipt = {
            "lease_id": lease.lease_id,
            "command_id": intent.get("request_id"),
            "status": "verified",
            "verified_at_ms": int(time.time() * 1000),
        }

        return True, "VALID", receipt

    def _verify_signature(self, lease: ActionLease) -> tuple[bool, str]:
        """验证 Ed25519 签名"""
        import nacl.exceptions

        try:
            message = lease.to_json_for_signing()
            signature = base64.urlsafe_b64decode(lease.signature + "==")
            self._verify_key.verify(message, signature)
            return True, "VALID"
        except nacl.exceptions.BadSignatureError:
            return False, "INVALID_SIGNATURE"
        except Exception as e:
            return False, f"SIGNATURE_ERROR: {e}"

    @staticmethod
    def _lease_from_dict(d: dict) -> ActionLease:
        """从字典创建 ActionLease"""
        required = {
            "schema_version", "lease_id", "request_id", "request_hash",
            "principal_id", "audience", "mission_id", "decision_id",
            "issued_at_ms", "expires_at_ms", "nonce", "key_id", "signature",
        }
        missing = required - set(d.keys())
        if missing:
            raise ValueError(f"Missing lease fields: {', '.join(missing)}")
        return ActionLease(**d)

    @staticmethod
    def _intent_from_dict(d: dict) -> dict:
        """验证并返回 intent 字典"""
        required = {"schema_version", "request_id", "principal_id", "action", "resource", "arguments"}
        missing = required - set(d.keys())
        if missing:
            raise ValueError(f"Missing intent fields: {', '.join(missing)}")
        return d

    @staticmethod
    def _normalize_intent(intent_dict: dict) -> bytes:
        """
        规范化 intent 用于哈希
        UTF-8 JSON、键排序、无空格、禁止浮点
        """
        # 确保 issued_at_ms 是 int
        d = {
            "schema_version": intent_dict["schema_version"],
            "request_id": intent_dict["request_id"],
            "principal_id": intent_dict["principal_id"],
            "issued_at_ms": int(intent_dict["issued_at_ms"]),
            "action": intent_dict["action"],
            "resource": intent_dict["resource"],
            "arguments": intent_dict["arguments"],
        }
        if intent_dict.get("schema_version") == "safeexec.action.v2":
            if "work_order_id" not in intent_dict:
                raise ValueError("safeexec.action.v2 requires work_order_id")
            d["work_order_id"] = intent_dict["work_order_id"]
        return json.dumps(d, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def get_stats(self) -> dict:
        return dict(self._stats)

    def close(self):
        self._lease_store.close()
