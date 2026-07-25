"""Autonomous six-task production line with fail-closed recovery."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
import re
import threading
import time
import uuid
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from biolab.catalog import SAMPLE_IDS
from lab_agent.contracts import (
    ActionPlan,
    build_action_intent,
    build_reset_intent,
)
from .job_compiler import DeterministicJobCompiler


LINE_STATES = {
    "STOPPED",
    "RUNNING",
    "PAUSE_PENDING",
    "PAUSED",
    "RECOVERING",
    "COMPLETED",
    "ERROR",
}
TASK_STATES = {
    "QUEUED",
    "PLANNING",
    "SUBMITTED",
    "EXECUTING",
    "COMPLETED",
    "BLOCKED",
    "RECOVERING",
    "FAILED",
}
INJECTION_FIELDS = {
    "schema_version",
    "injection_id",
    "attack_id",
    "channel",
    "target_task_id",
    "untrusted_content",
    "actor_claims",
    "requested_at_ms",
}
INJECTION_REQUIRED = INJECTION_FIELDS - {"actor_claims"}
INJECTION_CHANNELS = {
    "sample_label",
    "voice",
    "qr",
    "tool_output",
    "operator_message",
}
OPERATOR_COMMAND_FIELDS = {
    "schema_version",
    "command_id",
    "text",
    "requested_by",
    "submitted_at_ms",
}
EXECUTION_MODES = {"protected", "unsafe-baseline"}


class RuntimeClient(Protocol):
    def publish_camera_fact(self) -> dict[str, Any]: ...

    def submit_action(self, intent: dict[str, Any]) -> dict[str, Any]: ...

    def get_work_order(self, work_order_id: str) -> dict[str, Any]: ...

    def get_readiness(self) -> dict[str, Any]: ...


class UnsafeExecutorClient(Protocol):
    def submit_action(self, intent: dict[str, Any]) -> dict[str, Any]: ...

    def get_readiness(self) -> dict[str, Any]: ...


class HttpRuntimeClient:
    def __init__(self, base_url: str, timeout: float = 150.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def publish_camera_fact(self) -> dict[str, Any]:
        return self._request(
            "/v1/facts",
            {
                "key": "camera.healthy",
                "value": True,
                "source": "orchestrator-demo",
                "confidence": 1.0,
                "timestamp": time.time(),
                "ttl_ms": 1500,
                "evidence": {"mode": "deterministic-demo"},
            },
        )

    def submit_action(self, intent: dict[str, Any]) -> dict[str, Any]:
        return self._request("/v1/actions", intent)

    def get_work_order(self, work_order_id: str) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/v1/work-orders/{work_order_id}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Runtime HTTP {exc.code}: {detail}") from exc
        except (TimeoutError, URLError) as exc:
            raise ConnectionError(f"Runtime unavailable: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Runtime returned a non-object response")
        return value

    def get_readiness(self) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/readyz",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=min(self.timeout, 3.0)) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                value = json.loads(detail)
            except json.JSONDecodeError as parse_exc:
                raise RuntimeError(
                    f"Runtime readiness HTTP {exc.code}: {detail}"
                ) from parse_exc
        except (TimeoutError, URLError) as exc:
            raise ConnectionError(f"Runtime unavailable: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Runtime returned non-object readiness")
        return value

    def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Runtime HTTP {exc.code}: {detail}") from exc
        except (TimeoutError, URLError) as exc:
            raise ConnectionError(f"Runtime unavailable: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Runtime returned a non-object response")
        return value


class HttpUnsafeExecutorClient:
    """Explicit demo-only transport that bypasses Runtime and Guard."""

    def __init__(self, base_url: str, token: str, timeout: float = 150.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def submit_action(self, intent: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/legacy/v1/execute",
            data=json.dumps(intent).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Legacy-Demo-Token": self.token,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                receipt = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Legacy Bridge HTTP {exc.code}: {detail}") from exc
        except (TimeoutError, URLError) as exc:
            raise ConnectionError(f"Legacy Bridge unavailable: {exc}") from exc
        if not isinstance(receipt, dict):
            raise RuntimeError("Legacy Bridge returned a non-object response")
        succeeded = receipt.get("state") == "succeeded"
        return {
            "status": "ok" if succeeded else "error",
            "execution_mode": "unsafe-baseline",
            "decision": {
                "effect": "bypass",
                "reason_code": "SAFEEXEC_DISABLED",
                "lease_issued": False,
                "guard_reached": False,
            },
            # This compatibility envelope lets the Orchestrator consume the
            # same JOY receipt without pretending that Guard was reached.
            "guard_response": {
                "status": "executed" if succeeded else "failed",
                "bypassed": True,
                "execution": {
                    "status": "executed" if succeeded else "failed",
                    "receipt": receipt,
                },
            },
        }

    def get_readiness(self) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/healthz",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=min(self.timeout, 3.0)) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (HTTPError, TimeoutError, URLError) as exc:
            return {
                "status": "unavailable",
                "ready": False,
                "error": str(exc),
            }
        return {
            "status": "ready" if isinstance(value, dict) and value.get("ok") else "unavailable",
            "ready": bool(isinstance(value, dict) and value.get("ok")),
            "mode": "unsafe-baseline",
        }


class ReplayProvider:
    """Deterministic stand-in for a tool-calling Agent.

    Untrusted text influences the contaminated session only. A new clean
    session is always recreated from the trusted work order.
    """

    name = "replay-function-calling"

    def plan(
        self,
        sample_id: str,
        *,
        contaminated: bool,
        untrusted_input: str | None = None,
    ) -> ActionPlan:
        del untrusted_input
        return ActionPlan(
            sample_id,
            "cold-storage",
            "waste-bin" if contaminated else "analyzer-01",
        )


@dataclass
class Event:
    seq: int
    event: str
    at_ms: int
    source: str
    severity: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event": self.event,
            "at_ms": self.at_ms,
            "source": self.source,
            "severity": self.severity,
            "payload": json.loads(json.dumps(self.payload)),
        }


class LineOrchestrator:
    def __init__(
        self,
        runtime: RuntimeClient,
        *,
        provider: Any | None = None,
        job_compiler: Any | None = None,
        unsafe_executor: UnsafeExecutorClient | None = None,
        unsafe_demo_enabled: bool = False,
        unsafe_demo_token: str = "",
        fact_mode: str = "demo",
        recovery_delay: float = 1.5,
        recycle_delay: float = 1.0,
        testing_enabled: bool = True,
        require_trusted_work_order: bool = False,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if fact_mode not in {"demo", "external"}:
            raise ValueError("fact_mode must be demo or external")
        self.runtime = runtime
        self.provider = provider or ReplayProvider()
        self.job_compiler = job_compiler or DeterministicJobCompiler()
        self.unsafe_executor = unsafe_executor
        self.unsafe_demo_enabled = unsafe_demo_enabled
        self.unsafe_demo_token = unsafe_demo_token
        self.execution_mode = "protected"
        self.fact_mode = fact_mode
        self.recovery_delay = recovery_delay
        self.recycle_delay = max(0.0, recycle_delay)
        self.testing_enabled = testing_enabled
        self.require_trusted_work_order = require_trusted_work_order
        self._sleep = sleeper
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._worker: threading.Thread | None = None
        self._events: list[Event] = []
        self._seq = 0
        self._line_state = "STOPPED"
        self._current_task_id: str | None = None
        self._last_error: dict[str, Any] | None = None
        self._tasks: list[dict[str, Any]] = []
        self._task_history: list[dict[str, Any]] = []
        self._injections: dict[str, dict[str, Any]] = {}
        self._injection_by_task: dict[str, str] = {}
        self._operator_commands: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {}
        self._loop_stats: dict[str, int] = {}
        self._job_manifest: dict[str, Any] = {}
        self._physical_evidence: dict[str, Any] | None = None
        self._active_work_order_id: str | None = None
        self.continuous_mode = False
        self._pool_size = len(SAMPLE_IDS)
        self._cycle_index = 1
        self._cycle_recycled = 0
        self._rebuild_line()

    def _rebuild_line(
        self,
        sample_ids: tuple[str, ...] = SAMPLE_IDS,
        *,
        job_manifest: dict[str, Any] | None = None,
    ) -> None:
        if not sample_ids:
            raise ValueError("a production job must contain at least one sample")
        self._job_manifest = job_manifest or {
            "schema_version": "safeexec.job-manifest.v1",
            "job_id": "job-default-six-samples",
            "operator_text": "将全部六件样品送往分析区",
            "requested_by": "system-default",
            "sample_ids": list(sample_ids),
            "source": "cold-storage",
            "destination": "analyzer-01",
            "selection_strategy": "catalog-order",
            "provider": "system-default",
            "tool_call": {
                "name": "create_transfer_job",
                "arguments": {
                    "sample_ids": list(sample_ids),
                    "source": "cold-storage",
                    "destination": "analyzer-01",
                    "selection_strategy": "catalog-order",
                },
            },
            "created_at_ms": int(time.time() * 1000),
        }
        task_source = str(self._job_manifest.get("source", "cold-storage"))
        task_destination = str(
            self._job_manifest.get("destination", "analyzer-01")
        )
        self._cycle_index = 1
        self._cycle_recycled = 0
        self._pool_size = len(sample_ids)
        self._task_history = []
        self._tasks = [
            self._new_task(
                sample_id,
                task_source,
                task_destination,
                cycle_index=1,
            )
            for sample_id in sample_ids
        ]
        self._current_task_id = None
        self._last_error = None
        self._injections = {}
        self._injection_by_task = {}
        self._counters = {
            "completed_tasks": 0,
            "blocked_actions": 0,
            "recovered_tasks": 0,
            "unsafe_outcomes": 0,
        }
        self._loop_stats = {
            "completed_cycles": 0,
            "recycled_samples": 0,
        }

    @staticmethod
    def _new_task(
        sample_id: str,
        source: str,
        destination: str,
        *,
        cycle_index: int,
    ) -> dict[str, Any]:
        task_id = (
            f"task-{sample_id}"
            if cycle_index == 1
            else f"task-cycle-{cycle_index:04d}-{sample_id}"
        )
        return {
            "task_id": task_id,
            "sample_id": sample_id,
            "lot_id": f"LOT-{cycle_index:04d}-{sample_id[-1]}",
            "cycle_index": cycle_index,
            "source": source,
            "destination": destination,
            "status": "QUEUED",
            "attempt": 0,
            "session_id": None,
            "untrusted_input": None,
            "intent": None,
            "decision": None,
            "blocked_intent": None,
            "blocked_decision": None,
            "error": None,
        }

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": "ok",
                "line_state": self._line_state,
                "fact_mode": self.fact_mode,
                "execution_mode": self.execution_mode,
                "agent_provider": {
                    "job": getattr(self.job_compiler, "name", "unknown"),
                    "action": getattr(self.provider, "name", "unknown"),
                },
                "worker_alive": bool(self._worker and self._worker.is_alive()),
            }

    def preflight(self) -> dict[str, Any]:
        """Return the complete edge-to-device readiness chain."""
        with self._lock:
            execution_mode = self.execution_mode
            line_state = self._line_state
            has_work_order = self._active_work_order_id is not None
            requires_work_order = self.require_trusted_work_order
            worker_alive = bool(self._worker and self._worker.is_alive())
        blockers: list[dict[str, str]] = []
        runtime: dict[str, Any]
        try:
            runtime = self.runtime.get_readiness()
        except AttributeError:
            runtime = {
                "status": "ready",
                "ready": True,
                "test_double": True,
            }
        except (ConnectionError, RuntimeError) as exc:
            runtime = {
                "status": "unavailable",
                "ready": False,
                "error": str(exc),
            }

        execution_path_ready = runtime.get("ready") is True
        if execution_mode == "unsafe-baseline":
            if self.unsafe_executor is None:
                unsafe = {"status": "disabled", "ready": False}
            else:
                try:
                    unsafe = self.unsafe_executor.get_readiness()
                except AttributeError:
                    unsafe = {
                        "status": "ready",
                        "ready": True,
                        "test_double": True,
                    }
            execution_path_ready = unsafe.get("ready") is True
        else:
            unsafe = None

        if not execution_path_ready:
            blockers.append(
                {
                    "code": "EXECUTION_PATH_UNAVAILABLE",
                    "message": (
                        "Windows Guard 与 JOY 尚未同时就绪"
                        if execution_mode == "protected"
                        else "无保护演示执行端尚未就绪"
                    ),
                }
            )
        if requires_work_order and not has_work_order:
            blockers.append(
                {
                    "code": "WORK_ORDER_REQUIRED",
                    "message": "尚未激活已验证的可信工单",
                }
            )
        if line_state == "ERROR":
            blockers.append(
                {
                    "code": "LINE_ERROR",
                    "message": "存在未决故障；核对物理状态后执行签名复位",
                }
            )
        ready = not blockers
        connectivity = runtime.get("executor_connectivity")
        selected_guard = (
            connectivity.get("selected_guard")
            if isinstance(connectivity, Mapping)
            else None
        )
        return {
            "schema_version": "safeexec.preflight.v1",
            "status": "ready" if ready else "blocked",
            "ready": ready,
            "execution_mode": execution_mode,
            "line_state": line_state,
            "components": {
                "orchestrator": {
                    "status": "ready",
                    "ready": True,
                    "worker_alive": worker_alive,
                },
                "runtime": {
                    "status": runtime.get("status", "unknown"),
                    "ready": runtime.get("ready") is True,
                },
                "guard": {
                    "status": (
                        connectivity.get("status", "unknown")
                        if isinstance(connectivity, Mapping)
                        else "not-monitored"
                    ),
                    "ready": bool(
                        isinstance(connectivity, Mapping)
                        and connectivity.get("ready")
                    ),
                    "endpoint": (
                        connectivity.get("selected_endpoint")
                        if isinstance(connectivity, Mapping)
                        else None
                    ),
                },
                "joy": {
                    "status": (
                        "ready"
                        if isinstance(selected_guard, Mapping)
                        and selected_guard.get("executor", {}).get("ready")
                        else "unavailable"
                    ),
                    "ready": bool(
                        isinstance(selected_guard, Mapping)
                        and selected_guard.get("executor", {}).get("ready")
                    ),
                    "executor": (
                        selected_guard.get("executor")
                        if isinstance(selected_guard, Mapping)
                        else None
                    ),
                },
            },
            "runtime_readiness": runtime,
            "unsafe_readiness": unsafe,
            "work_order": {
                "required": requires_work_order,
                "active": has_work_order,
                "work_order_id": self._active_work_order_id,
            },
            "blockers": blockers,
            "auto_execute": False,
            "requires_operator_start": True,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            current = self._find_task(self._current_task_id)
            return {
                "schema_version": "safeexec.line-state.v1",
                "line_id": "biolab-line-01",
                "line_state": self._line_state,
                "current_task_id": self._current_task_id,
                "current_session": (
                    {
                        "session_id": current.get("session_id"),
                        "task_id": current["task_id"],
                        "contaminated": bool(current.get("untrusted_input")),
                    }
                    if current
                    else None
                ),
                "job_manifest": json.loads(json.dumps(self._job_manifest)),
                "active_work_order_id": self._active_work_order_id,
                "tasks": json.loads(json.dumps(self._tasks)),
                "recent_tasks": json.loads(json.dumps(self._task_history[-24:])),
                "counters": dict(self._counters),
                "loop_stats": dict(self._loop_stats),
                "continuous_mode": self.continuous_mode,
                "entity_pool": {
                    "strategy": "batch-turnover",
                    "size": self._pool_size,
                    "active_cycle": self._cycle_index,
                    "recycled_in_cycle": self._cycle_recycled,
                    "processed_in_cycle": sum(
                        item["status"] == "COMPLETED"
                        for item in self._tasks
                    ),
                },
                "physical_evidence": json.loads(
                    json.dumps(self._physical_evidence)
                ),
                "last_error": json.loads(json.dumps(self._last_error)),
                "last_seq": self._seq,
                "fact_mode": self.fact_mode,
                "agent_provider": {
                    "job": getattr(self.job_compiler, "name", "unknown"),
                    "action": getattr(self.provider, "name", "unknown"),
                },
                "execution_mode": self.execution_mode,
                "execution_modes": {
                    "protected": {"available": True},
                    "unsafe-baseline": {
                        "available": bool(
                            self.unsafe_demo_enabled and self.unsafe_executor
                        ),
                        "demo_only": True,
                    },
                },
                "controls": self._controls(),
            }

    def _controls(self) -> dict[str, bool]:
        state = self._line_state
        return {
            "start": state == "STOPPED"
            and (
                not self.require_trusted_work_order
                or self._active_work_order_id is not None
            ),
            "pause": state == "RUNNING",
            "resume": state == "PAUSED",
            "reset": state in {"STOPPED", "PAUSED", "COMPLETED", "ERROR"},
            "configure": state == "STOPPED",
            "inject": state in {"STOPPED", "RUNNING", "PAUSE_PENDING", "PAUSED"},
            "mode": state == "STOPPED"
            and all(task["status"] == "QUEUED" for task in self._tasks)
            and not any(self._counters.values()),
            "continuous": state == "STOPPED"
            and all(task["status"] == "QUEUED" for task in self._tasks)
            and not any(self._counters.values()),
        }

    def set_continuous_mode(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            if not self._controls()["continuous"]:
                raise ConflictError(
                    "continuous mode can change only on a stopped, reset line"
                )
            self.continuous_mode = enabled
            self._emit(
                "line.continuous_mode.changed",
                "orchestrator",
                {
                    "enabled": enabled,
                    "entity_pool_size": self._pool_size,
                },
            )
            return self.snapshot()

    def set_execution_mode(self, mode: str, provided_token: str = "") -> dict[str, Any]:
        if mode not in EXECUTION_MODES:
            raise ValidationError("execution mode must be protected or unsafe-baseline")
        with self._lock:
            if not self._controls()["mode"]:
                raise ConflictError(
                    "execution mode can change only on a stopped, reset line"
                )
            if mode == "unsafe-baseline":
                if not self.unsafe_demo_enabled or self.unsafe_executor is None:
                    raise NotFoundError("unsafe baseline is disabled")
                if not self.unsafe_demo_token or not hmac.compare_digest(
                    provided_token,
                    self.unsafe_demo_token,
                ):
                    raise AuthorizationError("invalid unsafe demo token")
            previous = self.execution_mode
            self.execution_mode = mode
            self._emit(
                "execution.mode.changed",
                "orchestrator",
                {
                    "previous": previous,
                    "current": mode,
                    "safeexec_enabled": mode == "protected",
                },
                "critical" if mode == "unsafe-baseline" else "info",
            )
            return self.snapshot()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._line_state != "STOPPED":
                raise ConflictError(f"cannot start from {self._line_state}")
            if self.require_trusted_work_order and self._active_work_order_id is None:
                raise ConflictError(
                    "no verified WorkOrder is active; create one in /config first"
                )
            preflight = self.preflight()
            if not preflight["ready"]:
                codes = ", ".join(item["code"] for item in preflight["blockers"])
                raise ConflictError(f"preflight blocked: {codes}")
            self._line_state = "RUNNING"
            self._emit(
                "line.started",
                "orchestrator",
                {
                    "task_count": len(self._tasks),
                    "job_id": self._job_manifest["job_id"],
                    "execution_mode": self.execution_mode,
                    "continuous_mode": self.continuous_mode,
                },
            )
            self._ensure_worker()
            return self.snapshot()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self._line_state != "RUNNING":
                raise ConflictError(f"cannot pause from {self._line_state}")
            if self._current_task_id is None:
                self._line_state = "PAUSED"
                self._emit("line.paused", "orchestrator", {"after_task": None})
            else:
                self._line_state = "PAUSE_PENDING"
                self._emit(
                    "line.pause_pending",
                    "orchestrator",
                    {"after_task": self._current_task_id},
                )
            return self.snapshot()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if self._line_state != "PAUSED":
                raise ConflictError(f"cannot resume from {self._line_state}")
            self._line_state = "RUNNING"
            self._emit("line.resumed", "orchestrator", {})
            self._ensure_worker()
            return self.snapshot()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            if self._line_state not in {"STOPPED", "PAUSED", "COMPLETED", "ERROR"}:
                raise ConflictError(f"cannot reset from {self._line_state}")
        result = self.runtime.submit_action(build_reset_intent())
        self._assert_execution_succeeded(result, action="lab.line.reset")
        with self._lock:
            self._line_state = "STOPPED"
            self.continuous_mode = False
            self._active_work_order_id = None
            current_manifest = json.loads(json.dumps(self._job_manifest))
            current_samples = tuple(current_manifest.get("sample_ids", SAMPLE_IDS))
            self._rebuild_line(
                current_samples,
                job_manifest=current_manifest,
            )
            self._record_physical_from_response(result, fallback_dock="home")
            self._emit(
                "line.reset",
                "orchestrator",
                {"path": "Policy→Lease→Guard→JOY"},
            )
            return self.snapshot()

    def activate_work_order(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {"work_order_id"}:
            raise ValidationError(
                "WorkOrder activation expects exactly work_order_id"
            )
        try:
            work_order_id = str(uuid.UUID(str(value["work_order_id"])))
        except (TypeError, ValueError) as exc:
            raise ValidationError("work_order_id must be a UUID") from exc
        with self._lock:
            if self._line_state != "STOPPED":
                raise ConflictError("WorkOrder can be activated only while stopped")
            if any(task["status"] != "QUEUED" for task in self._tasks):
                raise ConflictError("reset the line before activating a WorkOrder")

        response = self.runtime.get_work_order(work_order_id)
        order = response.get("work_order")
        if not isinstance(order, dict):
            raise ValidationError("Runtime did not return a verified WorkOrder")
        verification = order.get("verification")
        if not isinstance(verification, dict) or not all(
            verification.get(key) is True
            for key in (
                "issuer_trusted",
                "within_organization_policy",
            )
        ) or verification.get("signature") != "verified":
            raise ValidationError("Runtime has not verified this WorkOrder")
        if order.get("subject_principal_id") != "lab-agent-01":
            raise ValidationError("WorkOrder subject is not the Lab Agent")

        transfer_grants = [
            grant
            for grant in order.get("grants", [])
            if isinstance(grant, dict)
            and grant.get("action") == "lab.sample.transfer"
        ]
        if not transfer_grants:
            raise ValidationError("WorkOrder contains no Lab transfer grants")
        sample_ids = tuple(
            str(grant.get("resource", {}).get("id"))
            for grant in transfer_grants
        )
        if len(sample_ids) != len(set(sample_ids)) or any(
            sample_id not in SAMPLE_IDS for sample_id in sample_ids
        ):
            raise ValidationError("WorkOrder sample grants are invalid or duplicated")
        routes = {
            (
                grant.get("arguments", {}).get("source"),
                grant.get("arguments", {}).get("destination"),
            )
            for grant in transfer_grants
        }
        if len(routes) != 1:
            raise ValidationError("Lab demo requires one shared WorkOrder route")
        source, destination = next(iter(routes))
        manifest = {
            "schema_version": "safeexec.job-manifest.v2",
            "job_id": str(uuid.uuid4()),
            "work_order_id": work_order_id,
            "operator_text": order.get("operator_note") or "执行已签名可信工单",
            "requested_by": order.get("issuer_id"),
            "sample_ids": list(sample_ids),
            "source": source,
            "destination": destination,
            "selection_strategy": "signed-work-order-order",
            "provider": "safeexec-control-plane",
            "tool_call": None,
            "created_at_ms": int(time.time() * 1000),
            "valid_until_ms": order.get("valid_until_ms"),
            "max_executions_per_sample": min(
                int(grant.get("max_executions", 1))
                for grant in transfer_grants
            ),
        }
        with self._lock:
            self._active_work_order_id = work_order_id
            self._rebuild_line(sample_ids, job_manifest=manifest)
            self._emit(
                "work_order.activated",
                "orchestrator",
                {
                    "work_order_id": work_order_id,
                    "issuer_id": order.get("issuer_id"),
                    "sample_ids": list(sample_ids),
                    "valid_until_ms": order.get("valid_until_ms"),
                },
            )
            return {
                "status": "activated",
                "work_order": json.loads(json.dumps(order)),
                "line": self.snapshot(),
            }

    def compile_operator_command(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if self.require_trusted_work_order:
            raise ConflictError(
                "natural language cannot create trusted authorization; use /config"
            )
        command = self._validate_operator_command(value)
        command_id = command["command_id"]
        with self._lock:
            existing = self._operator_commands.get(command_id)
            if existing is not None:
                if existing["request"] != command:
                    raise ConflictError(
                        "command_id already exists with different content"
                    )
                return json.loads(json.dumps(existing["response"]))
            if self._line_state != "STOPPED":
                raise ConflictError(
                    "a new trusted job can be compiled only while the line is stopped"
                )

        compiled = self.job_compiler.compile(
            command["text"],
            available_samples=SAMPLE_IDS,
        )
        now_ms = int(time.time() * 1000)
        manifest = {
            "schema_version": "safeexec.job-manifest.v1",
            "job_id": str(uuid.uuid4()),
            "operator_text": command["text"],
            "requested_by": command["requested_by"],
            "sample_ids": list(compiled.sample_ids),
            "source": compiled.source,
            "destination": compiled.destination,
            "selection_strategy": compiled.selection_strategy,
            "provider": compiled.provider,
            "tool_call": compiled.tool_call,
            "created_at_ms": now_ms,
        }
        with self._lock:
            if self._line_state != "STOPPED":
                raise ConflictError("line state changed while compiling the job")
            self._rebuild_line(compiled.sample_ids, job_manifest=manifest)
            self._emit(
                "job.compiled",
                "agent",
                {
                    "job_id": manifest["job_id"],
                    "command_id": command_id,
                    "sample_ids": list(compiled.sample_ids),
                    "selection_strategy": compiled.selection_strategy,
                    "provider": compiled.provider,
                },
            )
            response = {
                "status": "compiled",
                "job_manifest": json.loads(json.dumps(manifest)),
                "line": self.snapshot(),
            }
            self._operator_commands[command_id] = {
                "request": command,
                "response": response,
            }
            return json.loads(json.dumps(response))

    @staticmethod
    def _validate_operator_command(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != OPERATOR_COMMAND_FIELDS:
            raise ValidationError(
                "operator command must contain exactly "
                f"{sorted(OPERATOR_COMMAND_FIELDS)}"
            )
        if value.get("schema_version") != "safeexec.operator-command.v1":
            raise ValidationError("unsupported operator command schema")
        try:
            uuid.UUID(str(value.get("command_id")))
        except (TypeError, ValueError) as exc:
            raise ValidationError("command_id must be a UUID") from exc
        text = value.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
            raise ValidationError("text must contain 1-1000 characters")
        requested_by = value.get("requested_by")
        if (
            not isinstance(requested_by, str)
            or not 1 <= len(requested_by.strip()) <= 128
        ):
            raise ValidationError("requested_by must contain 1-128 characters")
        submitted = value.get("submitted_at_ms")
        if isinstance(submitted, bool) or not isinstance(submitted, int) or submitted < 0:
            raise ValidationError("submitted_at_ms must be a non-negative integer")
        return json.loads(json.dumps(dict(value)))

    def register_injection(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if not self.testing_enabled:
            raise NotFoundError("testing injection endpoint is disabled")
        injection = self._validate_injection(value)
        injection_id = injection["injection_id"]
        with self._lock:
            existing = self._injections.get(injection_id)
            if existing is not None:
                if existing["request"] != injection:
                    raise ConflictError("injection_id already exists with other content")
                return self._injection_response(existing)
            if injection["target_task_id"] == "next-queued":
                task = next(
                    (
                        item
                        for item in self._tasks
                        if item["status"] == "QUEUED"
                        and not item.get("untrusted_input")
                    ),
                    None,
                )
            else:
                task = self._find_task(injection["target_task_id"])
            if task is None:
                raise ConflictError("no queued task is available for injection")
            if task["status"] != "QUEUED":
                raise ConflictError("target task is no longer queued")
            if task["task_id"] in self._injection_by_task:
                raise ConflictError("target task already has an injection")
            record = {
                "request": injection,
                "resolved_target_task_id": task["task_id"],
                "state": "queued",
                "created_at_ms": int(time.time() * 1000),
                "updated_at_ms": int(time.time() * 1000),
                "result": None,
            }
            self._injections[injection_id] = record
            self._injection_by_task[task["task_id"]] = injection_id
            task["untrusted_input"] = injection["untrusted_content"]
            self._emit(
                "attack.injected",
                "attacklab",
                {
                    "injection_id": injection_id,
                    "task_id": task["task_id"],
                    "channel": injection["channel"],
                },
                "warning",
            )
            return self._injection_response(record)

    def get_injection(self, injection_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._injections.get(injection_id)
            if record is None:
                raise NotFoundError("injection not found")
            return self._injection_response(record)

    def _injection_response(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "accepted": True,
            "injection_id": record["request"]["injection_id"],
            "state": record["state"],
            "target_task_id": record["resolved_target_task_id"],
            "result": json.loads(json.dumps(record["result"])),
        }

    @staticmethod
    def _validate_injection(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValidationError("injection must be an object")
        keys = set(value)
        unknown = keys - INJECTION_FIELDS
        missing = INJECTION_REQUIRED - keys
        if unknown or missing:
            raise ValidationError(
                f"invalid fields; missing={sorted(missing)}, extra={sorted(unknown)}"
            )
        if value.get("schema_version") != "safeexec.attack-injection.v1":
            raise ValidationError("unsupported schema_version")
        try:
            uuid.UUID(str(value.get("injection_id")))
        except (TypeError, ValueError) as exc:
            raise ValidationError("injection_id must be a UUID") from exc
        attack_id = value.get("attack_id")
        if (
            not isinstance(attack_id, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", attack_id) is None
        ):
            raise ValidationError("invalid attack_id")
        if value.get("channel") not in INJECTION_CHANNELS:
            raise ValidationError("invalid channel")
        target_task_id = value.get("target_task_id")
        if (
            not isinstance(target_task_id, str)
            or not 1 <= len(target_task_id) <= 128
        ):
            raise ValidationError("target_task_id must be a string")
        content = value.get("untrusted_content")
        if not isinstance(content, str) or len(content) > 4000:
            raise ValidationError("invalid untrusted_content")
        requested = value.get("requested_at_ms")
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 0:
            raise ValidationError("requested_at_ms must be a non-negative integer")
        claims = value.get("actor_claims")
        if claims is not None:
            if not isinstance(claims, Mapping) or set(claims) - {
                "claimed_role",
                "claimed_identity",
            }:
                raise ValidationError("invalid actor_claims")
            if claims.get("claimed_role") not in {
                None,
                "visitor",
                "operator",
                "lab_admin",
                "system",
            }:
                raise ValidationError("invalid claimed_role")
            identity = claims.get("claimed_identity")
            if identity is not None and (
                not isinstance(identity, str) or len(identity) > 128
            ):
                raise ValidationError("invalid claimed_identity")
        return json.loads(json.dumps(dict(value)))

    def events_after(self, after: int = 0) -> dict[str, Any]:
        with self._lock:
            return {
                "events": [event.to_dict() for event in self._events if event.seq > after],
                "last_seq": self._seq,
            }

    def wait_for_events(
        self, after: int, timeout: float = 10.0
    ) -> dict[str, Any]:
        with self._condition:
            if self._seq <= after:
                self._condition.wait(timeout)
            return self.events_after(after)

    def wait_until_terminal(self, timeout: float = 10.0) -> str:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._line_state not in {"COMPLETED", "ERROR", "PAUSED"}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("line did not reach a terminal state")
                self._condition.wait(remaining)
            return self._line_state

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._run,
                name="safeexec-line-orchestrator",
                daemon=True,
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._line_state not in {"RUNNING", "PAUSE_PENDING"}:
                    return
                task = next(
                    (item for item in self._tasks if item["status"] == "QUEUED"),
                    None,
                )
                batch_ready = bool(
                    task is None
                    and self.continuous_mode
                    and self._tasks
                    and all(
                        item["status"] == "COMPLETED"
                        for item in self._tasks
                    )
                )
                batch_anchor = self._tasks[-1] if batch_ready else None
                if task is None:
                    if batch_ready:
                        self._current_task_id = None
                    else:
                        self._line_state = "COMPLETED"
                        self._emit(
                            "line.completed",
                            "orchestrator",
                            {
                                "job_id": self._job_manifest["job_id"],
                                "task_count": len(self._tasks),
                                "counters": dict(self._counters),
                            },
                        )
                        return
                else:
                    self._current_task_id = task["task_id"]
            if batch_ready:
                try:
                    self._refresh_completed_batch()
                except ExecutionOutcomeUncertainError as exc:
                    assert batch_anchor is not None
                    self._fail_task(
                        batch_anchor,
                        "BATCH_REFRESH_OUTCOME_UNKNOWN",
                        str(exc),
                    )
                    return
                except Exception as exc:
                    assert batch_anchor is not None
                    self._fail_task(
                        batch_anchor,
                        "BATCH_REFRESH_FAILURE",
                        str(exc),
                    )
                    return
                continue
            assert task is not None
            try:
                self._process_task(task)
            except ExecutionOutcomeUncertainError as exc:
                self._fail_task(
                    task,
                    "EXECUTION_OUTCOME_UNKNOWN",
                    str(exc),
                )
                return
            except Exception as exc:
                self._fail_task(task, "ORCHESTRATOR_FAILURE", str(exc))
                return
            with self._lock:
                self._current_task_id = None
                if self._line_state == "ERROR":
                    return
                if self._line_state == "PAUSE_PENDING":
                    self._line_state = "PAUSED"
                    self._emit(
                        "line.paused",
                        "orchestrator",
                        {"after_task": task["task_id"]},
                    )
                    return

    def _refresh_completed_batch(self) -> None:
        """Retire a complete batch atomically, then queue its successor."""

        with self._lock:
            completed_batch = [
                item for item in self._tasks
                if item["status"] == "COMPLETED"
            ]
            if (
                not completed_batch
                or len(completed_batch) != len(self._tasks)
            ):
                raise RuntimeError(
                    "batch refresh requires every active task to be completed"
                )
            completed_cycle = self._cycle_index
            self._emit(
                "line.batch.completed",
                "orchestrator",
                {
                    "cycle_index": completed_cycle,
                    "sample_count": len(completed_batch),
                    "completed_tasks": self._counters["completed_tasks"],
                    "hold_ms": int(self.recycle_delay * 1000),
                },
            )
        if self.recycle_delay:
            self._sleep(self.recycle_delay)
        with self._lock:
            self._emit(
                "line.batch.refreshing",
                "orchestrator",
                {
                    "cycle_index": completed_cycle,
                    "sample_ids": [
                        item["sample_id"] for item in completed_batch
                    ],
                    "path": "Policy→Lease→Guard→JOY",
                },
            )
        result = self.runtime.submit_action(build_reset_intent())
        self._assert_execution_succeeded(result, action="lab.line.reset")
        with self._lock:
            self._record_physical_from_response(result, fallback_dock="home")
            retired_at_ms = int(time.time() * 1000)
            for item in completed_batch:
                completed = json.loads(json.dumps(item))
                completed["retired_at_ms"] = retired_at_ms
                completed["retired_with_batch"] = completed_cycle
                self._task_history.append(completed)
                self._injection_by_task.pop(str(item["task_id"]), None)
            if len(self._task_history) > 48:
                self._task_history = self._task_history[-48:]

            self._loop_stats["recycled_samples"] += len(completed_batch)
            self._loop_stats["completed_cycles"] += 1
            self._cycle_index += 1
            self._cycle_recycled = 0
            next_tasks = [
                self._new_task(
                    str(item["sample_id"]),
                    str(item["source"]),
                    str(item["destination"]),
                    cycle_index=self._cycle_index,
                )
                for item in completed_batch
            ]
            self._tasks = next_tasks
            self._emit(
                "line.batch.refreshed",
                "guard",
                {
                    "completed_cycle": completed_cycle,
                    "next_cycle": self._cycle_index,
                    "sample_count": len(next_tasks),
                    "destination": "cold-storage",
                    "path": "Policy→Lease→Guard→JOY",
                },
            )
            self._emit(
                "line.cycle.completed",
                "orchestrator",
                {
                    "cycle_index": completed_cycle,
                    "completed_tasks": self._counters["completed_tasks"],
                    "unsafe_outcomes": self._counters["unsafe_outcomes"],
                },
            )
            for next_task in next_tasks:
                self._emit(
                    "task.queued",
                    "orchestrator",
                    {
                        "task_id": next_task["task_id"],
                        "lot_id": next_task["lot_id"],
                        "sample_id": next_task["sample_id"],
                        "cycle_index": next_task["cycle_index"],
                    },
                )

    def _process_task(self, task: dict[str, Any]) -> None:
        injection_id = self._injection_by_task.get(task["task_id"])
        contaminated = injection_id is not None
        response = self._attempt(task, contaminated=contaminated)
        reason = self._deny_reason(response)
        if reason is not None:
            if not contaminated or reason not in {
                "NO_MATCHING_GRANT",
                "AGENT_OUTPUT_SCOPE_VIOLATION",
            }:
                self._fail_task(task, reason, "SafeExec denied without recoverable injection")
                return
            self._handle_registered_block(task, injection_id, response)
            return
        self._complete_task(task, response)

    def _attempt(
        self, task: dict[str, Any], *, contaminated: bool
    ) -> dict[str, Any]:
        with self._lock:
            task["attempt"] += 1
            task["status"] = "PLANNING"
            task["session_id"] = str(uuid.uuid4())
            session_id = task["session_id"]
            self._emit(
                "agent.session.created",
                "agent",
                {
                    "session_id": session_id,
                    "task_id": task["task_id"],
                    "contaminated": contaminated,
                },
                "warning" if contaminated else "info",
            )
            self._emit(
                "task.planning",
                "agent",
                {"task_id": task["task_id"], "attempt": task["attempt"]},
            )
        plan = self.provider.plan(
            task["sample_id"],
            contaminated=contaminated,
            untrusted_input=task.get("untrusted_input") if contaminated else None,
        )
        intent = build_action_intent(
            plan,
            work_order_id=self._active_work_order_id,
        )
        with self._lock:
            task["intent"] = intent
            task["status"] = "SUBMITTED"
            self._emit(
                "intent.proposed",
                "agent",
                {
                    "task_id": task["task_id"],
                    "session_id": session_id,
                    "sample_id": plan.sample_id,
                    "source": plan.source,
                    "destination": plan.destination,
                },
                "warning" if contaminated else "info",
            )
            self._emit(
                "task.submitted",
                "orchestrator",
                {"task_id": task["task_id"], "request_id": intent["request_id"]},
            )
        if plan.sample_id != task["sample_id"]:
            response = {
                "status": "denied",
                "decision": {
                    "schema_version": "safeexec.decision.v1",
                    "decision_id": str(uuid.uuid4()),
                    "request_id": intent["request_id"],
                    "effect": "deny",
                    "reason_code": "AGENT_OUTPUT_SCOPE_VIOLATION",
                    "matched_grant_id": None,
                    "evaluated_at_ms": int(time.time() * 1000),
                    "fact_refs": [],
                },
            }
            with self._lock:
                task["decision"] = response["decision"]
                self._emit(
                    "agent.output.scope_violation",
                    "safeexec_scope",
                    {
                        "task_id": task["task_id"],
                        "expected_sample_id": task["sample_id"],
                        "proposed_sample_id": plan.sample_id,
                        "request_id": intent["request_id"],
                        "runtime_reached": False,
                        "guard_reached": False,
                    },
                    "critical",
                )
            return response
        if (
            self.execution_mode == "protected"
            and self.fact_mode == "demo"
            and not contaminated
        ):
            fact_result = self.runtime.publish_camera_fact()
            if fact_result.get("status") != "ok":
                raise RuntimeError(f"demo Fact rejected: {fact_result!r}")
        if self.execution_mode == "protected":
            response = self.runtime.submit_action(intent)
            execution_source = "guard"
        else:
            if self.unsafe_executor is None:
                raise RuntimeError("unsafe baseline executor is unavailable")
            response = self.unsafe_executor.submit_action(intent)
            execution_source = "legacy_bridge"
        with self._lock:
            task["decision"] = response.get("decision")
        if self._deny_reason(response) is None:
            with self._lock:
                task["status"] = "EXECUTING"
                self._emit(
                    "task.executing",
                    execution_source,
                    {
                        "task_id": task["task_id"],
                        "request_id": intent["request_id"],
                        "execution_mode": self.execution_mode,
                    },
                    "critical" if self.execution_mode == "unsafe-baseline" else "info",
                )
        return response

    def _handle_registered_block(
        self,
        task: dict[str, Any],
        injection_id: str | None,
        response: dict[str, Any],
    ) -> None:
        assert injection_id is not None
        reason_code = self._deny_reason(response) or "UNKNOWN_DENIAL"
        with self._lock:
            task["status"] = "BLOCKED"
            task["blocked_intent"] = json.loads(json.dumps(task["intent"]))
            task["blocked_decision"] = json.loads(
                json.dumps(response.get("decision"))
            )
            self._counters["blocked_actions"] += 1
            self._line_state = "RECOVERING"
            record = self._injections[injection_id]
            record["state"] = "blocked"
            record["updated_at_ms"] = int(time.time() * 1000)
            record["result"] = {
                "effect": "deny",
                "reason_code": reason_code,
                "lease_issued": False,
                "guard_reached": False,
            }
            session_id = task["session_id"]
            self._emit(
                "task.blocked",
                (
                    "safeexec_scope"
                    if reason_code == "AGENT_OUTPUT_SCOPE_VIOLATION"
                    else "runtime"
                ),
                {
                    "task_id": task["task_id"],
                    "reason_code": reason_code,
                    "lease_issued": False,
                    "guard_reached": False,
                },
                "warning",
            )
            self._emit(
                "agent.session.compromised",
                "agent",
                {"session_id": session_id, "task_id": task["task_id"]},
                "warning",
            )
            self._emit(
                "agent.session.terminated",
                "orchestrator",
                {"session_id": session_id, "reason": "prompt_injection"},
                "warning",
            )
            task["status"] = "RECOVERING"
            self._emit(
                "task.recovering",
                "orchestrator",
                {
                    "task_id": task["task_id"],
                    "strategy": "destroy_session_and_replan_from_trusted_work_order",
                    "hold_ms": int(self.recovery_delay * 1000),
                },
                "warning",
            )
        self._sleep(self.recovery_delay)
        retry = self._attempt(task, contaminated=False)
        reason = self._deny_reason(retry)
        if reason is not None:
            self._fail_task(task, reason, "clean recovery attempt was denied")
            return
        try:
            self._complete_task(task, retry, recovered=True)
        except Exception:
            raise
        with self._lock:
            record = self._injections[injection_id]
            record["state"] = "recovered"
            record["updated_at_ms"] = int(time.time() * 1000)
            record["result"]["recovery"] = {
                "state": "succeeded",
                "final_destination": "analyzer-01",
            }
            if self._line_state != "ERROR":
                self._line_state = "RUNNING"

    def _complete_task(
        self,
        task: dict[str, Any],
        response: dict[str, Any],
        *,
        recovered: bool = False,
    ) -> None:
        self._assert_execution_succeeded(response, action="lab.sample.transfer")
        unsafe = self._unsafe_outcome(response)
        with self._lock:
            task["error"] = None
            self._counters["unsafe_outcomes"] += int(unsafe)
            self._record_physical_from_response(
                response,
                fallback_dock=task["destination"],
            )
            if unsafe:
                task["status"] = "FAILED"
                task["error"] = {
                    "code": "UNSAFE_PHYSICAL_OUTCOME",
                    "detail": "unprotected Agent action reached the physical executor",
                }
                injection_id = self._injection_by_task.get(task["task_id"])
                if injection_id is not None:
                    record = self._injections[injection_id]
                    record["state"] = "executed"
                    record["updated_at_ms"] = int(time.time() * 1000)
                    record["result"] = {
                        "effect": "bypass",
                        "reason_code": "SAFEEXEC_DISABLED",
                        "lease_issued": False,
                        "guard_reached": False,
                        "unsafe_outcome": True,
                    }
                self._emit(
                    "unsafe.action.executed",
                    "legacy_bridge",
                    {
                        "task_id": task["task_id"],
                        "sample_id": task["sample_id"],
                        "destination": "waste-bin",
                        "reason_code": "SAFEEXEC_DISABLED",
                    },
                    "critical",
                )
                self._line_state = "ERROR"
                self._last_error = {
                    "code": "UNSAFE_PHYSICAL_OUTCOME",
                    "task_id": task["task_id"],
                }
                self._emit(
                    "line.error",
                    "orchestrator",
                    self._last_error,
                    "critical",
                )
                return

            task["status"] = "COMPLETED"
            self._counters["completed_tasks"] += 1
            if recovered:
                self._counters["recovered_tasks"] += 1
                self._emit(
                    "agent.session.recovered",
                    "orchestrator",
                    {
                        "task_id": task["task_id"],
                        "session_id": task["session_id"],
                    },
                )
            self._emit(
                "task.completed",
                "joy",
                {
                    "task_id": task["task_id"],
                    "sample_id": task["sample_id"],
                    "destination": task["intent"]["arguments"]["destination"],
                    "recovered": recovered,
                    "unsafe_outcome": unsafe,
                },
            )

    @staticmethod
    def _execution_result(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
        guard = response.get("guard_response")
        if not isinstance(guard, Mapping):
            return None
        execution = guard.get("execution")
        if not isinstance(execution, Mapping):
            return None
        receipt = execution.get("receipt")
        if not isinstance(receipt, Mapping):
            return None
        result = receipt.get("result")
        return result if isinstance(result, Mapping) else None

    def _record_physical_from_response(
        self,
        response: Mapping[str, Any],
        *,
        fallback_dock: str,
    ) -> None:
        result = self._execution_result(response)
        if result is None:
            return
        existing_locations: dict[str, str] = {}
        if isinstance(self._physical_evidence, Mapping):
            previous = self._physical_evidence.get("sample_locations")
            if isinstance(previous, Mapping):
                existing_locations = {
                    str(key): str(location)
                    for key, location in previous.items()
                    if key in SAMPLE_IDS
                }
        locations = result.get("sample_locations")
        if isinstance(locations, Mapping):
            existing_locations.update(
                {
                    str(key): str(location)
                    for key, location in locations.items()
                    if key in SAMPLE_IDS
                }
            )
        elif result.get("reset") is True:
            existing_locations = {
                sample_id: "cold-storage" for sample_id in SAMPLE_IDS
            }
        sample_id = result.get("sample_id")
        location = result.get("location")
        if sample_id in SAMPLE_IDS and isinstance(location, str):
            existing_locations[str(sample_id)] = location
        self._physical_evidence = {
            "source": "joy-execution-receipt",
            "confirmed_at_ms": int(time.time() * 1000),
            "arm_state": str(result.get("arm_state", "IDLE")),
            "platform_state": str(result.get("platform_state", "IDLE")),
            "current_dock": str(result.get("current_dock", fallback_dock)),
            "sample_locations": existing_locations,
            "unsafe_outcome": bool(result.get("unsafe_outcome", False)),
        }

    @staticmethod
    def _deny_reason(response: Mapping[str, Any]) -> str | None:
        if response.get("status") != "denied":
            return None
        decision = response.get("decision")
        if isinstance(decision, Mapping):
            return str(decision.get("reason_code") or "UNKNOWN_DENIAL")
        return "UNKNOWN_DENIAL"

    @staticmethod
    def _assert_execution_succeeded(
        response: Mapping[str, Any], *, action: str
    ) -> None:
        if response.get("status") != "ok":
            raise RuntimeError(f"{action} was not allowed: {response!r}")
        guard = response.get("guard_response")
        if isinstance(guard, Mapping) and guard.get("status") == "uncertain":
            raise ExecutionOutcomeUncertainError(
                f"{action} may have reached the physical executor; "
                "operator reconciliation and signed reset are required"
            )
        if not isinstance(guard, Mapping) or guard.get("status") != "executed":
            raise RuntimeError(f"{action} was not executed by Guard: {guard!r}")
        execution = guard.get("execution")
        if isinstance(execution, Mapping) and execution.get("status") == "failed":
            raise RuntimeError(f"{action} executor failed: {execution!r}")

    @staticmethod
    def _unsafe_outcome(response: Mapping[str, Any]) -> bool:
        guard = response.get("guard_response")
        if not isinstance(guard, Mapping):
            return False
        execution = guard.get("execution")
        if not isinstance(execution, Mapping):
            return False
        receipt = execution.get("receipt")
        if isinstance(receipt, Mapping):
            result = receipt.get("result")
            return bool(isinstance(result, Mapping) and result.get("unsafe_outcome"))
        result = execution.get("result")
        return bool(isinstance(result, Mapping) and result.get("unsafe_outcome"))

    def _fail_task(self, task: dict[str, Any], code: str, detail: str) -> None:
        with self._lock:
            task["status"] = "FAILED"
            task["error"] = {"code": code, "detail": detail}
            self._line_state = "ERROR"
            self._current_task_id = task["task_id"]
            self._last_error = {
                "code": code,
                "detail": detail,
                "task_id": task["task_id"],
            }
            self._emit("task.failed", "orchestrator", self._last_error, "critical")
            self._emit("line.error", "orchestrator", self._last_error, "critical")

    def _find_task(self, task_id: str | None) -> dict[str, Any] | None:
        if task_id is None:
            return None
        return next((task for task in self._tasks if task["task_id"] == task_id), None)

    def _emit(
        self,
        event: str,
        source: str,
        payload: dict[str, Any],
        severity: str = "info",
    ) -> Event:
        self._seq += 1
        value = Event(
            seq=self._seq,
            event=event,
            at_ms=int(time.time() * 1000),
            source=source,
            severity=severity,
            payload=json.loads(json.dumps(payload)),
        )
        self._events.append(value)
        self._condition.notify_all()
        return value


class OrchestratorError(RuntimeError):
    status = 500


class ValidationError(OrchestratorError):
    status = 422


class ConflictError(OrchestratorError):
    status = 409


class NotFoundError(OrchestratorError):
    status = 404


class AuthorizationError(OrchestratorError):
    status = 401


class ExecutionOutcomeUncertainError(OrchestratorError):
    status = 503
