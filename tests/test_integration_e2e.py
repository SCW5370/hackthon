import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from guard.executor import FakeExecutor, JoyExecutor
from guard.guard_http import SafeExecGuard
from lab_agent.contracts import ActionPlan, build_action_intent
from runtime.contracts import ActionIntent, Fact, MissionSpec
from runtime.lease_authority import LeaseAuthority
from runtime.runtime_http import SafeExecRuntime


class InProcessRuntime(SafeExecRuntime):
    def __init__(self, mission, private_key, guard):
        super().__init__(mission, private_key)
        self.guard = guard

    def _call_guard(self, intent, lease):
        return self.guard.execute(intent.to_dict(), lease.to_dict())


def mission() -> MissionSpec:
    now_ms = int(time.time() * 1000)
    return MissionSpec.from_dict(
        {
            "schema_version": "safeexec.mission.v1",
            "mission_id": "mission-e2e-test",
            "principal_id": "lab-agent-01",
            "valid_from_ms": now_ms - 60_000,
            "valid_until_ms": now_ms + 60_000,
            "grants": [
                {
                    "grant_id": "sample-a-to-analyzer",
                    "action": "lab.sample.transfer",
                    "resource": {"type": "lab.sample", "id": "sample-A"},
                    "arguments": {
                        "source": "cold-storage",
                        "destination": "analyzer-01",
                    },
                    "required_facts": [
                        {
                            "key": "camera.healthy",
                            "equals": True,
                            "max_age_ms": 1500,
                        }
                    ],
                }
            ],
        }
    )


class FakeJoyBackend:
    def __init__(self, state="succeeded"):
        self.state = state
        self.calls = []

    def execute(self, intent):
        self.calls.append(intent)
        return {
            "schema_version": "safeexec.execution.v1",
            "state": self.state,
            "finished_at_ms": int(time.time() * 1000),
            "error_code": None if self.state == "succeeded" else "JOY_UNAVAILABLE",
            "error": None if self.state == "succeeded" else "offline",
        }


class UnavailableDiscovery:
    def require_ready(self):
        raise ConnectionError("JOY offline")

    def set_executing(self, _value):
        pass

    def refresh(self):
        return {"ready": False}


class SafeExecEndToEndTests(unittest.TestCase):
    def test_post_lease_connection_loss_is_reported_as_uncertain(self):
        private_key, _ = LeaseAuthority.generate_keypair()
        runtime = SafeExecRuntime(mission(), private_key)
        intent = ActionIntent.from_dict(
            build_action_intent(
                ActionPlan("sample-A", "cold-storage", "analyzer-01")
            )
        )
        lease = SimpleNamespace(
            lease_id="lease-after-dispatch",
            to_dict=lambda: {"lease_id": "lease-after-dispatch"},
        )
        with patch(
            "urllib.request.urlopen",
            side_effect=ConnectionResetError("peer reset after dispatch"),
        ):
            result = runtime._call_guard(intent, lease)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(
            result["reason_code"],
            "EXECUTION_OUTCOME_UNKNOWN",
        )

    def test_agent_runtime_guard_contract_allows_only_granted_action(self):
        private_key, public_key = LeaseAuthority.generate_keypair()
        executor = FakeExecutor()
        guard = SafeExecGuard(public_key, executor)
        runtime = InProcessRuntime(mission(), private_key, guard)
        runtime.add_fact(
            Fact(
                key="camera.healthy",
                value=True,
                source="rdk-camera",
                confidence=1.0,
                timestamp=time.time(),
                ttl_ms=1500,
            ).to_dict()
        )

        normal = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "analyzer-01")
        )
        allowed = runtime.process_action(normal)
        self.assertEqual(allowed["status"], "ok")
        self.assertEqual(allowed["guard_response"]["status"], "executed")
        self.assertEqual(executor.call_count, 1)

        malicious = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "waste-bin")
        )
        denied = runtime.process_action(malicious)
        self.assertEqual(denied["status"], "denied")
        self.assertEqual(
            denied["decision"]["reason_code"], "NO_MATCHING_GRANT"
        )
        self.assertEqual(executor.call_count, 1)
        latest = runtime.get_state()["latest_action"]
        self.assertEqual(latest["intent"]["request_id"], malicious["request_id"])
        self.assertEqual(latest["decision"]["effect"], "deny")
        self.assertIsNone(latest["lease"])

    def test_security_guard_uses_live_joy_adapter_backend(self):
        backend = FakeJoyBackend()
        executor = JoyExecutor(backend=backend)
        intent = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "analyzer-01")
        )
        receipt = executor.execute(intent)
        self.assertEqual(receipt["status"], "executed")
        self.assertEqual(len(backend.calls), 1)

    def test_fact_publisher_cannot_override_policy_max_age(self):
        private_key, public_key = LeaseAuthority.generate_keypair()
        executor = FakeExecutor()
        runtime = InProcessRuntime(
            mission(),
            private_key,
            SafeExecGuard(public_key, executor),
        )
        runtime.add_fact(
            Fact(
                key="camera.healthy",
                value=True,
                source="untrusted-adapter",
                confidence=1.0,
                timestamp=time.time() - 2,
                ttl_ms=60_000,
            ).to_dict()
        )
        intent = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "analyzer-01")
        )
        denied = runtime.process_action(intent)
        self.assertEqual(denied["status"], "denied")
        self.assertEqual(denied["decision"]["reason_code"], "FACT_STALE")
        self.assertEqual(executor.call_count, 0)

    def test_live_joy_failure_is_not_reported_as_executed(self):
        executor = JoyExecutor(backend=FakeJoyBackend(state="failed"))
        intent = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "analyzer-01")
        )
        with self.assertRaises(RuntimeError):
            executor.execute(intent)

    def test_unavailable_executor_fails_before_lease_and_guard(self):
        private_key, public_key = LeaseAuthority.generate_keypair()
        executor = FakeExecutor()
        runtime = InProcessRuntime(
            mission(),
            private_key,
            SafeExecGuard(public_key, executor),
        )
        runtime.guard_discovery = UnavailableDiscovery()
        runtime.add_fact(
            Fact(
                key="camera.healthy",
                value=True,
                source="rdk-camera",
                confidence=1.0,
                timestamp=time.time(),
                ttl_ms=1500,
            ).to_dict()
        )
        result = runtime.process_action(
            build_action_intent(
                ActionPlan("sample-A", "cold-storage", "analyzer-01")
            )
        )
        self.assertEqual(result["status"], "denied")
        self.assertEqual(
            result["decision"]["reason_code"],
            "EXECUTOR_UNAVAILABLE",
        )
        self.assertNotIn("lease", result)
        self.assertEqual(executor.call_count, 0)


if __name__ == "__main__":
    unittest.main()
