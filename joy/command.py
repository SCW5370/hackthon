"""Non-security command envelope handed off to the SafeExec team.

This module validates only shape and known JOY entity names. It intentionally
does not implement policy, leases, signatures, replay protection, or routing
authorization.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .locations import validate_location_name, validate_sample_id


@dataclass(frozen=True)
class JoyCommand:
    command_id: str
    action: str
    sample_id: str
    source: str
    destination: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "JoyCommand":
        expected = {
            "command_id",
            "action",
            "sample_id",
            "source",
            "destination",
        }
        if set(value) != expected:
            missing = sorted(expected - set(value))
            extra = sorted(set(value) - expected)
            raise ValueError(f"invalid command fields; missing={missing}, extra={extra}")

        fields = {name: value[name] for name in expected}
        if not all(isinstance(item, str) for item in fields.values()):
            raise ValueError("all command fields must be strings")
        if not fields["command_id"].strip():
            raise ValueError("command_id must be non-empty")
        if fields["action"] != "TRANSFER":
            raise ValueError("only the TRANSFER action is supported")

        validate_sample_id(fields["sample_id"])
        validate_location_name(fields["source"])
        validate_location_name(fields["destination"])
        if fields["source"] == fields["destination"]:
            raise ValueError("source and destination must differ")
        return cls(**fields)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
