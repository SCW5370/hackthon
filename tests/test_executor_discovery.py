import io
import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError

from guard.executor import FakeExecutor
from guard.guard_http import SafeExecGuard
from runtime.lease_authority import LeaseAuthority
from runtime.executor_discovery import (
    GuardEndpointDiscovery,
    normalize_guard_base_url,
)


class Response:
    def __init__(self, value):
        self.body = io.BytesIO(json.dumps(value).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body.read()


class ExecutorDiscoveryTests(unittest.TestCase):
    def test_guard_readiness_exposes_fresh_server_clock_outside_cache(self):
        _, public_key = LeaseAuthority.generate_keypair()
        guard = SafeExecGuard(public_key, FakeExecutor())
        guard._schedule_readiness_probe = lambda: None

        with patch("guard.guard_http.time.time", side_effect=[10.0, 11.0]):
            first = guard.readiness()
            second = guard.readiness()

        self.assertEqual(first["server_time_ms"], 10_000)
        self.assertEqual(second["server_time_ms"], 11_000)

    def test_guard_keeps_confirmed_readiness_while_executor_is_busy(self):
        _, public_key = LeaseAuthority.generate_keypair()
        guard = SafeExecGuard(public_key, FakeExecutor())
        guard._schedule_readiness_probe = lambda: None
        guard._readiness_cache.update(
            {
                "status": "ready",
                "ready": True,
                "checked_at_ms": 1_000,
                "error": None,
            }
        )
        guard._executing = True

        with patch("guard.guard_http.time.time", return_value=30.0):
            value = guard.readiness()

        self.assertTrue(value["ready"])
        self.assertTrue(value["executing"])
        self.assertEqual(value["status"], "executing")

    def test_normalizes_execute_url_to_service_base(self):
        self.assertEqual(
            normalize_guard_base_url("http://windows:8788/v1/execute"),
            "http://windows:8788",
        )

    def test_selects_first_ready_allowlisted_guard(self):
        def opener(request, timeout):
            del timeout
            if request.full_url.startswith("http://lan:8788"):
                raise URLError("offline")
            return Response(
                {
                    "status": "ready",
                    "ready": True,
                    "expected_audience": "joy-guard-01",
                    "executor": {"type": "joy", "ready": True},
                }
            )

        discovery = GuardEndpointDiscovery(
            ["http://lan:8788", "http://tailscale:8788"],
            opener=opener,
            autostart=False,
        )
        value = discovery.refresh()
        self.assertTrue(value["ready"])
        self.assertEqual(
            value["selected_endpoint"],
            "http://tailscale:8788",
        )
        self.assertEqual(
            discovery.require_ready(),
            "http://tailscale:8788/v1/execute",
        )
        self.assertFalse(value["auto_execute"])
        self.assertTrue(value["requires_operator_start"])

    def test_rejects_wrong_guard_audience(self):
        discovery = GuardEndpointDiscovery(
            ["http://wrong:8788"],
            opener=lambda *_args, **_kwargs: Response(
                {
                    "status": "ready",
                    "ready": True,
                    "expected_audience": "other-robot",
                }
            ),
            autostart=False,
        )
        value = discovery.refresh()
        self.assertFalse(value["ready"])
        with self.assertRaises(ConnectionError):
            discovery.require_ready()

    def test_rejects_ready_guard_when_device_clock_skew_exceeds_lease_budget(self):
        discovery = GuardEndpointDiscovery(
            ["http://skewed:8788"],
            now_ms=lambda: 10_000,
            opener=lambda *_args, **_kwargs: Response(
                {
                    "status": "ready",
                    "ready": True,
                    "server_time_ms": 6_500,
                    "expected_audience": "joy-guard-01",
                    "executor": {"type": "joy", "ready": True},
                }
            ),
            autostart=False,
        )

        value = discovery.refresh()

        self.assertFalse(value["ready"])
        candidate = value["candidates"][0]
        self.assertEqual(candidate["clock_skew_ms"], 3500)
        self.assertIn("clock skew", candidate["error"])

    def test_reports_acceptable_clock_skew_for_ready_guard(self):
        discovery = GuardEndpointDiscovery(
            ["http://synced:8788"],
            now_ms=lambda: 10_000,
            opener=lambda *_args, **_kwargs: Response(
                {
                    "status": "ready",
                    "ready": True,
                    "server_time_ms": 9_250,
                    "expected_audience": "joy-guard-01",
                    "executor": {"type": "joy", "ready": True},
                }
            ),
            autostart=False,
        )

        value = discovery.refresh()

        self.assertTrue(value["ready"])
        self.assertEqual(value["candidates"][0]["clock_skew_ms"], 750)

    def test_require_ready_waits_for_inflight_refresh_then_rechecks(self):
        discovery = GuardEndpointDiscovery(
            ["http://lan:8788"],
            opener=lambda *_args, **_kwargs: Response(
                {
                    "status": "ready",
                    "ready": True,
                    "expected_audience": "joy-guard-01",
                    "executor": {"type": "joy", "ready": True},
                }
            ),
            autostart=False,
        )
        discovery._refresh_lock.acquire()

        def release_after_delay():
            time.sleep(0.05)
            discovery._refresh_lock.release()

        releaser = threading.Thread(
            target=release_after_delay,
        )
        releaser.start()
        try:
            self.assertEqual(
                discovery.require_ready(),
                "http://lan:8788/v1/execute",
            )
        finally:
            releaser.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
