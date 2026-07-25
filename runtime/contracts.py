"""
SafeExec V1 Data Contracts
严格定义 ActionIntent, MissionSpec, Fact, Decision, ActionLease 的结构和验证
"""
from __future__ import annotations

import json
import uuid
import hashlib
import base64
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from enum import Enum

from biolab.catalog import (
    LINE_ID,
    LINE_RESOURCE_TYPE,
    LOCATION_NAMES,
    RESET_ACTION,
    SAMPLE_IDS,
    SAMPLE_RESOURCE_TYPE,
    TRANSFER_ACTION,
)

# ============================================================
# 枚举类
# ============================================================

class Effect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class SchemaVersion(str, Enum):
    ACTION_V1 = "safeexec.action.v1"
    ACTION_V2 = "safeexec.action.v2"
    MISSION_V1 = "safeexec.mission.v1"
    WORK_ORDER_V1 = "safeexec.work-order.v1"
    LEASE_V1 = "safeexec.lease.v1"
    DECISION_V1 = "safeexec.decision.v1"


# ============================================================
# ActionIntent - Agent → Runtime
# ============================================================

ALLOWED_ACTION = TRANSFER_ACTION
ALLOWED_ACTIONS = {TRANSFER_ACTION, RESET_ACTION}
ALLOWED_RESOURCE_IDS = set(SAMPLE_IDS)
ALLOWED_POSITIONS = set(LOCATION_NAMES)


