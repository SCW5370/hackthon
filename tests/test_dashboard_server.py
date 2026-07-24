from pathlib import Path
import unittest
from unittest.mock import patch

from dev.dashboard_server import DashboardBackend, UpstreamError


class DashboardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = DashboardBackend(
            orchestrator_url="http://orchestrator",
            runtime_url="http://runtime",
            guard_url="http://guard",
            legacy_url="http://legacy",
            legacy_token="",
            enable_unsafe_demo=False,
        )

    def test_snapshot_combines_orchestrator_and_physical_evidence(self) -> None:
        line = {
            "line_state": "RUNNING",
            "tasks": [{"task_id": "task-sample-A", "status": "COMPLETED"}],
            "counters": {"completed_tasks": 1},
        }
        physical = {
            "arm_state": "IDLE",
            "sample_locations": {"sample-A": "analyzer-01"},
            "unsafe_outcome": False,
        }

        def response(url, **_kwargs):
            if url == "http://orchestrator/v1/line/state":
                return line
            if url == "http://guard/v1/physical":
                return {"status": "ok", "physical": physical}
            raise AssertionError(url)

        with patch("dev.dashboard_server.json_request", side_effect=response):
            value = self.backend.snapshot()
        self.assertEqual(value["line"], line)
        self.assertEqual(value["physical"], physical)
        self.assertEqual(value["physical_status"], "live")

    def test_busy_guard_uses_last_physical_evidence_without_polling(self) -> None:
        self.backend._last_physical = {"arm_state": "MOVING"}
        line = {
            "line_state": "RUNNING",
            "tasks": [{"task_id": "task-sample-A", "status": "EXECUTING"}],
        }
        with patch(
            "dev.dashboard_server.json_request",
            return_value=line,
        ) as mocked:
            value = self.backend.snapshot()
        self.assertEqual(value["physical_status"], "cached")
        self.assertEqual(value["physical"]["arm_state"], "MOVING")
        self.assertEqual(mocked.call_count, 1)

    def test_busy_snapshot_merges_confirmed_execution_receipt_inventory(self) -> None:
        self.backend._last_physical = {
            "sample_locations": {
                "sample-A": "analyzer-01",
                "sample-B": "cold-storage",
            },
            "current_dock": "analyzer-01",
        }
        line = {
            "line_state": "RUNNING",
            "tasks": [{"task_id": "task-sample-C", "status": "EXECUTING"}],
            "physical_evidence": {
                "source": "joy-execution-receipt",
                "sample_locations": {
                    "sample-A": "analyzer-01",
                    "sample-B": "analyzer-01",
                },
                "current_dock": "analyzer-01",
            },
        }
        with patch(
            "dev.dashboard_server.json_request",
            return_value=line,
        ) as mocked:
            value = self.backend.snapshot()
        self.assertEqual(value["physical_status"], "confirmed")
        self.assertEqual(
            value["physical"]["sample_locations"]["sample-B"],
            "analyzer-01",
        )
        self.assertEqual(mocked.call_count, 1)

    def test_unsafe_baseline_is_disabled_by_default(self) -> None:
        with self.assertRaises(UpstreamError) as raised:
            self.backend.run_unsafe_baseline("")
        self.assertEqual(raised.exception.status, 404)

    def test_untrusted_content_is_rendered_with_text_content_only(self) -> None:
        source = Path("console/dashboard.js").read_text(encoding="utf-8")
        self.assertIn('ui[id].textContent', source)
        self.assertNotIn("innerHTML", source)


if __name__ == "__main__":
    unittest.main()
