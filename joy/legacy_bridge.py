"""Explicitly unsafe HTTP bridge used only for the Legacy comparison."""

from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from typing import Any

from .driver import JoyDriver
from .safeexec_adapter import JoyExecutor


MAX_BODY_BYTES = 64 * 1024


class LegacyBridge:
    def __init__(self, executor: JoyExecutor, token: str) -> None:
        self.executor = executor
        self.token = token

    def create_handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "SafeExecLegacyDemo/1"

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/healthz":
                    self._json(200, {"ok": True, "mode": "UNSAFE_LEGACY_DEMO"})
                    return
                if self.path == "/lab/v1/inventory":
                    self._json(200, bridge.executor.driver.get_inventory())
                    return
                self._json(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/legacy/v1/execute":
                    self._json(404, {"error": "not_found"})
                    return
                provided = self.headers.get("X-Legacy-Demo-Token", "")
                if not hmac.compare_digest(provided, bridge.token):
                    self._json(401, {"error": "invalid_demo_token"})
                    return
                try:
                    payload = self._read_json()
                    result = bridge.executor.execute(payload)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    self._json(400, {"error": "invalid_request", "detail": str(exc)})
                    return
                status = 200 if result.get("state") == "succeeded" else 500
                self._json(status, result)

            def _read_json(self) -> dict[str, Any]:
                raw_length = self.headers.get("Content-Length")
                if raw_length is None:
                    raise ValueError("Content-Length is required")
                length = int(raw_length)
                if length < 1 or length > MAX_BODY_BYTES:
                    raise ValueError("request body size is invalid")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("request body must be an object")
                return value

            def _json(self, status: int, value: dict[str, Any]) -> None:
                body = json.dumps(value, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                del format, args

        return Handler


def main() -> int:
    if os.environ.get("ALLOW_UNSAFE_LEGACY_DEMO") != "1":
        raise SystemExit(
            "Legacy bridge is disabled; set ALLOW_UNSAFE_LEGACY_DEMO=1 explicitly"
        )
    token = os.environ.get("LAB_LEGACY_TOKEN", "")
    if not token:
        raise SystemExit("LAB_LEGACY_TOKEN must be configured")
    host = os.environ.get("LAB_LEGACY_BIND", "127.0.0.1")
    port = int(os.environ.get("LAB_LEGACY_PORT", "8791"))

    driver = JoyDriver().connect()
    bridge = LegacyBridge(JoyExecutor(driver), token)
    server = ThreadingHTTPServer((host, port), bridge.create_handler())
    print(
        f"UNSAFE Legacy demo bridge listening on http://{host}:{port}",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        driver.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
