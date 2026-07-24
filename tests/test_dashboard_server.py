import unittest
from unittest.mock import patch

from dev.dashboard_server import DashboardState


class DashboardServerTests(unittest.TestCase):
    def test_snapshot_maps_real_runtime_denial_and_physical_state(self):
        state = DashboardState(
            runtime_url="http://runtime",
            guard_url="http://guard",
            legacy_url="http://legacy",
            legacy_token="",
            enable_unsafe_demo=False,
        )
        request_id = "a-request"
        state._run_id = request_id
        state._fingerprint = "sha256:test"

        def response(url, **_kwargs):
            if url == "http://runtime/v1/state":
                return {
                    "latest_action": {
                        "status": "denied",
                        "intent": {
                            "request_id": request_id,
                            "principal_id": "lab-agent-01",
                            "action": "lab.sample.transfer",
                            "resource": {"type": "lab.sample", "id": "sample-A"},
                            "arguments": {
                                "source": "cold-storage",
                                "destination": "waste-bin",
                            },
                        },
                        "decision": {
                            "effect": "deny",
                            "reason_code": "NO_MATCHING_GRANT",
                        },
                        "lease": None,
                        "guard_response": None,
                    }
                }
            if url == "http://runtime/v1/events":
                return {
                    "events": [
                        {
                            "timestamp": 1.0,
                            "source": "runtime",
                            "event": "policy.denied",
                            "severity": "warning",
                            "payload": {
                                "request_id": request_id,
                                "reason_code": "NO_MATCHING_GRANT",
                            },
                        }
                    ]
                }
            if url == "http://guard/v1/events":
                return {"events": []}
            if url == "http://guard/v1/physical":
                return {
                    "status": "ok",
                    "physical": {
                        "arm_state": "IDLE",
                        "platform_state": "IDLE",
                        "current_dock": "home",
                        "sample_locations": {"sample-A": "cold-storage"},
                        "unsafe_outcome": False,
                    },
                }
            raise AssertionError(url)

        with patch("dev.dashboard_server._json_request", side_effect=response):
            snapshot = state.snapshot()

        self.assertEqual(snapshot["result"]["state"], "blocked")
        self.assertEqual(snapshot["runtime"]["effect"], "deny")
        self.assertFalse(snapshot["lease"]["issued"])
        self.assertFalse(snapshot["guard"]["reached"])
        self.assertEqual(snapshot["physical"]["current_dock"], "home")
        self.assertEqual(snapshot["timeline"][0]["status"], "blocked")

    def test_unavailable_guard_is_not_reported_as_safe(self):
        state = DashboardState(
            runtime_url="http://runtime",
            guard_url="http://guard",
            legacy_url="http://legacy",
            legacy_token="",
            enable_unsafe_demo=False,
        )

        def response(url, **_kwargs):
            if url.endswith("/v1/state"):
                return {"latest_action": None}
            if url.endswith("/v1/events"):
                return {"events": []}
            if url.endswith("/v1/physical"):
                return {"_connection_error": "offline"}
            raise AssertionError(url)

        with patch("dev.dashboard_server._json_request", side_effect=response):
            snapshot = state.snapshot()

        self.assertIsNone(snapshot["physical"]["unsafe_outcome"])
        self.assertEqual(snapshot["connectivity"]["guard"], "disconnected")


if __name__ == "__main__":
    unittest.main()