@dataclass(frozen=True)
class ActionIntent:
    schema_version: str
    request_id: str
    principal_id: str
    issued_at_ms: int
    action: str
    resource: dict
    arguments: dict
    work_order_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> ActionIntent:
        """从字典创建，字段不全或有未知字段都会失败"""
        base_fields = {
            "schema_version", "request_id", "principal_id",
            "issued_at_ms", "action", "resource", "arguments"
        }
        schema_version = data.get("schema_version")
        if schema_version == SchemaVersion.ACTION_V1.value:
            required_fields = base_fields
        elif schema_version == SchemaVersion.ACTION_V2.value:
            required_fields = base_fields | {"work_order_id"}
        else:
            raise ValueError(f"Invalid schema_version: {schema_version}")

        # 检查未知字段
        unknown = set(data.keys()) - required_fields
        if unknown:
            raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")

        # 检查缺少字段
        missing = required_fields - set(data.keys())
        if missing:
            raise ValueError(f"Missing fields: {', '.join(sorted(missing))}")

        work_order_id = data.get("work_order_id")
        if schema_version == SchemaVersion.ACTION_V2.value:
            try:
                uuid.UUID(str(work_order_id))
            except (TypeError, ValueError) as exc:
                raise ValueError("work_order_id must be a UUID") from exc

        # 验证 action
        if data["action"] not in ALLOWED_ACTIONS:
            raise ValueError(f"Invalid action: {data['action']}")

        # 验证 resource/arguments according to the action. This is deliberately
        # exact so a signed reset cannot be confused with a sample transfer.
        resource = data["resource"]
        args = data["arguments"]
        if not isinstance(resource, dict) or set(resource) != {"type", "id"}:
            raise ValueError("resource must contain exactly type and id")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        if data["action"] == TRANSFER_ACTION:
            if resource.get("type") != SAMPLE_RESOURCE_TYPE:
                raise ValueError(f"Invalid resource type: {resource.get('type')}")
            if resource.get("id") not in ALLOWED_RESOURCE_IDS:
                raise ValueError(f"Invalid resource id: {resource.get('id')}")
            if set(args) != {"source", "destination"}:
                raise ValueError(
                    "transfer arguments must contain exactly source and destination"
                )
            if args.get("source") not in ALLOWED_POSITIONS:
                raise ValueError(f"Invalid source: {args.get('source')}")
            if args.get("destination") not in ALLOWED_POSITIONS:
                raise ValueError(f"Invalid destination: {args.get('destination')}")
            if args["source"] == args["destination"]:
                raise ValueError("source and destination must differ")
        else:
            if resource != {"type": LINE_RESOURCE_TYPE, "id": LINE_ID}:
                raise ValueError("Invalid reset resource")
            if args != {"command": "reset"}:
                raise ValueError("reset arguments must equal {'command': 'reset'}")

        # 验证 request_id 是 UUID
        try:
            uuid.UUID(data["request_id"])
        except ValueError:
            raise ValueError(f"Invalid request_id format: {data['request_id']}")

        # 验证时间偏差不超过 10 秒
        now_ms = int(time.time() * 1000)
        time_diff = abs(now_ms - data["issued_at_ms"])
        if time_diff > 10000:
            raise ValueError(f"Request time too old or in future: diff={time_diff}ms")

        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            principal_id=data["principal_id"],
            issued_at_ms=data["issued_at_ms"],
            action=data["action"],
            resource=resource,
            arguments=args,
            work_order_id=work_order_id,
        )

    def to_dict(self) -> dict:
        value = asdict(self)
        if self.schema_version == SchemaVersion.ACTION_V1.value:
            value.pop("work_order_id", None)
        return value

    def normalize(self) -> bytes:
        """
        规范化格式用于哈希：UTF-8 JSON、键排序、无空格、禁止浮点
        所有整数字段必须是 int，不能是 float
        """
        d = {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "principal_id": self.principal_id,
            "issued_at_ms": self.issued_at_ms,  # 已是 int
            "action": self.action,
            "resource": self.resource,
            "arguments": self.arguments,
        }
        if self.schema_version == SchemaVersion.ACTION_V2.value:
            d["work_order_id"] = self.work_order_id
        return json.dumps(d, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def compute_hash(self) -> str:
        """SHA-256 哈希"""
        return "sha256:" + hashlib.sha256(self.normalize()).hexdigest()


# ============================================================
# MissionSpec - 可信配置
# ============================================================

@dataclass(frozen=True)
class RequiredFact:
    key: str
    equals: bool
    max_age_ms: int


@dataclass(frozen=True)
class Grant:
    grant_id: str
    action: str
    resource: dict
    arguments: dict
    required_facts: tuple[RequiredFact, ...]


@dataclass(frozen=True)
class MissionSpec:
    schema_version: str
    mission_id: str
    principal_id: str
    valid_from_ms: int
    valid_until_ms: int
    grants: tuple[Grant, ...]

    @classmethod
    def from_dict(cls, data: dict) -> MissionSpec:
        if data.get("schema_version") != SchemaVersion.MISSION_V1.value:
            raise ValueError(f"Invalid schema_version: {data.get('schema_version')}")

        grants = []
        for g in data.get("grants", []):
            facts = tuple(
                RequiredFact(
                    key=rf["key"],
                    equals=rf["equals"],
                    max_age_ms=int(rf["max_age_ms"]),
                )
                for rf in g.get("required_facts", [])
            )
            grants.append(Grant(
                grant_id=g["grant_id"],
                action=g["action"],
                resource=g["resource"],
                arguments=g["arguments"],
                required_facts=facts,
            ))

        return cls(
            schema_version=data["schema_version"],
            mission_id=data["mission_id"],
            principal_id=data["principal_id"],
            valid_from_ms=int(data["valid_from_ms"]),
            valid_until_ms=int(data["valid_until_ms"]),
            grants=tuple(grants),
        )

    def is_valid_at(self, now_ms: int) -> bool:
        return self.valid_from_ms <= now_ms <= self.valid_until_ms

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# Fact - 设备适配器 → Runtime
# ============================================================

@dataclass
class Fact:
    key: str
    value: Any  # bool | None
    source: str
    confidence: float
    timestamp: float  # Unix time in seconds
    ttl_ms: int
    evidence: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Fact:
        required = {"key", "value", "source", "timestamp", "ttl_ms"}
        missing = required - set(data.keys())
        if missing:
            raise ValueError(f"Missing fields: {', '.join(sorted(missing))}")

        return cls(
            key=data["key"],
            value=data["value"],
            source=data["source"],
            confidence=float(data.get("confidence", 1.0)),
            timestamp=float(data["timestamp"]),
            ttl_ms=int(data["ttl_ms"]),
            evidence=data.get("evidence", {}),
        )

    def is_fresh(self, now: float) -> bool:
        """检查 Fact 是否在 TTL 内"""
        age_ms = (now - self.timestamp) * 1000
        return age_ms <= self.ttl_ms

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# Decision - Runtime → Agent
# ============================================================

DENY_CODES = {
    "INVALID_REQUEST",
    "UNKNOWN_PRINCIPAL",
    "MISSION_EXPIRED",
    "NO_MATCHING_GRANT",
    "FACT_MISSING",
    "FACT_STALE",
    "FACT_MISMATCH",
    "WORK_ORDER_REQUIRED",
    "WORK_ORDER_NOT_FOUND",
    "WORK_ORDER_EXPIRED",
    "WORK_ORDER_PRINCIPAL_MISMATCH",
    "WORK_ORDER_GRANT_MISMATCH",
    "WORK_ORDER_GRANT_CONSUMED",
    "EXECUTOR_UNAVAILABLE",
}


@dataclass(frozen=True)
class Decision:
    schema_version: str
    decision_id: str
    request_id: str
    effect: Effect
    reason_code: str
    matched_grant_id: Optional[str]
    evaluated_at_ms: int
    fact_refs: tuple[str, ...]  # ["camera.healthy@1784800000.125"]

    @classmethod
    def deny(
        cls,
        request_id: str,
        reason_code: str,
        decision_id: Optional[str] = None,
    ) -> Decision:
        if reason_code not in DENY_CODES:
            raise ValueError(f"Invalid deny reason: {reason_code}")

        return cls(
            schema_version=SchemaVersion.DECISION_V1.value,
            decision_id=decision_id or str(uuid.uuid4()),
            request_id=request_id,
            effect=Effect.DENY,
            reason_code=reason_code,
            matched_grant_id=None,
            evaluated_at_ms=int(time.time() * 1000),
            fact_refs=(),
        )

    @classmethod
    def allow(
        cls,
        request_id: str,
        matched_grant_id: str,
        fact_refs: tuple[str, ...],
        decision_id: Optional[str] = None,
    ) -> Decision:
        return cls(
            schema_version=SchemaVersion.DECISION_V1.value,
            decision_id=decision_id or str(uuid.uuid4()),
            request_id=request_id,
            effect=Effect.ALLOW,
            reason_code="GRANT_MATCHED",
            matched_grant_id=matched_grant_id,
            evaluated_at_ms=int(time.time() * 1000),
            fact_refs=fact_refs,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["effect"] = self.effect.value
        return d


# ============================================================
# ActionLease - Runtime → Guard (内部契约)
# ============================================================

@dataclass(frozen=True)
class ActionLease:
    schema_version: str
    lease_id: str
    request_id: str
    request_hash: str
    principal_id: str
    audience: str
    mission_id: str
    decision_id: str
    issued_at_ms: int
    expires_at_ms: int
    nonce: str
    key_id: str
    signature: str  # Base64url Ed25519 signature

    @classmethod
    def create(
        cls,
        request_id: str,
        request_hash: str,
        principal_id: str,
        audience: str,
        mission_id: str,
        decision_id: str,
        key_id: str,
        nonce: str,
        signature: str,
        ttl_ms: int = 5000,
    ) -> ActionLease:
        now_ms = int(time.time() * 1000)
        return cls(
            schema_version=SchemaVersion.LEASE_V1.value,
            lease_id=str(uuid.uuid4()),
            request_id=request_id,
            request_hash=request_hash,
            principal_id=principal_id,
            audience=audience,
            mission_id=mission_id,
            decision_id=decision_id,
            issued_at_ms=now_ms,
            expires_at_ms=now_ms + ttl_ms,
            nonce=nonce,
            key_id=key_id,
            signature=signature,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json_for_signing(self) -> bytes:
        """用于签名的 JSON（不包含 signature 字段）"""
        d = {
            "schema_version": self.schema_version,
            "lease_id": self.lease_id,
            "request_id": self.request_id,
            "request_hash": self.request_hash,
            "principal_id": self.principal_id,
            "audience": self.audience,
            "mission_id": self.mission_id,
            "decision_id": self.decision_id,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "nonce": self.nonce,
            "key_id": self.key_id,
        }
        return json.dumps(d, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


# ============================================================
# ExecutionReceipt - Guard → 调用方
# ============================================================

@dataclass
class ExecutionReceipt:
    lease_id: str
    command_id: str
    status: str  # "executed" | "failed" | "blocked"
    message: str
    executed_at_ms: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# Event - 审计日志
# ============================================================

@dataclass
class Event:
    seq: int
    event: str
    timestamp: float
    source: str
    severity: str  # "info" | "warning" | "critical"
    incident_id: Optional[str]
    payload: dict

    @classmethod
    def create(
        cls,
        event: str,
        source: str,
        payload: dict,
        severity: str = "info",
        incident_id: Optional[str] = None,
    ) -> Event:
        return cls(
            seq=0,  # 会在 EventLedger 中分配
            event=event,
            timestamp=time.time(),
            source=source,
            severity=severity,
            incident_id=incident_id,
            payload=payload,
        )

    def to_dict(self) -> dict:
        return asdict(self)
