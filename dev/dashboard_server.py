"""SafeExec V2 Dashboard BFF.

The browser never creates ActionIntent objects and never starts JOY or shell
processes. It controls the persistent Orchestrator and reads physical evidence.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from biolab.catalog import LOCATION_NAMES, SAMPLE_IDS
from runtime.work_orders import WorkOrderIssuer

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "console"
STATIC_FILES = {
    "/": CONSOLE / "index.html",
    "/index.html": CONSOLE / "index.html",
    "/config": CONSOLE / "config.html",
    "/config.html": CONSOLE / "config.html",
    "/styles.css": CONSOLE / "styles.css",
    "/dashboard.js": CONSOLE / "dashboard.js",
    "/config.js": CONSOLE / "config.js",
}


class UpstreamError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(
        url,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("error", detail)
        except json.JSONDecodeError:
            message = detail
        raise UpstreamError(exc.code, str(message)) from exc
    except (TimeoutError, URLError) as exc:
        raise UpstreamError(HTTPStatus.BAD_GATEWAY, str(exc)) from exc
    if not isinstance(value, dict):
        raise UpstreamError(HTTPStatus.BAD_GATEWAY, "upstream returned non-object JSON")
    return value


class DashboardBackend:
    def __init__(
        self,
        *,
        orchestrator_url: str,
        guard_url: str,
        runtime_url: str,
        work_order_issuer: WorkOrderIssuer | None = None,
    ) -> None:
        self.orchestrator_url = orchestrator_url.rstrip("/")
        self.guard_url = guard_url.rstrip("/")
        self.runtime_url = runtime_url.rstrip("/")
        self.work_order_issuer = work_order_issuer
        self._last_physical: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        line = json_request(f"{self.orchestrator_url}/v1/line/state", timeout=3)
        busy = any(
            task.get("status") in {"SUBMITTED", "EXECUTING"}
            for task in line.get("tasks", [])
            if isinstance(task, dict)
        )
        physical: dict[str, Any] | None = self._last_physical
        physical_status = "cached" if physical is not None else "unavailable"
        confirmed = line.get("physical_evidence")
        if isinstance(confirmed, dict):
            physical = self._merge_physical(physical, confirmed)
            self._last_physical = physical
            physical_status = "confirmed"
        if not busy:
            try:
                response = json_request(f"{self.guard_url}/v1/physical", timeout=3)
                value = response.get("physical")
                if isinstance(value, dict):
                    physical = self._merge_physical(physical, value)
                    self._last_physical = physical
                    physical_status = "live"
                else:
                    physical_status = str(response.get("status", "unavailable"))
            except UpstreamError:
                if physical is None:
                    physical_status = "unavailable"
        return {
            "schema_version": "safeexec.dashboard.v3",
            "line": line,
            "physical": physical,
            "physical_status": physical_status,
        }

    @staticmethod
    def _merge_physical(
        base: dict[str, Any] | None,
        update: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base or {})
        merged.update(
            {
                key: value
                for key, value in update.items()
                if key != "sample_locations"
            }
        )
        locations: dict[str, Any] = {}
        if isinstance((base or {}).get("sample_locations"), dict):
            locations.update((base or {})["sample_locations"])
        if isinstance(update.get("sample_locations"), dict):
            locations.update(update["sample_locations"])
        if locations:
            merged["sample_locations"] = locations
        return merged

    def control(self, action: str) -> dict[str, Any]:
        if action not in {"start", "pause", "resume", "reset"}:
            raise UpstreamError(HTTPStatus.NOT_FOUND, "unknown control")
        return json_request(
            f"{self.orchestrator_url}/v1/control/{action}",
            method="POST",
            payload={},
            timeout=160 if action == "reset" else 5,
        )

    def inject(self, value: dict[str, Any]) -> dict[str, Any]:
        return json_request(
            f"{self.orchestrator_url}/v1/testing/injections",
            method="POST",
            payload=value,
            timeout=5,
        )

    def submit_operator_command(self, value: dict[str, Any]) -> dict[str, Any]:
        return json_request(
            f"{self.orchestrator_url}/v1/agent/commands",
            method="POST",
            payload=value,
            timeout=65,
        )

    def events(self, after: int) -> dict[str, Any]:
        return json_request(
            f"{self.orchestrator_url}/v1/events?after={after}",
            timeout=5,
        )

    def set_execution_mode(
        self,
        mode: str,
        provided_token: str,
    ) -> dict[str, Any]:
        return json_request(
            f"{self.orchestrator_url}/v1/control/mode",
            method="POST",
            payload={"mode": mode},
            headers={"X-Unsafe-Demo-Token": provided_token},
            timeout=5,
        )

    def configuration(self) -> dict[str, Any]:
        runtime = json_request(f"{self.runtime_url}/v1/config", timeout=3)
        orders = json_request(f"{self.runtime_url}/v1/work-orders", timeout=3)
        line = json_request(f"{self.orchestrator_url}/v1/line/state", timeout=3)
        issuer = None
        if self.work_order_issuer is not None:
            issuer = {
                "issuer_id": self.work_order_issuer.issuer_id,
                "key_id": self.work_order_issuer.key_id,
                "status": "ready",
            }
        return {
            "schema_version": "safeexec.control-plane.v1",
            "runtime": runtime,
            "work_orders": orders.get("work_orders", []),
            "active_work_order_id": line.get("active_work_order_id"),
            "line": line,
            "issuer": issuer,
        }

    def issue_work_order(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.work_order_issuer is None:
            raise UpstreamError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "control-plane signing key is not configured",
            )
        required = {
            "schema_version",
            "sample_ids",
            "source",
            "destination",
            "subject_principal_id",
            "valid_for_ms",
            "operator_note",
        }
        if set(value) != required:
            raise ValueError(
                "invalid WorkOrder draft fields; "
                f"missing={sorted(required - set(value))}, "
                f"extra={sorted(set(value) - required)}"
            )
        if value["schema_version"] != "safeexec.work-order-draft.v1":
            raise ValueError("unsupported WorkOrder draft schema")
        sample_ids = value["sample_ids"]
        if (
            not isinstance(sample_ids, list)
            or not sample_ids
            or len(sample_ids) != len(set(sample_ids))
            or any(sample_id not in SAMPLE_IDS for sample_id in sample_ids)
        ):
            raise ValueError("sample_ids must be a unique non-empty sample list")
        source = value["source"]
        destination = value["destination"]
        if source not in LOCATION_NAMES or destination not in LOCATION_NAMES:
            raise ValueError("unknown WorkOrder route")
        if source == destination:
            raise ValueError("WorkOrder source and destination must differ")
        principal = value["subject_principal_id"]
        if not isinstance(principal, str) or not principal.strip():
            raise ValueError("subject_principal_id must be non-empty")
        valid_for_ms = value["valid_for_ms"]
        if isinstance(valid_for_ms, bool) or not isinstance(valid_for_ms, int):
            raise ValueError("valid_for_ms must be an integer")
        note = value["operator_note"]
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("operator_note must contain at most 500 characters")

        grants = [
            {
                "grant_id": f"work-order-{sample_id.lower()}-analysis",
                "action": "lab.sample.transfer",
                "resource": {"type": "lab.sample", "id": sample_id},
                "arguments": {
                    "source": source,
                    "destination": destination,
                },
                "required_facts": [
                    {
                        "key": "camera.healthy",
                        "equals": True,
                        "max_age_ms": 1500,
                    }
                ],
                "max_executions": 1,
            }
            for sample_id in sample_ids
        ]
        order = self.work_order_issuer.issue(
            subject_principal_id=principal,
            grants=grants,
            valid_for_ms=valid_for_ms,
            operator_note=note.strip(),
        )
        registered = json_request(
            f"{self.runtime_url}/v1/work-orders",
            method="POST",
            payload=order.to_dict(),
            timeout=5,
        )
        activated = json_request(
            f"{self.orchestrator_url}/v1/work-orders/activate",
            method="POST",
            payload={"work_order_id": order.work_order_id},
            timeout=5,
        )
        return {
            "status": "issued-and-activated",
            "work_order": registered.get("work_order"),
            "line": activated.get("line"),
        }


class DashboardServer(ThreadingHTTPServer):
    backend: DashboardBackend

    def handle_error(self, request: object, client_address: object) -> None:
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "SafeExecDashboard/2.0"

    @property
    def backend(self) -> DashboardBackend:
        return self.server.backend  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in STATIC_FILES:
                self._static(STATIC_FILES[parsed.path])
            elif parsed.path == "/healthz":
                self._json({"status": "ok"})
            elif parsed.path == "/api/dashboard/v2":
                self._json(self.backend.snapshot())
            elif parsed.path == "/api/config":
                self._json(self.backend.configuration())
            elif parsed.path == "/api/events":
                self._json(self.backend.events(self._after(parsed.query)))
            elif parsed.path == "/api/events/stream":
                self._proxy_stream(self._after(parsed.query))
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except UpstreamError as exc:
            self._error(exc.status, str(exc))
        except (TypeError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/control/mode":
                value = self._body()
                if set(value) != {"mode"} or not isinstance(value["mode"], str):
                    raise ValueError("mode endpoint expects exactly one string field")
                self._json(
                    self.backend.set_execution_mode(
                        value["mode"],
                        self.headers.get("X-Unsafe-Demo-Token", "")
                    )
                )
            elif parsed.path.startswith("/api/control/"):
                self._require_empty(self._body())
                action = parsed.path.rsplit("/", 1)[-1]
                self._json(self.backend.control(action))
            elif parsed.path == "/api/testing/injections":
                self._json(self.backend.inject(self._body()), HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/agent/commands":
                self._json(
                    self.backend.submit_operator_command(self._body()),
                    HTTPStatus.CREATED,
                )
            elif parsed.path == "/api/work-orders":
                self._json(
                    self.backend.issue_work_order(self._body()),
                    HTTPStatus.CREATED,
                )
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except UpstreamError as exc:
            self._error(exc.status, str(exc))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def _proxy_stream(self, after: int) -> None:
        request = Request(
            f"{self.backend.orchestrator_url}/v1/events/stream?after={after}",
            headers={"Accept": "text/event-stream"},
        )
        try:
            upstream = urlopen(request, timeout=30)
        except (HTTPError, URLError) as exc:
            raise UpstreamError(HTTPStatus.BAD_GATEWAY, str(exc)) from exc
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                line = upstream.readline()
                if not line:
                    return
                self.wfile.write(line)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        finally:
            upstream.close()

    @staticmethod
    def _after(query: str) -> int:
        value = int(parse_qs(query).get("after", ["0"])[0])
        if value < 0:
            raise ValueError("after must be non-negative")
        return value

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("request body must be an object")
        return value

    @staticmethod
    def _require_empty(value: dict[str, Any]) -> None:
        if value:
            raise ValueError("endpoint expects an empty object")

    def _static(self, path: Path) -> None:
        payload = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, value: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--orchestrator-url",
        default=os.environ.get("SAFEEXEC_ORCHESTRATOR_URL", "http://127.0.0.1:8789"),
    )
    parser.add_argument(
        "--runtime-url",
        default=os.environ.get("SAFEEXEC_RUNTIME_URL", "http://127.0.0.1:8790"),
    )
    parser.add_argument(
        "--guard-url",
        default=os.environ.get("SAFEEXEC_GUARD_URL", "http://127.0.0.1:8788"),
    )
    parser.add_argument(
        "--work-order-key",
        default=os.environ.get("SAFEEXEC_WORK_ORDER_PRIVATE_KEY", ""),
    )
    parser.add_argument(
        "--work-order-issuer-id",
        default=os.environ.get(
            "SAFEEXEC_WORK_ORDER_ISSUER_ID",
            "biolab-control-plane",
        ),
    )
    args = parser.parse_args()

    issuer = None
    if args.work_order_key:
        key_path = Path(args.work_order_key)
        issuer = WorkOrderIssuer(
            key_path.read_text(encoding="utf-8").strip(),
            issuer_id=args.work_order_issuer_id,
        )
    server = DashboardServer((args.host, args.port), Handler)
    server.backend = DashboardBackend(
        orchestrator_url=args.orchestrator_url,
        runtime_url=args.runtime_url,
        guard_url=args.guard_url,
        work_order_issuer=issuer,
    )
    print(f"SafeExec Dashboard listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
