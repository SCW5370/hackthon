import argparse
import os
import unittest
from unittest.mock import patch

from lab_agent.runner import run
from lab_agent.transports import LegacyTransport


class LabAgentRunnerTests(unittest.TestCase):
    def test_prompt_injection_replay_produces_malicious_intent_without_execution(self) -> None:
        result = run(
            argparse.Namespace(
                command="run",
                mode="replay",
                scenario="prompt-injection",
                transport="dry-run",
                confirm_unsafe_demo=False,
            )
        )
        self.assertEqual(
            result["action_intent"]["arguments"]["destination"], "waste-bin"
        )
        self.assertEqual(result["execution_result"]["state"], "not_executed")
        self.assertEqual(
            result["plan_source"], "deterministic-compromised-agent-replay"
        )

    def test_legacy_transport_fails_closed_without_explicit_confirmation(self) -> None:
        transport = LegacyTransport(
            "http://127.0.0.1:8791",
            "development-token",
            confirmed_unsafe_demo=False,
        )
        with self.assertRaises(PermissionError):
            transport.submit(
                {
                    "schema_version": "safeexec.action.v1",
                    "request_id": "d7588eb9-f22c-49a7-9814-b46260e19d8e",
                    "principal_id": "lab-agent-01",
                    "issued_at_ms": 1784800000125,
                    "action": "lab.sample.transfer",
                    "resource": {"type": "lab.sample", "id": "sample-A"},
                    "arguments": {
                        "source": "cold-storage",
                        "destination": "waste-bin",
                    },
                }
            )

    def test_missing_llm_configuration_is_rejected_before_network_access(self) -> None:
        args = argparse.Namespace(
            command="run",
            mode="llm",
            scenario="normal",
            transport="dry-run",
            confirm_unsafe_demo=False,
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                run(args)


if __name__ == "__main__":
    unittest.main()
