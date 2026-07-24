#!/usr/bin/env python3
"""SafeExec dashboard aggregator backed by the real Runtime, Guard and JOY."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lab_agent.contracts import build_action_intent, plan_fingerprint
from lab_agent.scenarios import get_scenario


CONSOLE = ROOT / "console"


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 130.0,
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    data = None
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            detail = {"error": body}
        return {"_http_error": exc.code, **detail}
    except (TimeoutError, URLError, OSError) as exc:
        return {"_connection_error": str(exc)}
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        return {"_protocol_error": "non-JSON response"}
    return result if isinstance(result, dict) else {"_protocol_error": "non-object JSON"}


def _event_message(event: dict[str, Any]) -> str:
    name = str(event.get("event", "unknown"))
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if name == "intent.received":
        return f"Agent 提交动作 {payload.get('action', '')}".strip()
    if name == "policy.denied":
        return f"Runtime 拒绝：{payload.get('reason_code', 'UNKNOWN')}"
    if name == "policy.allowed":
        return f"Runtime 放行：{payload.get('matched_grant_id', 'GRANT_MATCHED')}"
    if name == "lease.issued":
        return f"签发一次性 Lease：{payload.get('lease_id', '—')}"
    if name == "guard.response":
        return f"Guard 返回：{payload.get('status', 'unknown')}"
    if name == "guard.error":
        return f"Guard 调用失败：{payload.get('error', 'unknown')}"
    if name == "guard.blocked":
        return f"Guard 阻断：{event.get('reason', 'VERIFY_FAILED')}"
    if name == "guard.accepted":
        return "Guard 验签、防重放检查通过"
    if name == "executor.completed":
        return f"JOY 执行完成：{event.get('status', 'unknown')}"
    if name == "executor.failed":
        return f"JOY 执行失败：{event.get('error', 'unknown')}"
    if name == "fact.updated":
        return f"物理事实更新：{payload.get('key', '')}".strip("：")
    return name


def _event_status(event: dict[str, Any]) -> str:
    name = str(event.get("event", ""))
    severity = str(event.get("severity", "info"))
    if name in {"policy.denied", "guard.blocked"}:
        return "blocked"
    if name in {"guard.accepted", "executor.completed", "policy.allowed"}:
        return "safe"
    if severity in {"critical", "error", "warning"}:
        return "warning"
    return "info"


class DashboardState:
    def __init__(
        self,
        *,
        runtime_url: str,
        guard_url: str,
        legacy_url: str,
        legacy_token: str,
        enable_unsafe_demo: bool,
    ) -> None:
        self.runtime_url = runtime_url.rstrip("/")
        self.guard_url = guard_url.rstrip("/")
        self.legacy_url = legacy_url.rstrip("/")
        self.legacy_token = legacy_token
        self.enable_unsafe_demo = enable_unsafe_demo
        self._lock = threading.RLock()
        self._running = False
        self._scenario = "prompt-injection"
        self._mode = "protected"
        self._run_id: str | None = None
        self._fingerprint: str | None = None
        self._started_at_ms: int | None = None
        self._last_error: str | None = None
        self._last_physical: dict[str, Any] | None = None
        self._comparison = {
            "unprotected": {"result": "not_run", "sample_A_final": "—"},
            "protected": {"result": "not_run", "sample_A_final": "—"},
        }

    def start(self, scenario_name: str) -> None:
        if scenario_name not in {"prompt-injection", "legitimate", "baseline"}:
            raise ValueError(f"unknown scenario: {scenario_name!r}")
        with self._lock:
            if self._running:
                raise RuntimeError("another demonstration is still running")
            self._running = True
            self._scenario = scenario_name
            self._mode = "unprotected" if scenario_name == "baseline" else "protected"
            self._last_error = None
            self._started_at_ms = int(time.time() * 1000)
        threading.Thread(
            target=self._run,
            args=(scenario_name,),
            name=f"dashboard-{scenario_name}",
            daemon=True,
        ).start()

    def _run(self, scenario_name: str) -> None:
        scenario = get_scenario(
            "normal" if scenario_name == "legitimate" else "prompt-injection"
        )
        intent = build_action_intent(scenario.replay_plan)
        fingerprint = plan_fingerprint(scenario.replay_plan)
        with self._lock:
            self._run_id = intent["request_id"]
            self._fingerprint = fingerprint

        if scenario_name == "baseline":
            if not self.enable_unsafe_demo or not self.legacy_token:
                result = {
                    "state": "disabled",
                    "error": (
                        "无保护演示默认关闭；启动 Dashboard 时需显式提供 "
                        "--enable-unsafe-demo 和 LAB_LEGACY_TOKEN"
                    ),
                }
            else:
                result = _json_request(
                    f"{self.legacy_url}/legacy/v1/execute",
                    method="POST",
                    payload=intent,
                    headers={"X-Legacy-Demo-Token": self.legacy_token},
                )
        else:
            result = _json_request(
                f"{self.runtime_url}/v1/actions",
                method="POST",
                payload=intent,
            )

        physical_response = _json_request(f"{self.guard_url}/v1/physical", timeout=5)
        physical = physical_response.get("physical")
        if isinstance(physical, dict):
            with self._lock:
                self._last_physical = physical
        sample_a = (
            physical.get("sample_locations", {}).get("sample-A", "—")
            if isinstance(physical, dict)
            else "—"
        )
        error = (
            result.get("error")
            or result.get("message")
            or result.get("_connection_error")
            or result.get("_protocol_error")
        )
        if scenario_name == "baseline":
            state = str(result.get("state") or result.get("status") or "failed")
            comparison_result = (
                "unsafe_executed" if state in {"succeeded", "executed", "ok"} else state
            )
            key = "unprotected"
        else:
            state = self._protected_result_state(result)
            comparison_result = state
            key = "protected"
        with self._lock:
            self._comparison[key] = {
                "result": comparison_result,
                "sample_A_final": sample_a,
            }
            self._last_error = str(error) if error else None
            self._running = False

    @staticmethod
    def _protected_result_state(result: dict[str, Any]) -> str:
        if result.get("status") == "denied":
            return "blocked"
        guard = result.get("guard_response")
        if isinstance(guard, dict):
            if guard.get("status") == "executed":
                return "executed"
            if guard.get("status") == "blocked":
                return "blocked"
            if guard.get("status") in {"failed", "error"}:
                return "failed"
        return "failed" if any(str(key).startswith("_") for key in result) else "pending"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            scenario_name = self._scenario
            mode = self._mode
            running = self._running
            run_id = self._run_id
            fingerprint = self._fingerprint
            started_at_ms = self._started_at_ms
            last_error = self._last_error
            comparison = json.loads(json.dumps(self._comparison))
            cached_physical = (
                json.loads(json.dumps(self._last_physical))
                if self._last_physical is not None
                else None
            )

        runtime_state = _json_request(f"{self.runtime_url}/v1/state", timeout=2)
        runtime_events = _json_request(f"{self.runtime_url}/v1/events", timeout=2)
        if running:
            # Guard intentionally serializes execution. Polling it here would
            # contend with the active action and freeze the dashboard.
            guard_events = {"events": []}
            physical_response = {
                "status": "busy",
                "physical": cached_physical,
            }
        else:
            guard_events = _json_request(f"{self.guard_url}/v1/events", timeout=2)
            physical_response = _json_request(
                f"{self.guard_url}/v1/physical", timeout=2
            )
            fresh_physical = physical_response.get("physical")
            if isinstance(fresh_physical, dict):
                with self._lock:
                    self._last_physical = fresh_physical

        latest = (
            runtime_state.get("latest_action")
            if isinstance(runtime_state.get("latest_action"), dict)
            else {}
        )
        latest_intent = (
            latest.get("intent") if isinstance(latest.get("intent"), dict) else {}
        )
        if not run_id and mode == "protected" and latest_intent.get("request_id"):
            run_id = str(latest_intent["request_id"])
            started_at_ms = latest_intent.get("issued_at_ms")
            latest_arguments = latest_intent.get("arguments")
            if isinstance(latest_arguments, dict):
                scenario_name = (
                    "prompt-injection"
                    if latest_arguments.get("destination") == "waste-bin"
                    else "legitimate"
                )
        if mode == "unprotected" or (
            run_id
            and latest_intent.get("request_id")
            and latest_intent.get("request_id") != run_id
        ):
            latest = {}
            latest_intent = {}
        scenario = get_scenario(
            "normal" if scenario_name == "legitimate" else "prompt-injection"
        )
        intent = latest_intent
        plan = scenario.replay_plan
        resource = intent.get("resource") if isinstance(intent.get("resource"), dict) else {}
        arguments = (
            intent.get("arguments") if isinstance(intent.get("arguments"), dict) else {}
        )
        decision = (
            latest.get("decision") if isinstance(latest.get("decision"), dict) else {}
        )
        lease = latest.get("lease") if isinstance(latest.get("lease"), dict) else {}
        guard_response = (
            latest.get("guard_response")
            if isinstance(latest.get("guard_response"), dict)
            else {}
        )
        physical = (
            physical_response.get("physical")
            if isinstance(physical_response.get("physical"), dict)
            else None
        )

        result = self._result(
            running=running,
            mode=mode,
            decision=decision,
            guard_response=guard_response,
            physical=physical,
            last_error=last_error,
        )
        if latest and comparison["protected"]["result"] == "not_run":
            comparison["protected"] = {
                "result": result["state"],
                "sample_A_final": (
                    physical.get("sample_locations", {}).get("sample-A", "—")
                    if isinstance(physical, dict)
                    else "—"
                ),
            }
        guard_status = str(guard_response.get("status", ""))
        timeline = self._timeline(
            runtime_events.get("events", []),
            guard_events.get("events", []),
            run_id,
        )
        physical_view = physical or {
            "arm_state": "UNAVAILABLE",
            "platform_state": "UNAVAILABLE",
            "current_dock": "—",
            "sample_locations": {},
            "unsafe_outcome": None,
        }
        return {
            "schema_version": "safeexec.dashboard.v1",
            "run_id": run_id,
            "mode": mode,
            "scenario": scenario_name,
            "running": running,
            "started_at_ms": started_at_ms,
            "input": {
                "operator_task": scenario.operator_task,
                "untrusted_content": scenario.untrusted_record,
            },
            "agent": {
                "principal_id": intent.get("principal_id", "lab-agent-01"),
                "compromised": scenario_name in {"prompt-injection", "baseline"},
                "requested_action": intent.get("action", "lab.sample.transfer"),
                "sample_id": resource.get("id", plan.sample_id),
                "source": arguments.get("source", plan.source),
                "destination": arguments.get("destination", plan.destination),
                "semantic_fingerprint": fingerprint or plan_fingerprint(plan),
            },
            "runtime": {
                "status": latest.get("status", "pending"),
                "effect": decision.get("effect", "unknown"),
                "reason_code": decision.get("reason_code"),
                "matched_grant_id": decision.get("matched_grant_id"),
            },
            "lease": {
                "issued": bool(lease.get("lease_id")),
                "lease_id": lease.get("lease_id"),
                "expires_at_ms": lease.get("expires_at_ms"),
            },
            "guard": {
                "reached": bool(guard_response),
                "verification": (
                    "passed"
                    if guard_status == "executed"
                    else "blocked"
                    if guard_status == "blocked"
                    else "not_requested"
                ),
                "executor_called": guard_status in {"executed", "failed"},
            },
            "physical": physical_view,
            "result": result,
            "timeline": timeline,
            "comparison": comparison,
            "connectivity": {
                "runtime": "disconnected"
                if "_connection_error" in runtime_state
                else "connected",
                "guard": "disconnected"
                if "_connection_error" in physical_response
                else "connected",
            },
        }

    @staticmethod
    def _result(
        *,
        running: bool,
        mode: str,
        decision: dict[str, Any],
        guard_response: dict[str, Any],
        physical: dict[str, Any] | None,
        last_error: str | None,
    ) -> dict[str, str]:
        if running:
            return {
                "state": "pending",
                "title": "真实链路执行中",
                "detail": "正在等待 Runtime、Guard 与 JOY 返回",
            }
        if last_error:
            return {"state": "failed", "title": "联动失败", "detail": last_error}
        if mode == "unprotected":
            unsafe = bool(physical and physical.get("unsafe_outcome"))
            return {
                "state": "executed" if unsafe else "pending",
                "title": "无保护动作已直接送达执行器" if unsafe else "等待无保护演示",
                "detail": "该路径仅用于显式启用的红队对照演示",
            }
        if decision.get("effect") == "deny":
            return {
                "state": "blocked",
                "title": "恶意物理动作已阻断",
                "detail": f"Runtime 拒绝：{decision.get('reason_code', 'POLICY_DENIED')}",
            }
        if guard_response.get("status") == "executed":
            unsafe = bool(physical and physical.get("unsafe_outcome"))
            return {
                "state": "executed" if unsafe else "allowed",
                "title": "合法动作已执行" if not unsafe else "危险动作已执行",
                "detail": "Guard 验证 Lease 后调用了 JOY 执行器",
            }
        if guard_response.get("status") == "blocked":
            return {
                "state": "blocked",
                "title": "Guard 已阻断请求",
                "detail": str(guard_response.get("reason_code", "LEASE_VERIFY_FAILED")),
            }
        return {
            "state": "pending",
            "title": "等待 Agent 请求",
            "detail": "Dashboard 已连接真实安全链路",
        }

    @staticmethod
    def _timeline(
        runtime_events: Any, guard_events: Any, run_id: str | None
    ) -> list[dict[str, Any]]:
        combined: list[dict[str, Any]] = []
        for event in runtime_events if isinstance(runtime_events, list) else []:
            payload = event.get("payload")
            event_request_id = (
                payload.get("request_id") if isinstance(payload, dict) else None
            )
            if run_id and event_request_id and event_request_id != run_id:
                continue
            combined.append(
                {
                    "at_ms": int(float(event.get("timestamp", 0)) * 1000),
                    "source": str(event.get("source", "runtime")),
                    "event": event.get("event"),
                    "status": _event_status(event),
                    "message": _event_message(event),
                }
            )
        for event in guard_events if isinstance(guard_events, list) else []:
            if run_id and event.get("request_id") not in {None, run_id}:
                continue
            combined.append(
                {
                    "at_ms": int(float(event.get("timestamp", 0)) * 1000),
                    "source": "joy"
                    if str(event.get("event", "")).startswith("executor.")
                    else "guard",
                    "event": event.get("event"),
                    "status": _event_status(event),
                    "message": _event_message(event),
                }
            )
        combined.sort(key=lambda item: item["at_ms"])
        return combined[-20:]


STATE: DashboardState


class Handler(BaseHTTPRequestHandler):
    server_version = "SafeExecDashboard/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json({"status": "ok"})
            return
        if parsed.path == "/api/dashboard/v1":
            self._json(STATE.snapshot())
            return
        if parsed.path in {"/", "/index.html"}:
            self._file(CONSOLE / "index.html")
            return
        requested = (CONSOLE / parsed.path.lstrip("/")).resolve()
        if CONSOLE in requested.parents and requested.is_file():
            self._file(requested)
            return
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/dashboard/scenario":
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            data = self._read_json()
            STATE.start(str(data.get("scenario", "")))
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
            return
        self._json({"accepted": True}, HTTPStatus.ACCEPTED)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )

    def log_message(self, format: str, *args: object) -> None:
        if not self.path.startswith("/api/dashboard/v1"):
            print(f"[dashboard] {self.address_string()} {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--runtime-url",
        default=os.environ.get("SAFEEXEC_RUNTIME_URL", "http://127.0.0.1:8790"),
    )
    parser.add_argument(
        "--guard-url",
        default=os.environ.get("SAFEEXEC_GUARD_URL", "http://127.0.0.1:8788"),
    )
    parser.add_argument(
        "--legacy-url",
        default=os.environ.get("LAB_LEGACY_URL", "http://127.0.0.1:8791"),
    )
    parser.add_argument(
        "--legacy-token",
        default=os.environ.get("LAB_LEGACY_TOKEN", ""),
    )
    parser.add_argument("--enable-unsafe-demo", action="store_true")
    args = parser.parse_args()

    global STATE
    STATE = DashboardState(
        runtime_url=args.runtime_url,
        guard_url=args.guard_url,
        legacy_url=args.legacy_url,
        legacy_token=args.legacy_token,
        enable_unsafe_demo=args.enable_unsafe_demo,
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SafeExec dashboard: http://{args.host}:{args.port}")
    print(f"Runtime: {args.runtime_url}")
    print(f"Guard: {args.guard_url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
