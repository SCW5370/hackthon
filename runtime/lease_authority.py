"""
SafeExec V1 Lease Authority
Ed25519 签名签发和验签 Lease
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
import secrets
import threading
from typing import Optional

from .contracts import ActionIntent, ActionLease, Decision


class LeaseAuthority:
    """
    Lease Authority 负责：
    1. 使用 Ed25519 私钥签发 Lease
    2. 提供公钥给 Guard 验签
    """

    def __init__(self, private_key_b64: str, key_id: str = "x5-runtime-key-01"):
        """
        Args:
            private_key_b64: Base64 编码的 Ed25519 私钥
            key_id: 密钥标识符
        """
        import nacl.signing

        self._signing_key = nacl.signing.SigningKey(
            base64.b64decode(private_key_b64)
        )
        self._verify_key = self._signing_key.verify_key
        self.key_id = key_id

    @classmethod
    def generate_keypair(cls) -> tuple[str, str]:
        """
        生成新的 Ed25519 密钥对
        Returns: (private_key_b64, public_key_b64)
        """
        import nacl.signing

        signing_key = nacl.signing.SigningKey.generate()
        verify_key = signing_key.verify_key

        private_b64 = base64.b64encode(signing_key.encode()).decode("ascii")
        public_b64 = base64.b64encode(verify_key.encode()).decode("ascii")

        return private_b64, public_b64

    def get_public_key_b64(self) -> str:
        """获取公钥 (Base64)"""
        return base64.b64encode(self._verify_key.encode()).decode("ascii")

    def issue_lease(
        self,
        intent: ActionIntent,
        decision: Decision,
        audience: str,
        mission_id: str,
        ttl_ms: int = 5000,
    ) -> ActionLease:
        """
        签发一个新的 Lease

        Args:
            intent: ActionIntent
            decision: 允许的 Decision
            audience: 目标 Guard ID
            mission_id: Mission ID
            ttl_ms: Lease 有效期 (默认 5 秒)

        Returns:
            ActionLease with signature
        """
        if decision.effect.value != "allow":
            raise ValueError("Cannot issue lease for denied decision")

        # 计算 request hash
        request_hash = intent.compute_hash()

        # 生成随机 nonce (128-bit)
        nonce = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")

        # 构建 Lease (不含 signature)
        lease = ActionLease.create(
            request_id=intent.request_id,
            request_hash=request_hash,
            principal_id=intent.principal_id,
            audience=audience,
            mission_id=mission_id,
            decision_id=decision.decision_id,
            key_id=self.key_id,
            nonce=nonce,
            signature="",  # 先空着
            ttl_ms=ttl_ms,
        )

        # 用私钥签名
        message = lease.to_json_for_signing()
        signed = self._signing_key.sign(message)
        signature_b64 = base64.urlsafe_b64encode(signed.signature).decode("ascii").rstrip("=")

        # 创建最终的 Lease (含签名)
        final_lease_dict = lease.to_dict()
        final_lease_dict["signature"] = signature_b64

        return ActionLease(**final_lease_dict)

    @staticmethod
    def verify_lease(
        lease: ActionLease,
        public_key_b64: str,
        intent_normalized: bytes,
        clock_tolerance_ms: int = 1000,
    ) -> tuple[bool, str]:
        """
        验签 Lease (在 Guard 端执行)

        Returns: (is_valid, reason)
        """
        import nacl.signing
        import nacl.exceptions

        try:
            verify_key = nacl.signing.VerifyKey(
                base64.b64decode(public_key_b64)
            )
        except Exception as e:
            return False, f"INVALID_PUBLIC_KEY: {e}"

        try:
            # 验证签名
            message = lease.to_json_for_signing()
            signature = base64.urlsafe_b64decode(
                lease.signature + "=="
            )
            verify_key.verify(message, signature)
        except nacl.exceptions.BadSignatureError:
            return False, "INVALID_SIGNATURE"
        except Exception as e:
            return False, f"SIGNATURE_VERIFY_ERROR: {e}"

        # 验证时间
        now_ms = int(time.time() * 1000)
        if now_ms > lease.expires_at_ms + clock_tolerance_ms:
            return False, "LEASE_EXPIRED"

        if now_ms < lease.issued_at_ms - clock_tolerance_ms:
            return False, "LEASE_NOT_YET_VALID"

        # 验证 request hash
        expected_hash = "sha256:" + hashlib.sha256(intent_normalized).hexdigest()
        if lease.request_hash != expected_hash:
            return False, "REQUEST_HASH_MISMATCH"

        return True, "VALID"


class LeaseStore:
    """
    Guard 端使用的 Lease 存储
    SQLite 防重放
    """

    def __init__(self, db_path: str = ":memory:"):
        import sqlite3
        self._sqlite3 = sqlite3
        self._lock = threading.Lock()
        self.db = sqlite3.connect(
            db_path,
            isolation_level="IMMEDIATE",
            check_same_thread=False,
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS consumed_leases (
                lease_id TEXT PRIMARY KEY,
                consumed_at REAL
            )
            """
        )
        self.db.commit()

    def try_consume(self, lease_id: str) -> bool:
        """
        尝试原子消费 lease_id
        Returns: True if newly consumed, False if already exists (replay)
        """
        with self._lock:
            cursor = self.db.cursor()
            try:
                cursor.execute(
                    "INSERT INTO consumed_leases (lease_id, consumed_at) VALUES (?, ?)",
                    (lease_id, time.time())
                )
                self.db.commit()
                return True
            except self._sqlite3.IntegrityError:
                # UNIQUE constraint violated = replay attack
                return False

    def close(self):
        with self._lock:
            self.db.close()
