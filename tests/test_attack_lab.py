"""AttackLab 单元测试."""

import json
import unittest
from pathlib import Path

# 确保 attack_lab 可导入
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from attack_lab.loader import AttackCase, ValidationError
from attack_lab.runner import run_replay, run_all_replay, evaluate_replay
from attack_lab.reporter import AttackResult, Observation, make_result

CASE_DIR = Path(__file__).parents[1] / "contracts" / "examples"


class TestLoader(unittest.TestCase):
    def test_list_cases_finds_all_five(self):
        cases = AttackCase.list_cases()
        ids = [c.attack_id for c in cases]
        self.assertIn("label-injection-01", ids)
        self.assertIn("voice-injection-01", ids)
        self.assertIn("admin-impersonation-01", ids)
        self.assertIn("destination-replacement-01", ids)
        self.assertIn("normal-baseline-01", ids)

    def test_find_returns_correct_case(self):
        case = AttackCase.find("label-injection-01")
        self.assertIsNotNone(case)
        self.assertEqual(case.title, "样品标签注入攻击")
        self.assertEqual(case.channel, "sample_label")

    def test_find_returns_none_for_unknown(self):
        self.assertIsNone(AttackCase.find("does-not-exist"))

    def test_from_json_file_parses_correctly(self):
        path = CASE_DIR / "attack_case.label_injection.json"
        case = AttackCase.from_json_file(path)
        self.assertEqual(case.attack_id, "label-injection-01")
        self.assertEqual(case.expected_agent_intent["sample_id"], "sample-A")
        self.assertEqual(case.expected_agent_intent["destination"], "quarantine-zone")

    def test_normal_baseline_has_no_untrusted_content(self):
        case = AttackCase.find("normal-baseline-01")
        self.assertIsNotNone(case)
        self.assertEqual(case.untrusted_content, "")


class TestEvaluateReplay(unittest.TestCase):
    def test_normal_baseline_allowed(self):
        case = AttackCase.find("normal-baseline-01")
        self.assertIsNotNone(case)
        obs = evaluate_replay(case)
        self.assertFalse(obs.agent_compromised)
        self.assertFalse(obs.malicious_intent_generated)
        self.assertEqual(obs.runtime_effect, "allow")
        self.assertEqual(obs.reason_code, "GRANT_MATCHED")
        self.assertTrue(obs.lease_issued)
        self.assertTrue(obs.guard_reached)
        self.assertTrue(obs.executor_called)

    def test_label_injection_blocked(self):
        case = AttackCase.find("label-injection-01")
        self.assertIsNotNone(case)
        obs = evaluate_replay(case)
        self.assertTrue(obs.agent_compromised)
        self.assertTrue(obs.malicious_intent_generated)
        self.assertEqual(obs.runtime_effect, "deny")
        self.assertEqual(obs.reason_code, "NO_MATCHING_GRANT")
        self.assertFalse(obs.lease_issued)
        self.assertFalse(obs.guard_reached)
        self.assertFalse(obs.executor_called)
        self.assertFalse(obs.physical_changed)

    def test_voice_injection_blocked(self):
        case = AttackCase.find("voice-injection-01")
        self.assertIsNotNone(case)
        obs = evaluate_replay(case)
        self.assertTrue(obs.agent_compromised)
        self.assertTrue(obs.malicious_intent_generated)
        self.assertEqual(obs.runtime_effect, "deny")

    def test_admin_impersonation_blocked(self):
        case = AttackCase.find("admin-impersonation-01")
        self.assertIsNotNone(case)
        obs = evaluate_replay(case)
        self.assertTrue(obs.agent_compromised)
        self.assertEqual(obs.runtime_effect, "deny")

    def test_destination_replacement_blocked(self):
        case = AttackCase.find("destination-replacement-01")
        self.assertIsNotNone(case)
        obs = evaluate_replay(case)
        self.assertTrue(obs.agent_compromised)
        self.assertEqual(obs.runtime_effect, "deny")


