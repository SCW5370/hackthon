"""Signed, short-lived business authorization for SafeExec actions."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import json
import secrets
import threading
import time
import uuid
from typing import Any, Mapping

import nacl.exceptions
import nacl.signing

from .contracts import ActionIntent, Grant, MissionSpec, RequiredFact, SchemaVersion


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True)
class WorkOrderGrant:
    grant_id: str
    action: str
    resource: dict[str, Any]
    arguments: dict[str, Any]
    required_facts: tuple[RequiredFact, ...]
    max_executions: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkOrderGrant":
        required = {
            "grant_id",
            "action",
            "resource",
            "arguments",
            "required_facts",
            "max_executions",
        }
        if set(value) != required:
            raise ValueError(
                "invalid WorkOrder grant fields; "
                f"missing={sorted(required - set(value))}, "
                f"extra={sorted(set(value) - required)}"
            )
        maximum = value["max_executions"]
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
            raise ValueError("max_executions must be a positive integer")
        facts = tuple(
            RequiredFact(
                key=str(item["key"]),
                equals=item["equals"],
                max_age_ms=int(item["max_age_ms"]),
            )
            for item in value["required_facts"]
        )
        return cls(
            grant_id=str(value["grant_id"]),
            action=str(value["action"]),
            resource=dict(value["resource"]),
            arguments=dict(value["arguments"]),
            required_facts=facts,
            max_executions=maximum,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def matches(self, intent: ActionIntent) -> bool:
        return (
            self.action == intent.action
            and self.resource == intent.resource
            and self.arguments == intent.arguments
        )


@dataclass(frozen=True)
class WorkOrder:
    schema_version: str
    work_order_id: str
    issuer_id: str
    subject_principal_id: str
    issued_at_ms: int
    valid_from_ms: int
    valid_until_ms: int
    nonce: str
    grants: tuple[WorkOrderGrant, ...]
    operator_note: str
    key_id: str
    signature: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkOrder":
        required = {
            "schema_version",
            "work_order_id",
            "issuer_id",
            "subject_principal_id",
            "issued_at_ms",
            "valid_from_ms",
            "valid_until_ms",
            "nonce",
            "grants",
            "operator_note",
            "key_id",
            "signature",
        }
        if set(value) != required:
            raise ValueError(
                "invalid WorkOrder fields; "
                f"missing={sorted(required - set(value))}, "
                f"extra={sorted(set(value) - required)}"
            )
        if value["schema_version"] != SchemaVersion.WORK_ORDER_V1.value:
            raise ValueError("unsupported WorkOrder schema_version")
        try:
            uuid.UUID(str(value["work_order_id"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("work_order_id must be a UUID") from exc
        for field in ("issuer_id", "subject_principal_id", "nonce", "key_id"):
            if not isinstance(value[field], str) or not value[field].strip():
                raise ValueError(f"{field} must be a non-empty string")
        for field in ("issued_at_ms", "valid_from_ms", "valid_until_ms"):
            if isinstance(value[field], bool) or not isinstance(value[field], int):
                raise ValueError(f"{field} must be an integer")
        if value["valid_from_ms"] > value["valid_until_ms"]:
            raise ValueError("WorkOrder validity window is inverted")
        if not isinstance(value["operator_note"], str) or len(value["operator_note"]) > 500:
            raise ValueError("operator_note must contain at most 500 characters")
        if not isinstance(value["signature"], str) or not value["signature"]:
            raise ValueError("signature must be a non-empty string")
        grants = tuple(WorkOrderGrant.from_dict(item) for item in value["grants"])
        if not grants:
            raise ValueError("WorkOrder must contain at least one grant")
        grant_ids = [grant.grant_id for grant in grants]
        if len(grant_ids) != len(set(grant_ids)):
            raise ValueError("WorkOrder grant_id values must be unique")
        return cls(
            schema_version=str(value["schema_version"]),
            work_order_id=str(value["work_order_id"]),
            issuer_id=str(value["issuer_id"]),
            subject_principal_id=str(value["subject_principal_id"]),
            issued_at_ms=value["issued_at_ms"],
            valid_from_ms=value["valid_from_ms"],
            valid_until_ms=value["valid_until_ms"],
            nonce=str(value["nonce"]),
            grants=grants,
            operator_note=value["operator_note"],
            key_id=str(value["key_id"]),
            signature=value["signature"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def signing_payload(self) -> bytes:
        value = self.to_dict()
        del value["signature"]
        return _canonical_json(value)

    def is_valid_at(self, now_ms: int) -> bool:
        return self.valid_from_ms <= now_ms <= self.valid_until_ms


class WorkOrderIssuer:
    """Control-plane signer. Runtime receives only the matching public key."""

    def __init__(
        self,
        private_key_b64: str,
        *,
        issuer_id: str = "biolab-control-plane",
        key_id: str = "biolab-control-plane-key-01",
    ) -> None:
        self._signing_key = nacl.signing.SigningKey(
            base64.b64decode(private_key_b64)
        )
        self.issuer_id = issuer_id
        self.key_id = key_id

    def public_key_b64(self) -> str:
        return base64.b64encode(self._signing_key.verify_key.encode()).decode("ascii")

    def issue(
        self,
        *,
        subject_principal_id: str,
        grants: list[dict[str, Any]],
        valid_for_ms: int,
        operator_note: str = "",
        now_ms: int | None = None,
    ) -> WorkOrder:
        if valid_for_ms < 30_000 or valid_for_ms > 8 * 60 * 60 * 1000:
            raise ValueError("valid_for_ms must be between 30 seconds and 8 hours")
        issued_at = now_ms if now_ms is not None else int(time.time() * 1000)
        unsigned = WorkOrder(
            schema_version=SchemaVersion.WORK_ORDER_V1.value,
            work_order_id=str(uuid.uuid4()),
            issuer_id=self.issuer_id,
            subject_principal_id=subject_principal_id,
            issued_at_ms=issued_at,
            valid_from_ms=issued_at - 1000,
            valid_until_ms=issued_at + valid_for_ms,
            nonce=base64.urlsafe_b64encode(secrets.token_bytes(16))
            .decode("ascii")
            .rstrip("="),
            grants=tuple(WorkOrderGrant.from_dict(item) for item in grants),
            operator_note=operator_note,
            key_id=self.key_id,
            signature="pending",
        )
        signature = self._signing_key.sign(unsigned.signing_payload()).signature
        value = unsigned.to_dict()
        value["signature"] = (
            base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        )
        return WorkOrder.from_dict(value)


class WorkOrderRegistry:
    """Runtime-owned registry and atomic execution budget."""

    def __init__(
        self,
        trusted_issuer_keys: Mapping[str, str],
        mission_spec: MissionSpec,
    ) -> None:
        self._keys = {
            issuer_id: nacl.signing.VerifyKey(base64.b64decode(public_key))
            for issuer_id, public_key in trusted_issuer_keys.items()
        }
        self._mission_spec = mission_spec
        self._orders: dict[str, WorkOrder] = {}
        self._execution_counts: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    @property
    def trusted_issuers(self) -> tuple[str, ...]:
        return tuple(sorted(self._keys))

    def register(self, value: Mapping[str, Any]) -> WorkOrder:
        order = WorkOrder.from_dict(value)
        verify_key = self._keys.get(order.issuer_id)
        if verify_key is None:
            raise ValueError("untrusted WorkOrder issuer")
        try:
            signature = base64.urlsafe_b64decode(order.signature + "==")
            verify_key.verify(order.signing_payload(), signature)
        except (ValueError, nacl.exceptions.BadSignatureError) as exc:
            raise ValueError("invalid WorkOrder signature") from exc
        now_ms = int(time.time() * 1000)
        if order.issued_at_ms > now_ms + 10_000:
            raise ValueError("WorkOrder issued_at_ms is in the future")
        if order.valid_until_ms - order.valid_from_ms > 8 * 60 * 60 * 1000 + 1000:
            raise ValueError("WorkOrder validity exceeds 8 hours")
        if order.subject_principal_id != self._mission_spec.principal_id:
            raise ValueError("WorkOrder subject is outside OrganizationPolicy")
        for grant in order.grants:
            if not self._within_policy(grant):
                raise ValueError(
                    f"WorkOrder grant {grant.grant_id} exceeds OrganizationPolicy"
                )
        with self._lock:
            existing = self._orders.get(order.work_order_id)
            if existing is not None:
                if existing != order:
                    raise ValueError("work_order_id already exists with different content")
                return existing
            self._orders[order.work_order_id] = order
        return order

    def _within_policy(self, candidate: WorkOrderGrant) -> bool:
        for grant in self._mission_spec.grants:
            if (
                grant.action == candidate.action
                and grant.resource == candidate.resource
                and grant.arguments == candidate.arguments
                and grant.required_facts == candidate.required_facts
            ):
                return True
        return False

    def get(self, work_order_id: str) -> WorkOrder | None:
        with self._lock:
            return self._orders.get(work_order_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._status(order)
                for order in sorted(
                    self._orders.values(),
                    key=lambda item: item.issued_at_ms,
                    reverse=True,
                )
            ]

    def status(self, work_order_id: str) -> dict[str, Any] | None:
        with self._lock:
            order = self._orders.get(work_order_id)
            return None if order is None else self._status(order)

    def _status(self, order: WorkOrder) -> dict[str, Any]:
        value = order.to_dict()
        value["execution_counts"] = {
            grant.grant_id: self._execution_counts.get(
                (order.work_order_id, grant.grant_id), 0
            )
            for grant in order.grants
        }
        value["verification"] = {
            "signature": "verified",
            "issuer_trusted": True,
            "within_organization_policy": True,
        }
        return value

    def authorize(
        self,
        intent: ActionIntent,
        *,
        now_ms: int | None = None,
    ) -> tuple[WorkOrderGrant | None, str | None]:
        if not intent.work_order_id:
            return None, "WORK_ORDER_REQUIRED"
        with self._lock:
            order = self._orders.get(intent.work_order_id)
            if order is None:
                return None, "WORK_ORDER_NOT_FOUND"
            timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
            if not order.is_valid_at(timestamp):
                return None, "WORK_ORDER_EXPIRED"
            if intent.principal_id != order.subject_principal_id:
                return None, "WORK_ORDER_PRINCIPAL_MISMATCH"
            match = next((grant for grant in order.grants if grant.matches(intent)), None)
            if match is None:
                return None, "WORK_ORDER_GRANT_MISMATCH"
            consumed = self._execution_counts.get(
                (order.work_order_id, match.grant_id), 0
            )
            if consumed >= match.max_executions:
                return None, "WORK_ORDER_GRANT_CONSUMED"
            return match, None

    def consume(self, work_order_id: str, grant_id: str) -> None:
        with self._lock:
            key = (work_order_id, grant_id)
            self._execution_counts[key] = self._execution_counts.get(key, 0) + 1
