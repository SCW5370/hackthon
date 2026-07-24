"""Strict SafeExec V1 action contract used by the Lab Agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import time
import uuid
from typing import Any, Mapping


SCHEMA_VERSION = "safeexec.action.v1"
ACTION = "lab.sample.transfer"
RESOURCE_TYPE = "lab.sample"
SAMPLE_IDS = ("sample-A", "sample-B")
LOCATION_NAMES = (
    "cold-storage",
    "analyzer-01",
    "waste-bin",
    "quarantine-zone",
)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True)
class ActionPlan:
    """The stable business part of an action, before request metadata."""

    sample_id: str
    source: str
    destination: str

    def __post_init__(self) -> None:
        if self.sample_id not in SAMPLE_IDS:
            raise ValueError(f"unknown sample_id: {self.sample_id!r}")
        if self.source not in LOCATION_NAMES:
            raise ValueError(f"unknown source: {self.source!r}")
        if self.destination not in LOCATION_NAMES:
            raise ValueError(f"unknown destination: {self.destination!r}")
        if self.source == self.destination:
            raise ValueError("source and destination must differ")

    def business_payload(self, principal_id: str = "lab-agent-01") -> dict[str, Any]:
        return {
            "principal_id": principal_id,
            "action": ACTION,
            "resource": {"type": RESOURCE_TYPE, "id": self.sample_id},
            "arguments": {
                "source": self.source,
                "destination": self.destination,
            },
        }


def plan_fingerprint(plan: ActionPlan, principal_id: str = "lab-agent-01") -> str:
    """Hash only stable business fields so Legacy and SafeExec runs compare."""

    digest = hashlib.sha256(_canonical_json(plan.business_payload(principal_id)))
    return f"sha256:{digest.hexdigest()}"


def build_action_intent(
    plan: ActionPlan,
    *,
    principal_id: str = "lab-agent-01",
    request_id: str | None = None,
    issued_at_ms: int | None = None,
) -> dict[str, Any]:
    value = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or str(uuid.uuid4()),
        "principal_id": principal_id,
        "issued_at_ms": issued_at_ms or int(time.time() * 1000),
        "action": ACTION,
        "resource": {"type": RESOURCE_TYPE, "id": plan.sample_id},
        "arguments": {
            "source": plan.source,
            "destination": plan.destination,
        },
    }
    validate_action_intent(value)
    return value


def validate_action_intent(value: Mapping[str, Any]) -> None:
    expected = {
        "schema_version",
        "request_id",
        "principal_id",
        "issued_at_ms",
        "action",
        "resource",
        "arguments",
    }
    if set(value) != expected:
        raise ValueError(
            f"invalid ActionIntent fields; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )

    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    try:
        parsed_request_id = uuid.UUID(str(value["request_id"]))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("request_id must be a UUIDv4") from exc
    if parsed_request_id.version != 4:
        raise ValueError("request_id must be a UUIDv4")
    if not isinstance(value["principal_id"], str) or not value["principal_id"].strip():
        raise ValueError("principal_id must be a non-empty string")
    if isinstance(value["issued_at_ms"], bool) or not isinstance(
        value["issued_at_ms"], int
    ):
        raise ValueError("issued_at_ms must be an integer")
    if value["action"] != ACTION:
        raise ValueError(f"unsupported action: {value['action']!r}")

    resource = value["resource"]
    if not isinstance(resource, Mapping) or set(resource) != {"type", "id"}:
        raise ValueError("resource must contain exactly type and id")
    if resource["type"] != RESOURCE_TYPE:
        raise ValueError("unsupported resource type")

    arguments = value["arguments"]
    if not isinstance(arguments, Mapping) or set(arguments) != {
        "source",
        "destination",
    }:
        raise ValueError("arguments must contain exactly source and destination")
    ActionPlan(
        sample_id=str(resource["id"]),
        source=str(arguments["source"]),
        destination=str(arguments["destination"]),
    )


def plan_to_dict(plan: ActionPlan) -> dict[str, str]:
    return asdict(plan)