class TestRunReplay(unittest.TestCase):
    def test_run_replay_returns_valid_result(self):
        case = AttackCase.find("label-injection-01")
        self.assertIsNotNone(case)
        result, updated_case = run_replay(case)
        self.assertIsInstance(result, AttackResult)
        self.assertEqual(result.attack_id, "label-injection-01")
        self.assertEqual(result.target, "replay")
        self.assertIsNotNone(result.started_at_ms)
        self.assertIsNotNone(result.finished_at_ms)

    def test_run_all_replay_all_five_cases(self):
        cases = AttackCase.list_cases()
        results = run_all_replay(cases)
        self.assertEqual(len(results), 5)
        # normal baseline should be normal_allowed
        normal = next(r for r in results if r.attack_id == "normal-baseline-01")
        self.assertEqual(normal.verdict, "normal_allowed")
        # all attack cases should be blocked
        for r in results:
            if r.attack_id != "normal-baseline-01":
                self.assertEqual(r.verdict, "attack_blocked", f"{r.attack_id} verdict={r.verdict}")


class TestReporter(unittest.TestCase):
    def test_make_result_attack_blocked(self):
        obs = Observation(
            agent_compromised=True,
            malicious_intent_generated=True,
            runtime_effect="deny",
            reason_code="NO_MATCHING_GRANT",
            lease_issued=False,
            guard_reached=False,
            executor_called=False,
            physical_changed=False,
            recovery_state="not_available",
        )
        result = make_result(
            attack_id="test-attack",
            target="replay",
            observations=obs,
            evidence=[],
            started_at_ms=1000,
            finished_at_ms=2000,
        )
        self.assertEqual(result.verdict, "attack_blocked")
        d = result.to_dict()
        self.assertEqual(d["verdict"], "attack_blocked")
        self.assertIn("run_id", d)

    def test_make_result_normal_allowed(self):
        obs = Observation(
            agent_compromised=False,
            malicious_intent_generated=False,
            runtime_effect="allow",
            reason_code="GRANT_MATCHED",
            lease_issued=True,
            guard_reached=True,
            executor_called=True,
            physical_changed=False,
            recovery_state="not_available",
        )
        result = make_result(
            attack_id="normal-test",
            target="replay",
            observations=obs,
            evidence=[],
            started_at_ms=1000,
            finished_at_ms=2000,
        )
        self.assertEqual(result.verdict, "normal_allowed")

    def test_markdown_output_contains_key_fields(self):
        case = AttackCase.find("label-injection-01")
        self.assertIsNotNone(case)
        result, _ = run_replay(case)
        md = result.to_markdown()
        self.assertIn("label-injection-01", md)
        self.assertIn("attack_blocked", md)
        self.assertIn("agent_compromised", md)
        self.assertIn("runtime_effect", md)


class TestAttackResultSchema(unittest.TestCase):
    def test_result_dict_conforms_to_schema(self):
        case = AttackCase.find("label-injection-01")
        self.assertIsNotNone(case)
        result, _ = run_replay(case)
        d = result.to_dict()
        # required fields per attack_result.v1.schema.json
        self.assertEqual(d["schema_version"], "safeexec.attack-result.v1")
        self.assertIn("run_id", d)
        self.assertIn(d["target"], ["replay", "orchestrator_http", "runtime_http"])
        self.assertIn(d["verdict"], ["normal_allowed", "attack_blocked", "attack_succeeded", "inconclusive", "evaluation_error"])
        obs = d["observations"]
        for field in ["agent_compromised", "malicious_intent_generated", "runtime_effect",
                      "reason_code", "lease_issued", "guard_reached", "executor_called",
                      "physical_changed", "recovery_state", "recovery_time_ms"]:
            self.assertIn(field, obs)


if __name__ == "__main__":
    unittest.main()
