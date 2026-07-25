import copy
import time
import unittest

from guard.executor import FakeExecutor
from guard.guard_http import SafeExecGuard
from lab_agent.contracts import ActionPlan, build_action_intent
from runtime.contracts import Fact, MissionSpec
from runtime.lease_authority import LeaseAuthority
from runtime.runtime_http import SafeExecRuntime
from runtime.work_orders import WorkOrderIssuer


def mission() -> MissionSpec:
    now_ms = int(time.time() * 1000)
    return MissionSpec.from_dict(
        {
            "schema_version": "safeexec.mission.v1",
            "mission_id": "mission-work-order-test",
            "principal_id": "lab-agent-01",
            "valid_from_ms": now_ms - 60_000,
            "valid_until_ms": now_ms + 60_000,
            "grants": [
                {
                    "grant_id": "policy-sample-a-analysis",
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


def grant(destination: str = "analyzer-01") -> dict:
    return {
        "grant_id": "order-sample-a",
        "action": "lab.sample.transfer",
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {
            "source": "cold-storage",
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


class InProcessRuntime(SafeExecRuntime):
    def __init__(self, policy, lease_key, guard, issuer):
        super().__init__(
            policy,
            lease_key,
            trusted_work_order_keys={
                issuer.issuer_id: issuer.public_key_b64(),
            },
            require_work_order_for_actions=True,
        )
        self.guard = guard

    def _call_guard(self, intent, lease):
        return self.guard.execute(intent.to_dict(), lease.to_dict())


class WorkOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        issuer_private, _ = LeaseAuthority.generate_keypair()
        self.issuer = WorkOrderIssuer(issuer_private)
        lease_private, lease_public = LeaseAuthority.generate_keypair()
        self.executor = FakeExecutor()
        self.runtime = InProcessRuntime(
            mission(),
            lease_private,
            SafeExecGuard(lease_public, self.executor),
            self.issuer,
        )
        self.runtime.add_fact(
            Fact(
                key="camera.healthy",
                value=True,
                source="rdk-x5",
                confidence=1.0,
                timestamp=time.time(),
                ttl_ms=1500,
            ).to_dict()
        )

    def issue(self, grants=None):
        return self.issuer.issue(
            subject_principal_id="lab-agent-01",
            grants=grants or [grant()],
            valid_for_ms=60_000,
            operator_note="analyze sample A",
        )

    def test_signed_order_allows_once_and_binds_lease(self):
        order = self.issue()
        registered = self.runtime.register_work_order(order.to_dict())
        self.assertEqual(registered["status"], "registered")
        intent = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "analyzer-01"),
            work_order_id=order.work_order_id,
        )
        allowed = self.runtime.process_action(intent)
        self.assertEqual(allowed["status"], "ok")
        self.assertEqual(allowed["lease"]["mission_id"], order.work_order_id)
        self.assertEqual(self.executor.call_count, 1)

        replay_budget = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "analyzer-01"),
            work_order_id=order.work_order_id,
        )
        denied = self.runtime.process_action(replay_budget)
        self.assertEqual(
            denied["decision"]["reason_code"],
            "WORK_ORDER_GRANT_CONSUMED",
        )
        self.assertEqual(self.executor.call_count, 1)

    def test_transfer_without_order_is_rejected(self):
        intent = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "analyzer-01")
        )
        denied = self.runtime.process_action(intent)
        self.assertEqual(denied["decision"]["reason_code"], "WORK_ORDER_REQUIRED")
        self.assertEqual(self.executor.call_count, 0)

    def test_signature_tampering_is_rejected(self):
        value = self.issue().to_dict()
        value["operator_note"] = "tampered after signing"
        with self.assertRaisesRegex(ValueError, "signature"):
            self.runtime.register_work_order(value)

    def test_order_cannot_exceed_organization_policy(self):
        value = self.issue([grant("waste-bin")]).to_dict()
        with self.assertRaisesRegex(ValueError, "exceeds OrganizationPolicy"):
            self.runtime.register_work_order(value)


if __name__ == "__main__":
    unittest.main()
