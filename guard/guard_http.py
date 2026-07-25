"""
SafeExec V1 Guard HTTP Server
提供 REST API:
- POST /v1/execute - 验证 Lease 后执行
- GET /v1/events - Guard 审计事件
- GET /v1/physical - JOY 实时物理状态
- GET /healthz - 健康检查
"""
from __future__ import annotations

import json
import argparse
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .verifier import LeaseVerifier
from .executor import BaseExecutor, FakeExecutor


class SafeExecGuard:
    """
    SafeExec Guard 主类
    验证 Lease 后调用 Executor 执行
    """

    def __init__(
        self,
        public_key_b64: str,
        executor: BaseExecutor,
        expected_audience: str = "joy-guard-01",
    ):
        self.verifier = LeaseVerifier(
            public_key_b64=public_key_b64,
            expected_audience=expected_audience,
        )
        self.executor = executor
        self._lock = threading.RLock()
        self._executor_lock = threading.Lock()
        self._readiness_probe: threading.Thread | None = None
        self._readiness_cache = {
            "schema_version": "safeexec.guard-readiness.v1",
            "status": "probing",
            "ready": False,
            "guard_id": "joy-guard-01",
            "expected_audience": self.verifier.expected_audience,
            "executor": {"type": "joy", "ready": False},
            "error": "initial JOY readiness probe pending",
            "checked_at_ms": None,
            "auto_execute": False,
        }
        self._physical_cache: dict | None = None

        # 审计日志
        self._events = []

    def execute(self, intent: dict, lease: dict) -> dict:
        """
        验证 Lease 并执行

        流程:
        1. 验证 Lease
        2. 如果验证失败，不调用 Executor
        3. 如果验证成功，调用 Executor
        4. 返回 ExecutionReceipt
        """
        # 1. 验证
        is_valid, reason, verify_receipt = self.verifier.verify_and_consume(intent, lease)

        with self._lock:
            if not is_valid:
                # 记录阻断事件
                self._events.append({
                    "seq": len(self._events) + 1,
                    "event": "guard.blocked",
                    "timestamp": self._get_time(),
                    "request_id": intent.get("request_id"),
                    "lease_id": lease.get("lease_id"),
                    "reason": reason,
                    "severity": "critical",
                })

                return {
                    "status": "blocked",
                    "reason_code": reason,
                    "verify_receipt": verify_receipt,
                }

            # 2. 验证通过，记录事件
            self._events.append({
                "seq": len(self._events) + 1,
                "event": "guard.accepted",
                "timestamp": self._get_time(),
                "request_id": intent.get("request_id"),
                "lease_id": lease.get("lease_id"),
                "severity": "info",
            })

        # 3. 调用 Executor
        try:
            with self._executor_lock:
                exec_receipt = self.executor.execute(intent)

            with self._lock:
                self._events.append({
                    "seq": len(self._events) + 1,
                    "event": "executor.completed",
                    "timestamp": self._get_time(),
                    "request_id": intent.get("request_id"),
                    "status": exec_receipt.get("status"),
                    "severity": "info",
                })

            result = {
                "status": "executed",
                "lease_id": lease.get("lease_id"),
                "execution": exec_receipt,
            }
            self._schedule_readiness_probe()
            return result

        except Exception as e:
            with self._lock:
                self._events.append({
                    "seq": len(self._events) + 1,
                    "event": "executor.failed",
                    "timestamp": self._get_time(),
                    "request_id": intent.get("request_id"),
                    "error": str(e),
                    "severity": "error",
                })

            return {
                "status": "failed",
                "lease_id": lease.get("lease_id"),
                "error": str(e),
            }

    def get_events(self) -> list:
        with self._lock:
            return list(self._events)

    def get_stats(self) -> dict:
        return self.verifier.get_stats()

    def get_physical_state(self) -> dict:
        self._schedule_readiness_probe()
        with self._lock:
            physical = (
                json.loads(json.dumps(self._physical_cache))
                if self._physical_cache is not None
                else None
            )
            error = self._readiness_cache.get("error")
        return {
            "status": "ok" if physical is not None else "unavailable",
            "physical": physical,
            **({"error": error} if physical is None and error else {}),
        }

    def readiness(self) -> dict:
        """Return cached deep readiness and keep exactly one probe in flight."""
        self._schedule_readiness_probe()
        with self._lock:
            value = json.loads(json.dumps(self._readiness_cache))
        checked_at = value.get("checked_at_ms")
        if (
            value.get("ready")
            and isinstance(checked_at, int)
            and int(time.time() * 1000) - checked_at > 15_000
        ):
            value["status"] = "probing"
            value["ready"] = False
            value["error"] = "JOY readiness evidence is stale"
        return value

    def _schedule_readiness_probe(self) -> None:
        with self._lock:
            checked_at = self._readiness_cache.get("checked_at_ms")
            fresh = (
                isinstance(checked_at, int)
                and int(time.time() * 1000) - checked_at < 5_000
            )
            if fresh or (
                self._readiness_probe is not None
                and self._readiness_probe.is_alive()
            ):
                return
            self._readiness_probe = threading.Thread(
                target=self._probe_readiness,
                name="safeexec-joy-readiness",
                daemon=True,
            )
            self._readiness_probe.start()

    def _probe_readiness(self) -> None:
        """Probe JOY off the HTTP path so slow RPC cannot block Guard health."""
        try:
            with self._executor_lock:
                physical = self.executor.physical_state()
            is_fake = isinstance(self.executor, FakeExecutor)
            if physical is None and not is_fake:
                raise RuntimeError("executor returned no physical state")
            if isinstance(physical, dict) and physical.get("ok") is False:
                raise RuntimeError(str(physical.get("error") or "JOY RPC not ready"))
            value = {
                "schema_version": "safeexec.guard-readiness.v1",
                "status": "ready",
                "ready": True,
                "guard_id": "joy-guard-01",
                "expected_audience": self.verifier.expected_audience,
                "executor": {
                    "type": "fake" if is_fake else "joy",
                    "ready": True,
                    "arm_state": (
                        physical.get("arm_state")
                        if isinstance(physical, dict)
                        else None
                    ),
                    "platform_state": (
                        physical.get("platform_state")
                        if isinstance(physical, dict)
                        else None
                    ),
                },
                "checked_at_ms": int(time.time() * 1000),
                "auto_execute": False,
            }
            with self._lock:
                self._readiness_cache = value
                self._physical_cache = (
                    json.loads(json.dumps(physical))
                    if isinstance(physical, dict)
                    else None
                )
        except Exception as exc:
            value = {
                "schema_version": "safeexec.guard-readiness.v1",
                "status": "unavailable",
                "ready": False,
                "guard_id": "joy-guard-01",
                "expected_audience": self.verifier.expected_audience,
                "executor": {"type": "joy", "ready": False},
                "error": str(exc),
                "checked_at_ms": int(time.time() * 1000),
                "auto_execute": False,
            }
            with self._lock:
                self._readiness_cache = value
                self._physical_cache = None

    @staticmethod
    def _get_time() -> float:
        import time
        return time.time()


