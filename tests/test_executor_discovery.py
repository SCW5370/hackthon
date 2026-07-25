import io
import json
import threading
import time
import unittest
from urllib.error import URLError

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
