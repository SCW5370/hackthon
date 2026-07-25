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
import threading
import time
from typing import Any
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from biolab.catalog import LOCATION_NAMES, SAMPLE_IDS
from guard.verifier import LeaseVerifier
from runtime.contracts import ActionIntent, Decision
from runtime.lease_authority import LeaseAuthority
from runtime.work_orders import WorkOrderIssuer

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "console"
STATIC_FILES = {
    "/": CONSOLE / "experience.html",
    "/experience": CONSOLE / "experience.html",
    "/experience.html": CONSOLE / "experience.html",
    "/monitor": CONSOLE / "monitor.html",
    "/monitor.html": CONSOLE / "monitor.html",
    "/config": CONSOLE / "config.html",
    "/config.html": CONSOLE / "config.html",
    "/config.css": CONSOLE / "config.css",
    "/config.js": CONSOLE / "config.js",
    "/experience.css": CONSOLE / "experience.css",
    "/experience.js": CONSOLE / "experience.js",
    "/monitor.css": CONSOLE / "monitor.css",
    "/monitor.js": CONSOLE / "monitor.js",
    "/joy-lab-reference.jpg": CONSOLE / "joy-lab-reference.jpg",
}
DIRECT_OPENER = build_opener(ProxyHandler({}))
EXPERIENCE_ATTACK_TYPES = {
    "prompt-injection",
    "model-hallucination",
    "intent-tampering",
    "lease-replay",
}
DEMO_RECOVERABLE_ERRORS = {
    "WORK_ORDER_EXPIRED",
    "WORK_ORDER_REQUIRED",
    "WORK_ORDER_BUDGET_EXHAUSTED",
    "WORK_ORDER_RENEWAL_REQUIRED",
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
        # Device-plane endpoints are explicit operator configuration, not
        # Internet destinations. Never route them through a desktop proxy.
        with DIRECT_OPENER.open(request, timeout=timeout) as response:
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
        work_order_fact_mode: str = "camera",
        unsafe_demo_token: str = "",
    ) -> None:
        if work_order_fact_mode not in {"camera", "none"}:
            raise ValueError("work_order_fact_mode must be camera or none")
        self.orchestrator_url = orchestrator_url.rstrip("/")
        self.guard_url = guard_url.rstrip("/")
        self.runtime_url = runtime_url.rstrip("/")
        self.work_order_issuer = work_order_issuer
        self.work_order_fact_mode = work_order_fact_mode
        self.unsafe_demo_token = unsafe_demo_token
        self._last_physical: dict[str, Any] | None = None
        self._experience_lock = threading.RLock()
        self._restore_lock = threading.Lock()
        self._latest_experience_challenge: dict[str, Any] | None = None
        self._experience_challenges: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        line = json_request(f"{self.orchestrator_url}/v1/line/state", timeout=3)
        try:
            preflight = json_request(
                f"{self.orchestrator_url}/v1/preflight",
                timeout=4,
            )
        except UpstreamError as exc:
            preflight = {
                "schema_version": "safeexec.preflight.v1",
                "status": "blocked",
                "ready": False,
                "components": {
                    "orchestrator": {"status": "unavailable", "ready": False},
                    "runtime": {"status": "unknown", "ready": False},
                    "guard": {"status": "unknown", "ready": False},
                    "joy": {"status": "unknown", "ready": False},
                },
                "blockers": [
                    {
                        "code": "PREFLIGHT_UNAVAILABLE",
                        "message": str(exc),
                    }
                ],
                "auto_execute": False,
                "requires_operator_start": True,
            }
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
            guard_endpoint = (
                preflight.get("components", {})
                .get("guard", {})
                .get("endpoint")
            )
            if not isinstance(guard_endpoint, str) or not guard_endpoint:
                guard_endpoint = self.guard_url
            try:
                response = json_request(
                    f"{guard_endpoint.rstrip('/')}/v1/physical",
                    timeout=3,
                )
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
            "schema_version": "safeexec.dashboard.v4",
            "line": line,
            "preflight": preflight,
            "physical": physical,
            "physical_status": physical_status,
        }

    def preflight(self) -> dict[str, Any]:
        return json_request(
            f"{self.orchestrator_url}/v1/preflight",
            timeout=4,
        )

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

    def restore_demo(self, *, automatic: bool = False) -> dict[str, Any]:
        """Rebuild a known-safe exhibition line after boot or authorization expiry."""

        if self.work_order_issuer is None:
            raise UpstreamError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "control-plane signing key is not configured",
            )
        if not self._restore_lock.acquire(blocking=False):
            raise UpstreamError(
                HTTPStatus.CONFLICT,
                "demo restoration is already running",
            )
        try:
            line = json_request(
                f"{self.orchestrator_url}/v1/line/state",
                timeout=3,
            )
            state = str(line.get("line_state", "UNKNOWN"))
            if state in {"RUNNING", "PAUSE_PENDING", "RECOVERING"}:
                return {
                    "status": "already-running",
                    "line": line,
                }
            if line.get("unsafe_recovery"):
                if automatic:
                    return {
                        "status": "operator-required",
                        "reason_code": "UNSAFE_RECONCILIATION_REQUIRED",
                        "line": line,
                    }
                line = self.set_execution_mode("protected", "")
                if line.get("line_state") == "RUNNING":
                    return {"status": "restored", "line": line}
                state = str(line.get("line_state", "UNKNOWN"))

            last_error = line.get("last_error")
            error_code = (
                str(last_error.get("code"))
                if isinstance(last_error, dict)
                else ""
            )
            if (
                automatic
                and state == "ERROR"
                and error_code not in DEMO_RECOVERABLE_ERRORS
            ):
                return {
                    "status": "operator-required",
                    "reason_code": error_code or "UNRECONCILED_ERROR",
                    "line": line,
                }
            if state not in {"STOPPED", "PAUSED", "COMPLETED", "ERROR"}:
                raise UpstreamError(
                    HTTPStatus.CONFLICT,
                    f"cannot restore demo from {state}",
                )

            # Always reconcile the simulator before rebuilding logical tasks.
            # This makes a cold boot deterministic even if Windows preserved a
            # partially processed JOY scene.
            self.control("reset")
            issued = self.issue_work_order(
                {
                    "schema_version": "safeexec.work-order-draft.v1",
                    "sample_ids": list(SAMPLE_IDS),
                    "source": "cold-storage",
                    "destination": "analyzer-01",
                    "subject_principal_id": "lab-agent-01",
                    "valid_for_ms": 8 * 60 * 60 * 1000,
                    "operator_note": (
                        "Motion Gate 展览持续产线：循环分析固定实体池"
                    ),
                    "max_executions_per_sample": 10_000,
                }
            )
            configured = self.set_continuous_mode(True)
            started = self.control("start")
            return {
                "status": "restored",
                "work_order_id": (
                    issued.get("work_order", {}).get("work_order_id")
                ),
                "continuous_mode": configured.get("continuous_mode"),
                "line": started,
            }
        finally:
            self._restore_lock.release()

    def set_continuous_mode(self, enabled: bool) -> dict[str, Any]:
        return json_request(
            f"{self.orchestrator_url}/v1/control/continuous",
            method="POST",
            payload={"enabled": enabled},
            timeout=5,
        )

    def inject(self, value: dict[str, Any]) -> dict[str, Any]:
        return json_request(
            f"{self.orchestrator_url}/v1/testing/injections",
            method="POST",
            payload=value,
            timeout=5,
        )

    def experience_challenge(self, value: dict[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version",
            "challenge_id",
            "attack_type",
            "target_task_id",
            "untrusted_content",
        }
        if set(value) != required:
            raise ValueError(
                "experience challenge expects exactly "
                f"{sorted(required)}"
            )
        if value["schema_version"] != "safeexec.experience-challenge.v1":
            raise ValueError("unsupported experience challenge schema")
        try:
            challenge_id = str(uuid.UUID(str(value["challenge_id"])))
        except (TypeError, ValueError) as exc:
            raise ValueError("challenge_id must be a UUID") from exc
        attack_type = value["attack_type"]
        if attack_type not in EXPERIENCE_ATTACK_TYPES:
            raise ValueError("unknown experience attack_type")
        target_task_id = value["target_task_id"]
        if not isinstance(target_task_id, str) or len(target_task_id) > 128:
            raise ValueError("target_task_id must be a string of at most 128 characters")
        untrusted_content = value["untrusted_content"]
        if (
            not isinstance(untrusted_content, str)
            or len(untrusted_content) > 1000
        ):
            raise ValueError("untrusted_content must contain at most 1000 characters")

        line = json_request(
            f"{self.orchestrator_url}/v1/line/state",
            timeout=3,
        )
        unsafe_mode = line.get("execution_mode") == "unsafe-baseline"

        if unsafe_mode or attack_type in {
            "prompt-injection",
            "model-hallucination",
        }:
            if not target_task_id or not untrusted_content.strip():
                raise ValueError(
                    "the challenge requires a queued task and attack description"
                )
            injection = self.inject(
                {
                    "schema_version": "safeexec.attack-injection.v1",
                    "injection_id": challenge_id,
                    "attack_id": f"experience-{attack_type}",
                    "attack_type": attack_type,
                    "channel": (
                        "operator_message"
                        if attack_type == "prompt-injection"
                        else "tool_output"
                    ),
                    "target_task_id": target_task_id,
                    "untrusted_content": untrusted_content.strip(),
                    "actor_claims": {
                        "claimed_role": "visitor",
                        "claimed_identity": "experience-participant",
                    },
                    "requested_at_ms": int(time.time() * 1000),
                }
            )
            result = self._challenge_result(
                challenge_id=challenge_id,
                attack_type=attack_type,
                status="armed",
                blocked_at=(
                    "bypassed"
                    if unsafe_mode
                    else (
                        "safeexec_scope"
                        if attack_type == "intent-tampering"
                        else "runtime"
                    )
                ),
                reason_code="AWAITING_AGENT_INTENT",
                physical_outcome="pending",
                detail=(
                    "Motion Gate 已关闭，攻击将在目标任务到达时直接进入执行器。"
                    if unsafe_mode
                    else (
                        "攻击已进入 Agent 任务上下文，等待形成结构化动作意图。"
                    )
                ),
                evidence={
                    "target_task_id": injection.get("target_task_id"),
                    "injection_id": injection.get("injection_id"),
                    "execution_mode": line.get("execution_mode"),
                    "lease_issued": False,
                    "guard_invoked": False,
                    "executor_invoked": False,
                },
            )
        else:
            result = self._run_guard_challenge(challenge_id, attack_type)

        with self._experience_lock:
            self._latest_experience_challenge = json.loads(json.dumps(result))
            self._experience_challenges.append(
                json.loads(json.dumps(result))
            )
            if len(self._experience_challenges) > 64:
                self._experience_challenges = self._experience_challenges[-64:]
        return result

    def latest_experience_challenge(self) -> dict[str, Any]:
        with self._experience_lock:
            value = self._latest_experience_challenge
            if value is None:
                return {
                    "schema_version": "safeexec.experience-challenge-result.v1",
                    "status": "idle",
                }
            return json.loads(json.dumps(value))

    def experience_challenges(self) -> dict[str, Any]:
        """Return read-only challenge evidence for the exhibition monitor."""

        with self._experience_lock:
            return {
                "schema_version": "safeexec.experience-challenge-list.v1",
                "challenges": json.loads(
                    json.dumps(self._experience_challenges)
                ),
            }

    def monitor_snapshot(self) -> dict[str, Any]:
        """Build the read-only security-observability surface."""

        dashboard = self.snapshot()
        self._reconcile_challenge_history(dashboard.get("line", {}))
        history = self.experience_challenges()
        return {
            "schema_version": "safeexec.monitor.v1",
            "generated_at_ms": int(time.time() * 1000),
            "dashboard": dashboard,
            "challenges": history["challenges"],
        }

    def _reconcile_challenge_history(self, line: dict[str, Any]) -> None:
        tasks = [
            task
            for task in [
                *line.get("tasks", []),
                *line.get("recent_tasks", []),
            ]
            if isinstance(task, dict)
        ]
        by_id = {
            str(task.get("task_id")): task
            for task in tasks
            if task.get("task_id")
        }
        with self._experience_lock:
            for challenge in self._experience_challenges:
                if challenge.get("status") != "armed":
                    continue
                evidence = challenge.get("evidence")
                if not isinstance(evidence, dict):
                    continue
                task = by_id.get(str(evidence.get("target_task_id")))
                if task is None:
                    continue
                blocked = task.get("blocked_decision")
                if isinstance(blocked, dict):
                    recovered = task.get("status") == "COMPLETED"
                    challenge.update(
                        {
                            "status": "recovered" if recovered else "blocked",
                            "reason_code": blocked.get(
                                "reason_code",
                                "NO_MATCHING_GRANT",
                            ),
                            "physical_outcome": (
                                "authorized-recovery"
                                if recovered
                                else "no-change"
                            ),
                            "detail": (
                                "越权动作未获得执行权，污染会话已销毁，"
                                "可信任务随后恢复。"
                                if recovered
                                else "越权动作未获得执行权。"
                            ),
                        }
                    )
                    evidence.update(
                        {
                            "intent": task.get("blocked_intent"),
                            "decision": blocked,
                            "executor_invoked": False,
                            "recovered": recovered,
                        }
                    )
                elif task.get("status") == "UNSAFE_EXECUTED":
                    attack_type = str(challenge.get("attack_type"))
                    challenge.update(
                        {
                            "status": "executed",
                            "blocked_at": "bypassed",
                            "reason_code": "SAFEEXEC_DISABLED",
                            "physical_outcome": task.get(
                                "attack_effect",
                                "unexpected-action",
                            ),
                            "detail": {
                                "prompt-injection": "目标样品被送入废弃区。",
                                "model-hallucination": "目标样品被错误送入隔离区。",
                                "intent-tampering": "另一件样品被替换为目标并送入废弃区。",
                                "lease-replay": "旧复位命令被重放，整个批次意外回滚。",
                            }.get(attack_type, "无保护动作已抵达执行器。"),
                        }
                    )
                    evidence.update(
                        {
                            "intent": task.get("intent"),
                            "attack_effect": task.get("attack_effect"),
                            "replacement_sample_id": task.get(
                                "attack_replacement_sample_id"
                            ),
                            "executor_invoked": True,
                        }
                    )
                challenge["updated_at_ms"] = int(time.time() * 1000)
            if self._experience_challenges:
                self._latest_experience_challenge = json.loads(
                    json.dumps(self._experience_challenges[-1])
                )

    def _run_hallucination_challenge(
        self,
        challenge_id: str,
        target_task_id: str,
    ) -> dict[str, Any]:
        line = json_request(
            f"{self.orchestrator_url}/v1/line/state",
            timeout=3,
        )
        work_order_id = line.get("active_work_order_id")
        if not isinstance(work_order_id, str) or not work_order_id:
            raise UpstreamError(
                HTTPStatus.CONFLICT,
                "activate a trusted WorkOrder before running this challenge",
            )
        target_task_id = self._resolve_challenge_target(
            line,
            target_task_id,
        )
        target = next(
            (
                task
                for task in line.get("tasks", [])
                if isinstance(task, dict)
                and task.get("task_id") == target_task_id
            ),
            None,
        )
        sample_id = (
            target.get("sample_id")
            if isinstance(target, dict)
            and target.get("sample_id") in SAMPLE_IDS
            else SAMPLE_IDS[0]
        )
        intent = {
            "schema_version": "safeexec.action.v2",
            "request_id": str(uuid.uuid4()),
            "principal_id": "lab-agent-01",
            "work_order_id": work_order_id,
            "issued_at_ms": int(time.time() * 1000),
            "action": "lab.sample.transfer",
            "resource": {"type": "lab.sample", "id": sample_id},
            "arguments": {
                "source": "cold-storage",
                "destination": "quarantine-zone",
            },
        }
        response = json_request(
            f"{self.runtime_url}/v1/actions",
            method="POST",
            payload=intent,
            timeout=8,
        )
        decision = response.get("decision")
        blocked = (
            response.get("status") == "denied"
            and isinstance(decision, dict)
            and decision.get("effect") == "deny"
            and response.get("lease") is None
        )
        if not blocked:
            raise UpstreamError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "hallucination challenge did not fail closed",
            )
        return self._challenge_result(
            challenge_id=challenge_id,
            attack_type="model-hallucination",
            status="blocked",
            blocked_at="runtime",
            reason_code=str(decision.get("reason_code")),
            physical_outcome="no-change",
            detail=(
                f"Agent 将 {sample_id} 的目标臆测为隔离区，"
                "该动作不在可信工单授权范围内。"
            ),
            evidence={
                "intent": intent,
                "decision": decision,
                "lease_issued": False,
                "guard_invoked": False,
                "executor_invoked": False,
            },
        )

    @staticmethod
    def _resolve_challenge_target(
        line: dict[str, Any],
        target_task_id: str,
    ) -> str:
        if "tasks" not in line and target_task_id != "next-queued":
            return target_task_id
        tasks = [
            task
            for task in line.get("tasks", [])
            if isinstance(task, dict)
            and task.get("status") == "QUEUED"
            and not task.get("untrusted_input")
        ]
        if target_task_id == "next-queued":
            if not tasks:
                raise UpstreamError(
                    HTTPStatus.CONFLICT,
                    "no queued task is currently available for injection",
                )
            return str(tasks[0]["task_id"])
        if not any(task.get("task_id") == target_task_id for task in tasks):
            raise UpstreamError(
                HTTPStatus.CONFLICT,
                "selected task is no longer queued; choose the next task",
            )
        return target_task_id

    def _run_guard_challenge(
        self,
        challenge_id: str,
        attack_type: str,
    ) -> dict[str, Any]:
        private_key, public_key = LeaseAuthority.generate_keypair()
        authority = LeaseAuthority(private_key, "experience-key-01")
        verifier = LeaseVerifier(public_key, "joy-guard-01")
        try:
            work_order_id = str(uuid.uuid4())
            intent = ActionIntent.from_dict(
                {
                    "schema_version": "safeexec.action.v2",
                    "request_id": str(uuid.uuid4()),
                    "principal_id": "lab-agent-01",
                    "work_order_id": work_order_id,
                    "issued_at_ms": int(time.time() * 1000),
                    "action": "lab.sample.transfer",
                    "resource": {"type": "lab.sample", "id": "sample-A"},
                    "arguments": {
                        "source": "cold-storage",
                        "destination": "analyzer-01",
                    },
                }
            )
            decision = Decision.allow(
                request_id=intent.request_id,
                matched_grant_id="experience-verified-grant",
                fact_refs=(),
            )
            lease = authority.issue_lease(
                intent=intent,
                decision=decision,
                audience="joy-guard-01",
                mission_id=work_order_id,
            )
            intent_value = intent.to_dict()
            lease_value = lease.to_dict()

            if attack_type == "intent-tampering":
                challenged_intent = json.loads(json.dumps(intent_value))
                challenged_intent["arguments"]["destination"] = "waste-bin"
                accepted, reason, _ = verifier.verify_and_consume(
                    challenged_intent,
                    lease_value,
                )
                detail = (
                    "Lease 绑定的是“送往分析区”，传输途中被改成“送往废弃区”。"
                )
                evidence = {
                    "signed_intent": intent_value,
                    "received_intent": challenged_intent,
                    "lease_id": lease.lease_id,
                    "lease_issued": True,
                    "guard_invoked": True,
                    "executor_invoked": False,
                }
            else:
                first_accepted, first_reason, _ = verifier.verify_and_consume(
                    intent_value,
                    lease_value,
                )
                accepted, reason, _ = verifier.verify_and_consume(
                    intent_value,
                    lease_value,
                )
                detail = "同一个一次性 Lease 在首次消费后被再次提交。"
                evidence = {
                    "lease_id": lease.lease_id,
                    "first_attempt": {
                        "accepted": first_accepted,
                        "reason_code": first_reason,
                    },
                    "second_attempt": {
                        "accepted": accepted,
                        "reason_code": reason,
                    },
                    "lease_issued": True,
                    "guard_invoked": True,
                    "executor_invoked": False,
                }
            if accepted:
                raise RuntimeError("Guard challenge unexpectedly passed")
            return self._challenge_result(
                challenge_id=challenge_id,
                attack_type=attack_type,
                status="blocked",
                blocked_at="guard",
                reason_code=reason,
                physical_outcome="no-change",
                detail=detail,
                evidence=evidence,
            )
        finally:
            verifier.close()

    @staticmethod
    def _challenge_result(
        *,
        challenge_id: str,
        attack_type: str,
        status: str,
        blocked_at: str,
        reason_code: str,
        physical_outcome: str,
        detail: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": "safeexec.experience-challenge-result.v1",
            "challenge_id": challenge_id,
            "attack_type": attack_type,
            "status": status,
            "blocked_at": blocked_at,
            "reason_code": reason_code,
            "physical_outcome": physical_outcome,
            "detail": detail,
            "evidence": evidence,
            "created_at_ms": int(time.time() * 1000),
        }

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
            headers={
                "X-Unsafe-Demo-Token": (
                    provided_token or self.unsafe_demo_token
                )
            },
            timeout=160,
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
            "work_order_fact_mode": self.work_order_fact_mode,
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
        optional = {"max_executions_per_sample"}
        if not required.issubset(value) or set(value) - required - optional:
            raise ValueError(
                "invalid WorkOrder draft fields; "
                f"missing={sorted(required - set(value))}, "
                f"extra={sorted(set(value) - required - optional)}"
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
        max_executions = value.get("max_executions_per_sample", 1)
        if (
            isinstance(max_executions, bool)
            or not isinstance(max_executions, int)
            or not 1 <= max_executions <= 10_000
        ):
            raise ValueError(
                "max_executions_per_sample must be an integer from 1 to 10000"
            )

        required_facts = (
            [
                {
                    "key": "camera.healthy",
                    "equals": True,
                    "max_age_ms": 1500,
                }
            ]
            if self.work_order_fact_mode == "camera"
            else []
        )
        grants = [
            {
                "grant_id": f"work-order-{sample_id.lower()}-analysis",
                "action": "lab.sample.transfer",
                "resource": {"type": "lab.sample", "id": sample_id},
                "arguments": {
                    "source": source,
                    "destination": destination,
                },
                "required_facts": [dict(fact) for fact in required_facts],
                "max_executions": max_executions,
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


class DemoRestoreSupervisor:
    """Restore boot state and watch for authorization-only line failures."""

    def __init__(
        self,
        backend: DashboardBackend,
        *,
        enabled: bool,
        retry_seconds: float = 8.0,
    ) -> None:
        self.backend = backend
        self.enabled = enabled
        self.retry_seconds = max(1.0, retry_seconds)
        self._thread: threading.Thread | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if not self.enabled or (
            self._thread is not None and self._thread.is_alive()
        ):
            return
        self._thread = threading.Thread(
            target=self._run,
            name="motion-gate-demo-restore",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        boot_restore_pending = True
        while True:
            try:
                should_restore = boot_restore_pending
                if not boot_restore_pending:
                    line = json_request(
                        f"{self.backend.orchestrator_url}/v1/line/state",
                        timeout=3,
                    )
                    error = line.get("last_error")
                    error_code = (
                        str(error.get("code"))
                        if isinstance(error, dict)
                        else ""
                    )
                    should_restore = (
                        line.get("line_state") in {"STOPPED", "ERROR"}
                        and error_code in DEMO_RECOVERABLE_ERRORS
                        and not line.get("unsafe_recovery")
                    )
                if should_restore:
                    result = self.backend.restore_demo(automatic=True)
                    self.last_result = result
                    self.last_error = None
                    if result.get("status") in {
                        "restored",
                        "already-running",
                        "operator-required",
                    }:
                        boot_restore_pending = False
            except (UpstreamError, ConnectionError, RuntimeError) as exc:
                self.last_error = str(exc)
            time.sleep(self.retry_seconds)


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
            elif parsed.path == "/api/monitor":
                self._json(self.backend.monitor_snapshot())
            elif parsed.path == "/api/preflight":
                self._json(self.backend.preflight())
            elif parsed.path == "/api/experience/challenges":
                self._json(self.backend.experience_challenges())
            elif parsed.path == "/api/experience/challenges/latest":
                self._json(self.backend.latest_experience_challenge())
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
            elif parsed.path == "/api/control/continuous":
                value = self._body()
                if set(value) != {"enabled"} or not isinstance(
                    value["enabled"], bool
                ):
                    raise ValueError(
                        "continuous endpoint expects exactly one boolean field"
                    )
                self._json(self.backend.set_continuous_mode(value["enabled"]))
            elif parsed.path == "/api/demo/restore":
                self._require_empty(self._body())
                self._json(self.backend.restore_demo())
            elif parsed.path.startswith("/api/control/"):
                self._require_empty(self._body())
                action = parsed.path.rsplit("/", 1)[-1]
                self._json(self.backend.control(action))
            elif parsed.path == "/api/testing/injections":
                self._json(self.backend.inject(self._body()), HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/experience/challenges":
                self._json(
                    self.backend.experience_challenge(self._body()),
                    HTTPStatus.ACCEPTED,
                )
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
            upstream = DIRECT_OPENER.open(request, timeout=30)
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
    parser.add_argument(
        "--work-order-fact-mode",
        choices=("camera", "none"),
        default=os.environ.get("SAFEEXEC_WORK_ORDER_FACT_MODE", "camera"),
        help="Attach camera Fact requirements or issue sensor-independent orders.",
    )
    parser.add_argument(
        "--unsafe-demo-token",
        default=os.environ.get("SAFEEXEC_UNSAFE_DEMO_TOKEN", ""),
        help="Server-side token used by the isolated experience A/B switch.",
    )
    parser.add_argument(
        "--auto-restore-demo",
        action="store_true",
        default=os.environ.get("SAFEEXEC_DEMO_AUTORESTORE", "0") == "1",
        help="After boot, restore and start only known-safe demo states.",
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
        work_order_fact_mode=args.work_order_fact_mode,
        unsafe_demo_token=args.unsafe_demo_token,
    )
    supervisor = DemoRestoreSupervisor(
        server.backend,
        enabled=args.auto_restore_demo,
    )
    supervisor.start()
    print(f"SafeExec Dashboard listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