# ============================================================
# HTTP Handler
# ============================================================

_guard: SafeExecGuard = None


class Handler(BaseHTTPRequestHandler):
    server_version = "SafeExecGuard/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/healthz":
            self._json({"status": "ok"})
            return

        if parsed.path == "/readyz":
            value = _guard.readiness()
            self._json(
                value,
                HTTPStatus.OK if value["ready"] else HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return

        if parsed.path == "/v1/stats":
            self._json(_guard.get_stats())
            return

        if parsed.path == "/v1/events":
            self._json({"events": _guard.get_events()})
            return

        if parsed.path == "/v1/physical":
            self._json(_guard.get_physical_state())
            return

        self._error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))

        try:
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            self._error(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {e}")
            return

        if parsed.path == "/v1/execute":
            intent = data.get("intent", {})
            lease = data.get("lease", {})

            if not intent:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'intent'")
                return

            if not lease:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'lease'")
                return

            result = _guard.execute(intent, lease)
            self._json(result)
            return

        self._error(HTTPStatus.NOT_FOUND, "Not found")

    def _json(self, data: dict, status: int = HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _error(self, status: HTTPStatus, message: str):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="SafeExec Guard")
    parser.add_argument("--key", required=True, help="Path to public key (base64)")
    parser.add_argument("--executor", default="fake", choices=["fake", "joy"])
    parser.add_argument("--audience", default="joy-guard-01")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--joy-host", default="127.0.0.1")
    parser.add_argument("--joy-port", type=int, default=18189)
    parser.add_argument("--joy-timeout", type=float, default=120.0)

    args = parser.parse_args()

    # 加载公钥
    with open(args.key) as f:
        public_key_b64 = f.read().strip()

    # 创建 Executor
    if args.executor == "fake":
        executor = FakeExecutor()
        print("[Guard] Using FakeExecutor (no real robot)")
    else:
        from .executor import JoyExecutor
        executor = JoyExecutor(
            host=args.joy_host,
            port=args.joy_port,
            timeout=args.joy_timeout,
        )
        print("[Guard] Using JoyExecutor (real robot)")

    global _guard
    _guard = SafeExecGuard(
        public_key_b64=public_key_b64,
        executor=executor,
        expected_audience=args.audience,
    )

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SafeExec Guard listening on {args.host}:{args.port}")
    print(f"[Guard] Executor: {args.executor}")
    server.serve_forever()


if __name__ == "__main__":
    main()
