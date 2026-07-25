"""
SafeExec V1 Runtime HTTP Server
提供 REST API:
- POST /v1/actions - Agent 提交动作
- POST /v1/facts - 适配器更新 Fact
- GET /v1/events - 审计事件流
- GET /healthz - 健康检查
"""
from __future__ import annotations

import json
import os
import sys
import argparse
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .contracts import ActionIntent, Decision, Fact, MissionSpec, TRANSFER_ACTION
from .policy_engine import PolicyEngine
from .lease_authority import LeaseAuthority
from .fact_hub import FactHub
from .event_ledger import EventLedger
from .state_machine import StateMachine
from .work_orders import WorkOrderRegistry
from .executor_discovery import GuardEndpointDiscovery


class SafeExecRuntime:
    """
    SafeExec Runtime 主类
    整合所有组件
    """

    def __init__(
        self,
        mission_spec: MissionSpec,
        private_key_b64: str,
        key_id: str = "x5-runtime-key-01",
        guard_url: str = "http://localhost:8788/v1/execute",
        trusted_work_order_keys: dict[str, str] | None = None,
        require_work_order_for_actions: bool = False,
        guard_candidates: list[str] | None = None,
    ):
        self.mission_spec = mission_spec
        self.guard_url = guard_url
        self.require_work_order_for_actions = require_work_order_for_actions
        self.guard_discovery = (
            GuardEndpointDiscovery(guard_candidates)
            if guard_candidates
            else None
        )

        # 核心组件
        self.policy_engine = PolicyEngine(mission_spec)
        self.lease_authority = LeaseAuthority(private_key_b64, key_id)
        self.fact_hub = FactHub()
        self.event_ledger = EventLedger()
        self.state_machine = StateMachine(self._emit_event)
        self._latest_action_lock = threading.RLock()
        self._latest_action: dict | None = None
        self._action_lock = threading.Lock()
        self.work_orders = (
            WorkOrderRegistry(trusted_work_order_keys, mission_spec)
            if trusted_work_order_keys
            else None
        )

        # 当关键 Fact 变化时触发状态变化
        self.fact_hub.subscribe(self._on_critical_fact_change)

    def _emit_event(self, event: str, source: str, payload: dict, severity: str = "info"):
        self.event_ledger.emit(event, source, payload, severity)

    def _on_critical_fact_change(self, key: str, value: any):
        """关键 Fact 变化时触发状态机"""
        if value is not True:  # zone.clear=False 或 camera.healthy=False/None
            self.state_machine.to_safe_hold(f"{key}={value}")

    def process_action(self, intent_dict: dict, audience: str = "joy-guard-01") -> dict:
        """Serialize physical actions while allowing read-only HTTP polling."""
        with self._action_lock:
            return self._process_action(intent_dict, audience)

    def _process_action(self, intent_dict: dict, audience: str) -> dict:
        """
        处理 ActionIntent

        流程:
        1. 解析并验证 intent
        2. Policy Engine 评估
        3. 如果允许，签发 Lease
        4. 调用 Guard 执行
        5. 返回结果
        """
        # 1. 解析 intent
        try:
            intent = ActionIntent.from_dict(intent_dict)
        except ValueError as e:
            self._emit_event("intent.received", "runtime", {"error": str(e)}, "warning")
            self._record_latest_action(
                intent=intent_dict,
                status="error",
                reason_code="INVALID_REQUEST",
            )
            return {
                "status": "error",
                "reason_code": "INVALID_REQUEST",
                "message": str(e),
            }

        self._emit_event(
            "intent.received",
            "runtime",
            {
                "request_id": intent.request_id,
                "principal_id": intent.principal_id,
                "action": intent.action,
            },
        )

        # 2. OrganizationPolicy 评估。MissionSpec 是长期权限上限。
        facts = self.fact_hub.get_all_facts()
        decision = self.policy_engine.evaluate(intent, facts)

        if decision.effect.value == "deny":
            self._emit_event(
                "policy.denied",
                "runtime",
                {
                    "request_id": intent.request_id,
                    "reason_code": decision.reason_code,
                },
                "warning",
            )
            response = {
                "status": "denied",
                "decision": decision.to_dict(),
            }
            self._record_latest_action(
                intent=intent.to_dict(),
                status="denied",
                decision=decision.to_dict(),
            )
            return response

        # 3. WorkOrder 评估。Agent 只能在本次短期业务授权内行动。
        matched_work_order_grant = None
        if intent.action == TRANSFER_ACTION and (
            self.require_work_order_for_actions
            or intent.work_order_id is not None
        ):
            if self.work_orders is None:
                order_reason = "WORK_ORDER_NOT_FOUND"
            else:
                matched_work_order_grant, order_reason = self.work_orders.authorize(
                    intent
                )
            if order_reason is not None:
                denied = Decision.deny(
                    request_id=intent.request_id,
                    reason_code=order_reason,
                )
                self._emit_event(
                    "work_order.denied",
                    "runtime",
                    {
                        "request_id": intent.request_id,
                        "work_order_id": intent.work_order_id,
                        "reason_code": order_reason,
                    },
                    "warning",
                )
                response = {"status": "denied", "decision": denied.to_dict()}
                self._record_latest_action(
                    intent=intent.to_dict(),
                    status="denied",
                    decision=denied.to_dict(),
                )
                return response
            decision = Decision.allow(
                request_id=intent.request_id,
                matched_grant_id=matched_work_order_grant.grant_id,
                fact_refs=decision.fact_refs,
            )
            self._emit_event(
                "work_order.matched",
                "runtime",
                {
                    "request_id": intent.request_id,
                    "work_order_id": intent.work_order_id,
                    "grant_id": matched_work_order_grant.grant_id,
                },
            )

        # 4. Device readiness is a precondition, not an authorization source.
        # Discovery never executes; unavailable devices fail closed before a
        # Lease exists.
        if self.guard_discovery is not None:
            try:
                self.guard_discovery.require_ready()
            except ConnectionError as exc:
                unavailable = Decision.deny(
                    request_id=intent.request_id,
                    reason_code="EXECUTOR_UNAVAILABLE",
                )
                self._emit_event(
                    "executor.unavailable",
                    "runtime",
                    {
                        "request_id": intent.request_id,
                        "error": str(exc),
                    },
                    "critical",
                )
                response = {
                    "status": "denied",
                    "decision": unavailable.to_dict(),
                }
                self._record_latest_action(
                    intent=intent.to_dict(),
                    status="denied",
                    decision=unavailable.to_dict(),
                )
                return response

        # 5. 允许 - 签发 Lease
        self._emit_event(
            "policy.allowed",
            "runtime",
            {
                "request_id": intent.request_id,
                "matched_grant_id": decision.matched_grant_id,
            },
        )

        lease = self.lease_authority.issue_lease(
            intent=intent,
            decision=decision,
            audience=audience,
            mission_id=intent.work_order_id or self.mission_spec.mission_id,
        )

        self._emit_event(
            "lease.issued",
            "runtime",
            {
                "lease_id": lease.lease_id,
                "expires_at_ms": lease.expires_at_ms,
            },
        )

        # 6. 调用 Guard 执行 (同步)
        guard_response = self._call_guard(intent, lease)
        if (
            matched_work_order_grant is not None
            and intent.work_order_id
            and guard_response.get("status") == "executed"
        ):
            assert self.work_orders is not None
            self.work_orders.consume(
                intent.work_order_id,
                matched_work_order_grant.grant_id,
            )
            self._emit_event(
                "work_order.grant_consumed",
                "runtime",
                {
                    "work_order_id": intent.work_order_id,
                    "grant_id": matched_work_order_grant.grant_id,
                    "request_id": intent.request_id,
                },
            )

        # 7. 返回结果
        response = {
            "status": "ok",
            "decision": decision.to_dict(),
            "lease": lease.to_dict(),
            "guard_response": guard_response,
        }
        self._record_latest_action(
            intent=intent.to_dict(),
            status="completed",
            decision=decision.to_dict(),
            lease={
                "lease_id": lease.lease_id,
                "audience": lease.audience,
                "issued_at_ms": lease.issued_at_ms,
                "expires_at_ms": lease.expires_at_ms,
            },
            guard_response=guard_response,
        )
        return response

    def _record_latest_action(
        self,
        *,
        intent: dict,
        status: str,
        decision: dict | None = None,
        lease: dict | None = None,
        guard_response: dict | None = None,
        reason_code: str | None = None,
    ) -> None:
        """Store a dashboard-safe summary without exposing Lease signatures."""
        record = {
            "updated_at_ms": int(time.time() * 1000),
            "status": status,
            "intent": intent,
            "decision": decision,
            "lease": lease,
            "guard_response": guard_response,
        }
        if reason_code:
            record["reason_code"] = reason_code
        with self._latest_action_lock:
            self._latest_action = record

    def _call_guard(self, intent: ActionIntent, lease) -> dict:
        """调用 Guard 执行"""
        import urllib.request
        import urllib.error

        payload = {
            "intent": intent.to_dict(),
            "lease": lease.to_dict(),
        }

        discovery = self.guard_discovery
        try:
            execute_url = (
                discovery.require_ready()
                if discovery is not None
                else self.guard_url
            )
            if discovery is not None:
                discovery.set_executing(True)
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                execute_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
                self._emit_event(
                    "guard.response",
                    "runtime",
                    {"lease_id": lease.lease_id, "status": result.get("status")},
                )
                return result

        except urllib.error.URLError as e:
            self._emit_event(
                "guard.error",
                "runtime",
                {"lease_id": lease.lease_id, "error": str(e)},
                "critical",
            )
            return {"status": "error", "message": f"Guard unavailable: {e}"}
        except ConnectionError as e:
            self._emit_event(
                "guard.error",
                "runtime",
                {"lease_id": lease.lease_id, "error": str(e)},
                "critical",
            )
            return {"status": "error", "message": f"Guard unavailable: {e}"}
        finally:
            if discovery is not None:
                discovery.set_executing(False)
                discovery.refresh()

    def add_fact(self, fact_dict: dict) -> dict:
        """添加或更新 Fact"""
        try:
            fact = Fact.from_dict(fact_dict)
            self.fact_hub.update_fact(fact)

            self._emit_event(
                "fact.updated",
                fact.source,
                {"key": fact.key, "value": fact.value},
                "info" if fact.value is True else "warning",
            )

            return {"status": "ok"}

        except ValueError as e:
            return {"status": "error", "message": str(e)}

    def register_work_order(self, value: dict) -> dict:
        if self.work_orders is None:
            raise ValueError("WorkOrder trust is not configured")
        order = self.work_orders.register(value)
        self._emit_event(
            "work_order.registered",
            "runtime",
            {
                "work_order_id": order.work_order_id,
                "issuer_id": order.issuer_id,
                "subject_principal_id": order.subject_principal_id,
                "grant_count": len(order.grants),
                "valid_until_ms": order.valid_until_ms,
            },
        )
        status = self.work_orders.status(order.work_order_id)
        assert status is not None
        return {"status": "registered", "work_order": status}

    def get_work_order(self, work_order_id: str) -> dict | None:
        if self.work_orders is None:
            return None
        return self.work_orders.status(work_order_id)

    def list_work_orders(self) -> dict:
        return {
            "work_orders": self.work_orders.list() if self.work_orders else [],
        }

    def get_config(self) -> dict:
        return {
            "schema_version": "safeexec.runtime-config.v1",
            "organization_policy": self.mission_spec.to_dict(),
            "work_order_enforcement": self.require_work_order_for_actions,
            "trusted_work_order_issuers": (
                list(self.work_orders.trusted_issuers) if self.work_orders else []
            ),
        }

    def get_readiness(self, *, refresh: bool = False) -> dict:
        connectivity = None
        if self.guard_discovery is not None:
            connectivity = (
                self.guard_discovery.refresh()
                if refresh
                else self.guard_discovery.snapshot()
            )
        ready = connectivity is None or connectivity.get("ready") is True
        blockers = [] if ready else [
            {
                "code": "EXECUTOR_UNAVAILABLE",
                "message": "未发现同时就绪的 Windows Guard 与 JOY 执行器",
            }
        ]
        return {
            "schema_version": "safeexec.runtime-readiness.v1",
            "status": "ready" if ready else "unavailable",
            "ready": ready,
            "runtime": {"status": "ready"},
            "executor_connectivity": connectivity,
            "blockers": blockers,
            "auto_execute": False,
        }

    def get_events(self, after_seq: int = 0) -> dict:
        """获取事件"""
        return {
            "events": [e.to_dict() for e in self.event_ledger.events_after(after_seq)],
            "last_seq": self.event_ledger.get_last_seq(),
        }

    def get_state(self) -> dict:
        """获取当前状态"""
        with self._latest_action_lock:
            latest_action = (
                json.loads(json.dumps(self._latest_action))
                if self._latest_action is not None
                else None
            )
        return {
            "system_state": self.state_machine.snapshot(),
            "facts": [f.to_dict() for f in self.fact_hub.get_all_facts().values()],
            "latest_action": latest_action,
        }


