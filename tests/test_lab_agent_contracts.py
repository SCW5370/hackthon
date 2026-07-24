import unittest

from lab_agent.contracts import (
    ActionPlan,
    build_action_intent,
    plan_fingerprint,
    validate_action_intent,
)


class LabAgentContractTests(unittest.TestCase):
    def test_action_intent_matches_frozen_contract(self) -> None:
        intent = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "analyzer-01"),
            request_id="d7588eb9-f22c-49a7-9814-b46260e19d8e",
            issued_at_ms=1784800000125,
        )
        self.assertEqual(
            intent,
            {
                "schema_version": "safeexec.action.v1",
                "request_id": "d7588eb9-f22c-49a7-9814-b46260e19d8e",
                "principal_id": "lab-agent-01",
                "issued_at_ms": 1784800000125,
                "action": "lab.sample.transfer",
                "resource": {"type": "lab.sample", "id": "sample-A"},
                "arguments": {
                    "source": "cold-storage",
                    "destination": "analyzer-01",
                },
            },
        )

    def test_unknown_fields_and_raw_coordinates_are_rejected(self) -> None:
        intent = build_action_intent(
            ActionPlan("sample-A", "cold-storage", "analyzer-01")
        )
        with self.assertRaises(ValueError):
            validate_action_intent({**intent, "raw_coordinates": [1, 2, 3]})

    def test_fingerprint_ignores_request_metadata(self) -> None:
        plan = ActionPlan("sample-A", "cold-storage", "waste-bin")
        self.assertEqual(plan_fingerprint(plan), plan_fingerprint(plan))
        self.assertNotEqual(
            plan_fingerprint(plan),
            plan_fingerprint(
                ActionPlan("sample-A", "cold-storage", "analyzer-01")
            ),
        )


if __name__ == "__main__":
    unittest.main()
