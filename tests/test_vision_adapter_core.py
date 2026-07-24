import unittest
from types import SimpleNamespace

from adapters.vision_adapter_core import (
    ZoneTracker,
    body_boxes_from_targets,
    grayscale_samples_from_image,
)


def roi(kind, x, y, width, height, confidence=0.9):
    return SimpleNamespace(
        type=kind,
        confidence=confidence,
        rect=SimpleNamespace(
            x_offset=x, y_offset=y, width=width, height=height
        ),
    )


class VisionAdapterCoreTests(unittest.TestCase):
    def test_extracts_only_body_roi(self) -> None:
        target = SimpleNamespace(
            type="person",
            track_id=12,
            rois=[
                roi("face", 10, 10, 20, 20),
                roi("body", 500, 100, 200, 400),
            ],
        )
        boxes = body_boxes_from_targets([target])
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].track_id, 12)
        self.assertEqual(boxes[0].width, 200)

    def test_zone_requires_consecutive_frames_and_clears_late(self) -> None:
        tracker = ZoneTracker(
            polygon=[(0.5, 0.5), (1, 0.5), (1, 1), (0.5, 1)],
            image_width=1000,
            image_height=600,
            enter_frames=2,
            clear_after_ms=500,
        )
        inside = body_boxes_from_targets(
            [SimpleNamespace(type="person", track_id=7, rois=[roi("body", 600, 100, 100, 400)])]
        )
        self.assertTrue(tracker.observe(inside, now=1.0).value)
        self.assertFalse(tracker.observe(inside, now=1.1).value)
        self.assertFalse(tracker.observe([], now=1.2).value)
        self.assertTrue(tracker.observe([], now=1.7).value)

    def test_detection_timeout_becomes_unknown(self) -> None:
        tracker = ZoneTracker(
            polygon=[(0, 0), (1, 0), (1, 1), (0, 1)],
            image_width=100,
            image_height=100,
            detection_timeout_ms=500,
        )
        tracker.observe([], now=1.0)
        self.assertIsNone(tracker.status(now=1.6).value)

    def test_rgb_sampling(self) -> None:
        self.assertEqual(
            grayscale_samples_from_image(bytes([30, 60, 90, 0, 0, 0]), "rgb8"),
            [60, 0],
        )


if __name__ == "__main__":
    unittest.main()