# ============================================================
# HTTP Handler
# ============================================================

_runtime: SafeExecRuntime = None


class Handler(BaseHTTPRequestHandler):
    server_version = "SafeExecRuntime/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/healthz":
            self._json({"status": "ok"})
            return

        if parsed.path == "/readyz":
            refresh = parse_qs(parsed.query).get("refresh", ["0"])[0] == "1"
            value = _runtime.get_readiness(refresh=refresh)
            self._json(
                value,
                HTTPStatus.OK if value["ready"] else HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return

        if parsed.path == "/v1/state":
            self._json(_runtime.get_state())
            return

        if parsed.path == "/v1/events":
            after = int(parse_qs(parsed.query).get("after", ["0"])[0])
            self._json(_runtime.get_events(after))
            return

        if parsed.path == "/v1/keys":
            # 返回公钥 (Guard 需要)
            self._json({"public_key": _runtime.lease_authority.get_public_key_b64()})
            return

        if parsed.path == "/v1/config":
            self._json(_runtime.get_config())
            return

        if parsed.path == "/v1/work-orders":
            self._json(_runtime.list_work_orders())
            return

        if parsed.path.startswith("/v1/work-orders/"):
            work_order_id = parsed.path.rsplit("/", 1)[-1]
            value = _runtime.get_work_order(work_order_id)
            if value is None:
                self._error(HTTPStatus.NOT_FOUND, "WorkOrder not found")
            else:
                self._json({"work_order": value})
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

        if parsed.path == "/v1/actions":
            audience = data.pop("audience", "joy-guard-01")
            result = _runtime.process_action(data, audience)
            self._json(result)
            return

        if parsed.path == "/v1/facts":
            result = _runtime.add_fact(data)
            self._json(result)
            return

        if parsed.path == "/v1/work-orders":
            try:
                result = _runtime.register_work_order(data)
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._json(result, HTTPStatus.CREATED)
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


def create_runtime(
    mission_path: str,
    private_key_path: str,
    guard_url: str,
    *,
    work_order_public_key_path: str | None = None,
    work_order_issuer_id: str = "biolab-control-plane",
    guard_candidates: list[str] | None = None,
) -> SafeExecRuntime:
    """创建 Runtime 实例"""
    import yaml

    # 加载 MissionSpec
    with open(mission_path) as f:
        mission_data = yaml.safe_load(f)
    mission = MissionSpec.from_dict(mission_data)

    # 加载私钥
    with open(private_key_path) as f:
        private_key_b64 = f.read().strip()
    trusted_work_order_keys = None
    if work_order_public_key_path:
        with open(work_order_public_key_path) as f:
            trusted_work_order_keys = {work_order_issuer_id: f.read().strip()}

    return SafeExecRuntime(
        mission_spec=mission,
        private_key_b64=private_key_b64,
        guard_url=guard_url,
        trusted_work_order_keys=trusted_work_order_keys,
        require_work_order_for_actions=trusted_work_order_keys is not None,
        guard_candidates=guard_candidates,
    )


def main():
    parser = argparse.ArgumentParser(description="SafeExec Runtime")
    parser.add_argument("--mission", required=True, help="Path to mission.yaml")
    parser.add_argument("--key", required=True, help="Path to private key (base64)")
    parser.add_argument("--guard-url", default="http://localhost:8788/v1/execute")
    parser.add_argument(
        "--guard-candidate",
        action="append",
        default=[],
        help="Allowed Guard service base URL; repeat for failover candidates.",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--work-order-public-key")
    parser.add_argument(
        "--work-order-issuer-id",
        default="biolab-control-plane",
    )

    args = parser.parse_args()

    global _runtime
    _runtime = create_runtime(
        args.mission,
        args.key,
        args.guard_url,
        work_order_public_key_path=args.work_order_public_key,
        work_order_issuer_id=args.work_order_issuer_id,
        guard_candidates=args.guard_candidate or None,
    )

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SafeExec Runtime listening on {args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
