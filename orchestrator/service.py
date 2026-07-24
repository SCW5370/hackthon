"""Autonomous six-task production line with fail-closed recovery."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import threading
import time
import uuid
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from biolab.catalog import SAMPLE_IDS
from lab_agent.contracts import ActionPlan, build_action_intent, build_reset_intent


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


class RuntimeClient(Protocol):
    def publish_camera_fact(self) -> dict[str, Any]: ...

    def submit_action(self, intent: dict[str, Any]) -> dict[str, Any]: ...


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


class ReplayProvider:
    """Deterministic stand-in for a tool-calling Agent.

    Untrusted text influences the contaminated session only. A new clean
    session is always recreated from the trusted work order.
    """

    def plan(
        self,
        sample_id: str,
        *,
        contaminated: bool,
    ) -> ActionPlan:
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
        provider: ReplayProvider | None = None,
        fact_mode: str = "demo",
        recovery_delay: float = 1.5,
        testing_enabled: bool = True,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if fact_mode not in {"demo", "external"}:
            raise ValueError("fact_mode must be demo or external")
        self.runtime = runtime
        self.provider = provider or ReplayProvider()
        self.fact_mode = fact_mode
        self.recovery_delay = recovery_delay
        self.testing_enabled = testing_enabled
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
        self._injections: dict[str, dict[str, Any]] = {}
        self._injection_by_task: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._rebuild_line()

    def _rebuild_line(self) -> None:
        self._tasks = [
            {
                "task_id": f"task-{sample_id}",
                "sample_id": sample_id,
                "source": "cold-storage",
                "destination": "analyzer-01",
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
            for sample_id in SAMPLE_IDS
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

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": "ok",
                "line_state": self._line_state,
                "fact_mode": self.fact_mode,
                "worker_alive": bool(self._worker and self._worker.is_alive()),
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
                "tasks": json.loads(json.dumps(self._tasks)),
                "counters": dict(self._counters),
                "last_error": json.loads(json.dumps(self._last_error)),
                "last_seq": self._seq,
                "fact_mode": self.fact_mode,
                "controls": self._controls(),
            }

    def _controls(self) -> dict[str, bool]:
        state = self._line_state
        return {
            "start": state == "STOPPED",
            "pause": state == "RUNNING",
            "resume": state == "PAUSED",
            "reset": state in {"STOPPED", "PAUSED", "COMPLETED", "ERROR"},
            "inject": state in {"STOPPED", "RUNNING", "PAUSE_PENDING", "PAUSED"},
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._line_state != "STOPPED":
                raise ConflictError(f"cannot start from {self._line_state}")
            self._line_state = "RUNNING"
            self._emit("line.started", "orchestrator", {"task_count": len(self._tasks)})
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
            self._rebuild_line()
            self._emit(
                "line.reset",
                "orchestrator",
                {"path": "Policy→Lease→Guard→JOY"},
            )
            return self.snapshot()

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
            task = self._find_task(injection["target_task_id"])
            if task is None:
                raise ValidationError("unknown target_task_id")
            if task["sample_id"] != "sample-C":
                raise ValidationError("V2 demo accepts attacks only for sample-C")
            if task["status"] != "QUEUED":
                raise ConflictError("target task is no longer queued")
            if task["task_id"] in self._injection_by_task:
                raise ConflictError("target task already has an injection")
            record = {
                "request": injection,
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
            "target_task_id": record["request"]["target_task_id"],
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
                if task is None:
                    self._line_state = "COMPLETED"
                    self._emit(
                        "line.completed",
                        "orchestrator",
                        {"counters": dict(self._counters)},
                    )
                    return
                self._current_task_id = task["task_id"]
            try:
                self._process_task(task)
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

    def _process_task(self, task: dict[str, Any]) -> None:
        injection_id = self._injection_by_task.get(task["task_id"])
        contaminated = injection_id is not None
        response = self._attempt(task, contaminated=contaminated)
        reason = self._deny_reason(response)
        if reason is not None:
            if not contaminated or reason != "NO_MATCHING_GRANT":
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
        plan = self.provider.plan(task["sample_id"], contaminated=contaminated)
        intent = build_action_intent(plan)
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
        if self.fact_mode == "demo" and not contaminated:
            fact_result = self.runtime.publish_camera_fact()
            if fact_result.get("status") != "ok":
                raise RuntimeError(f"demo Fact rejected: {fact_result!r}")
        response = self.runtime.submit_action(intent)
        with self._lock:
            task["decision"] = response.get("decision")
        if self._deny_reason(response) is None:
            with self._lock:
                task["status"] = "EXECUTING"
                self._emit(
                    "task.executing",
                    "guard",
                    {"task_id": task["task_id"], "request_id": intent["request_id"]},
                )
        return response

    def _handle_registered_block(
        self,
        task: dict[str, Any],
        injection_id: str | None,
        response: dict[str, Any],
    ) -> None:
        assert injection_id is not None
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
                "reason_code": "NO_MATCHING_GRANT",
                "lease_issued": False,
                "guard_reached": False,
            }
            session_id = task["session_id"]
            self._emit(
                "task.blocked",
                "runtime",
                {
                    "task_id": task["task_id"],
                    "reason_code": "NO_MATCHING_GRANT",
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
            task["status"] = "COMPLETED"
            task["error"] = None
            self._counters["completed_tasks"] += 1
            self._counters["unsafe_outcomes"] += int(unsafe)
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
                    "destination": "analyzer-01",
                    "recovered": recovered,
                    "unsafe_outcome": unsafe,
                },
            )
            if unsafe:
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
