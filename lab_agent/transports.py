"""Transport boundary between the Lab Agent and execution paths."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contracts import validate_action_intent


class ActionTransport(Protocol):
    def submit(self, intent: dict[str, Any]) -> dict[str, Any]:
        """Submit one validated ActionIntent."""


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 130.0,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            detail = {"error": body}
        return {
            "state": "denied" if exc.code in {401, 403, 409} else "failed",
            "http_status": exc.code,
            "detail": detail,
        }
    except (TimeoutError, URLError) as exc:
        raise ConnectionError(f"request to {url!r} failed: {exc}") from exc

    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("execution endpoint returned a non-object JSON value")
    return parsed


@dataclass(frozen=True)
class SafeExecTransport:
    runtime_url: str

    def submit(self, intent: dict[str, Any]) -> dict[str, Any]:
        validate_action_intent(intent)
        return _post_json(f"{self.runtime_url.rstrip('/')}/v1/actions", intent)


@dataclass(frozen=True)
class LegacyTransport:
    bridge_url: str
    demo_token: str
    confirmed_unsafe_demo: bool = False

    def submit(self, intent: dict[str, Any]) -> dict[str, Any]:
        validate_action_intent(intent)
        if not self.confirmed_unsafe_demo:
            raise PermissionError(
                "Legacy execution requires the explicit unsafe-demo confirmation"
            )
        if not self.demo_token:
            raise PermissionError("LAB_LEGACY_TOKEN must be configured")
        return _post_json(
            f"{self.bridge_url.rstrip('/')}/legacy/v1/execute",
            intent,
            headers={"X-Legacy-Demo-Token": self.demo_token},
        )


class DryRunTransport:
    def submit(self, intent: dict[str, Any]) -> dict[str, Any]:
        validate_action_intent(intent)
        return {
            "schema_version": "safeexec.execution.v1",
            "request_id": intent["request_id"],
            "executor_id": "dry-run",
            "state": "not_executed",
            "result": None,
            "error_code": None,
        }
