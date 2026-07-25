"""SafeExec ActionIntent adapter for the JOY BioLab executor."""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Mapping

from .driver import JoyDriver
from .locations import LOCATION_NAMES, SAMPLE_IDS
from biolab.catalog import LINE_ID, RECYCLE_ACTION, RESET_ACTION, TRANSFER_ACTION


ACTION_SCHEMAS = {"safeexec.action.v1", "safeexec.action.v2"}
EXECUTION_SCHEMA = "safeexec.execution.v1"


def action_intent_to_joy(value: Mapping[str, Any]) -> dict[str, str]:
    base_fields = {
        "schema_version",
        "request_id",
        "principal_id",
        "issued_at_ms",
        "action",
        "resource",
        "arguments",
    }
    schema_version = value.get("schema_version")
    expected = (
        base_fields | {"work_order_id"}
        if schema_version == "safeexec.action.v2"
        else base_fields
    )
    if set(value) != expected:
        raise ValueError(
            f"invalid ActionIntent fields; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )
    if schema_version not in ACTION_SCHEMAS:
        raise ValueError("unsupported ActionIntent schema")
    if schema_version == "safeexec.action.v2":
        try:
            uuid.UUID(str(value["work_order_id"]))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("work_order_id must be a UUID") from exc
    try:
        request_id = uuid.UUID(str(value["request_id"]))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("request_id must be a UUIDv4") from exc
    if request_id.version != 4:
        raise ValueError("request_id must be a UUIDv4")
    if not isinstance(value["principal_id"], str) or not value["principal_id"].strip():
        raise ValueError("principal_id must be a non-empty string")
    if isinstance(value["issued_at_ms"], bool) or not isinstance(
        value["issued_at_ms"], int
    ):
        raise ValueError("issued_at_ms must be an integer")
    if value["action"] != TRANSFER_ACTION:
        raise ValueError("only lab.sample.transfer maps to a JOY transfer")

    resource = value["resource"]
    if not isinstance(resource, Mapping) or set(resource) != {"type", "id"}:
        raise ValueError("resource must contain exactly type and id")
    if resource["type"] != "lab.sample" or resource["id"] not in SAMPLE_IDS:
        raise ValueError("unsupported resource")

    arguments = value["arguments"]
    if not isinstance(arguments, Mapping) or set(arguments) != {
        "source",
        "destination",
    }:
        raise ValueError("arguments must contain exactly source and destination")
    source = arguments["source"]
    destination = arguments["destination"]
    if source not in LOCATION_NAMES or destination not in LOCATION_NAMES:
        raise ValueError("unknown source or destination")
    if source == destination:
        raise ValueError("source and destination must differ")

    return {
        "command_id": str(request_id),
        "action": "TRANSFER",
        "sample_id": str(resource["id"]),
        "source": str(source),
        "destination": str(destination),
    }


class JoyExecutor:
    """Synchronous SafeExec Executor backed by the existing JoyDriver."""

    def __init__(
        self,
        driver: JoyDriver,
        *,
        executor_id: str = "joy-guard-01",
        timeout: float = 120.0,
        poll_interval: float = 0.25,
        monotonic: Callable[[], float] = time.monotonic,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.driver = driver
        self.executor_id = executor_id
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._monotonic = monotonic
        self._now_ms = now_ms

    def execute(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        execution_id = str(uuid.uuid4())
        started_at_ms = self._now_ms()
        if intent.get("action") == RESET_ACTION:
            return self._execute_reset(intent, execution_id, started_at_ms)
        if intent.get("action") == RECYCLE_ACTION:
            return self._execute_recycle(intent, execution_id, started_at_ms)
        try:
            command = action_intent_to_joy(intent)
        except (TypeError, ValueError) as exc:
            return self._failed(
                execution_id,
                str(intent.get("request_id", "")),
                started_at_ms,
                "INVALID_INTENT",
                str(exc),
            )

        try:
            self.driver.transfer(
                command["sample_id"],
                command["source"],
                command["destination"],
                command_id=command["command_id"],
            )
            final = self._wait_for_destination(
                command["sample_id"], command["destination"]
            )
        except TimeoutError as exc:
            return self._failed(
                execution_id,
                command["command_id"],
                started_at_ms,
                "EXECUTION_TIMEOUT",
                str(exc),
            )
        except (ConnectionError, RuntimeError) as exc:
            return self._failed(
                execution_id,
                command["command_id"],
                started_at_ms,
                "JOY_UNAVAILABLE",
                str(exc),
            )
        except Exception as exc:  # JOY RPC errors are not consistently typed.
            return self._failed(
                execution_id,
                command["command_id"],
                started_at_ms,
                "EXECUTION_FAILED",
                str(exc),
            )

        return {
            "schema_version": EXECUTION_SCHEMA,
            "execution_id": execution_id,
            "request_id": command["command_id"],
            "executor_id": self.executor_id,
            "state": "succeeded",
            "started_at_ms": started_at_ms,
            "finished_at_ms": self._now_ms(),
            "result": {
                "sample_id": command["sample_id"],
                "location": command["destination"],
                "sample_locations": final.get("sample_locations", {}),
                "current_dock": final.get("current_dock", command["destination"]),
                "arm_state": final.get("arm_state", "IDLE"),
                "platform_state": final.get("platform_state", "IDLE"),
                "unsafe_outcome": bool(final.get("unsafe_outcome", False)),
            },
            "error_code": None,
            "error": None,
        }

    def _execute_recycle(
        self,
        intent: Mapping[str, Any],
        execution_id: str,
        started_at_ms: int,
    ) -> dict[str, Any]:
        resource = intent.get("resource")
        arguments = intent.get("arguments")
        if (
            not isinstance(resource, Mapping)
            or resource.get("type") != "lab.sample"
            or resource.get("id") not in SAMPLE_IDS
            or arguments
            != {
                "source": "analyzer-01",
                "destination": "cold-storage",
            }
        ):
            return self._failed(
                execution_id,
                str(intent.get("request_id", "")),
                started_at_ms,
                "INVALID_INTENT",
                "invalid lab.sample.recycle payload",
        )
        sample_id = str(resource["id"])
        try:
            self._wait_for_idle()
            final = self.driver.recycle(sample_id)
        except Exception as exc:
            return self._failed(
                execution_id,
                str(intent.get("request_id", "")),
                started_at_ms,
                "EXECUTION_FAILED",
                str(exc),
            )
        return {
            "schema_version": EXECUTION_SCHEMA,
            "execution_id": execution_id,
            "request_id": str(intent.get("request_id", "")),
            "executor_id": self.executor_id,
            "state": "succeeded",
            "started_at_ms": started_at_ms,
            "finished_at_ms": self._now_ms(),
            "result": {
                "sample_id": sample_id,
                "location": "cold-storage",
                "recycled": True,
                "sample_locations": final.get("sample_locations", {}),
                "current_dock": final.get("current_dock", "analyzer-01"),
                "arm_state": final.get("arm_state", "IDLE"),
                "platform_state": final.get("platform_state", "IDLE"),
                "unsafe_outcome": bool(final.get("unsafe_outcome", False)),
            },
            "error_code": None,
            "error": None,
        }

    def _execute_reset(
        self,
        intent: Mapping[str, Any],
        execution_id: str,
        started_at_ms: int,
    ) -> dict[str, Any]:
        expected_resource = {"type": "lab.line", "id": LINE_ID}
        if (
            intent.get("resource") != expected_resource
            or intent.get("arguments") != {"command": "reset"}
        ):
            return self._failed(
                execution_id,
                str(intent.get("request_id", "")),
                started_at_ms,
                "INVALID_INTENT",
                "invalid lab.line.reset payload",
            )
        try:
            final = self.driver.health()
            if final.get("active_command") or final.get("queue_depth", 0):
                raise RuntimeError("line reset is allowed only while JOY is idle")
            final = self.driver.reset()
        except Exception as exc:
            return self._failed(
                execution_id,
                str(intent.get("request_id", "")),
                started_at_ms,
                "EXECUTION_FAILED",
                str(exc),
            )
        return {
            "schema_version": EXECUTION_SCHEMA,
            "execution_id": execution_id,
            "request_id": str(intent.get("request_id", "")),
            "executor_id": self.executor_id,
            "state": "succeeded",
            "started_at_ms": started_at_ms,
            "finished_at_ms": self._now_ms(),
            "result": {
                "line_id": LINE_ID,
                "reset": True,
                "sample_locations": final.get("sample_locations", {}),
                "unsafe_outcome": bool(final.get("unsafe_outcome", False)),
            },
            "error_code": None,
            "error": None,
        }

    def _wait_for_destination(
        self, sample_id: str, destination: str
    ) -> dict[str, Any]:
        deadline = self._monotonic() + self.timeout
        last: dict[str, Any] = {}
        while self._monotonic() < deadline:
            last = self.driver.health()
            locations = last.get("sample_locations", {})
            if (
                isinstance(locations, Mapping)
                and locations.get(sample_id) == destination
                and last.get("arm_state") in {"COMPLETED", "IDLE"}
            ):
                return last
            time.sleep(self.poll_interval)
        raise TimeoutError(
            f"{sample_id} did not reach {destination}; last status={last!r}"
        )

    def _wait_for_idle(self) -> dict[str, Any]:
        deadline = self._monotonic() + self.timeout
        last: dict[str, Any] = {}
        while self._monotonic() < deadline:
            last = self.driver.health()
            if (
                not last.get("active_command")
                and not last.get("queue_depth", 0)
                and last.get("arm_state") == "IDLE"
            ):
                return last
            time.sleep(self.poll_interval)
        raise TimeoutError(f"JOY did not become idle; last status={last!r}")

    def _failed(
        self,
        execution_id: str,
        request_id: str,
        started_at_ms: int,
        error_code: str,
        error: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": EXECUTION_SCHEMA,
            "execution_id": execution_id,
            "request_id": request_id,
            "executor_id": self.executor_id,
            "state": "failed",
            "started_at_ms": started_at_ms,
            "finished_at_ms": self._now_ms(),
            "result": None,
            "error_code": error_code,
            "error": error,
        }
