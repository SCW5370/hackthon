import unittest

from adapters.camera_health import CameraHealthTracker, grayscale_entropy


class CameraHealthTests(unittest.TestCase):
    def test_entropy_of_flat_frame_is_zero(self) -> None:
        self.assertEqual(grayscale_entropy([42] * 100), 0.0)

    def test_healthy_varying_frame(self) -> None:
        tracker = CameraHealthTracker()
        tracker.observe(list(range(256)), now=1.0)
        self.assertTrue(tracker.status(now=1.1).healthy)

    def test_black_frame(self) -> None:
        tracker = CameraHealthTracker()
        tracker.observe([0] * 256, now=1.0)
        self.assertEqual(tracker.status(now=1.1).reason, "LOW_BRIGHTNESS")

    def test_frame_timeout(self) -> None:
        tracker = CameraHealthTracker(frame_timeout_ms=500)
        tracker.observe(list(range(256)), now=1.0)
        self.assertEqual(tracker.status(now=1.6).reason, "FRAME_TIMEOUT")


if __name__ == "__main__":
    unittest.main()

