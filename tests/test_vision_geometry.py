import unittest

from adapters.vision_geometry import BoundingBox, point_in_polygon, tracks_in_zone


ZONE = [(0.5, 0.5), (1.0, 0.5), (1.0, 1.0), (0.5, 1.0)]


class VisionGeometryTests(unittest.TestCase):
    def test_inside_and_outside(self) -> None:
        self.assertTrue(point_in_polygon((0.75, 0.75), ZONE))
        self.assertFalse(point_in_polygon((0.25, 0.75), ZONE))

    def test_boundary_counts_as_inside(self) -> None:
        self.assertTrue(point_in_polygon((0.5, 0.7), ZONE))

    def test_tracks_use_bottom_center(self) -> None:
        boxes = [
            BoundingBox(600, 200, 100, 300, confidence=0.9, track_id=7),
            BoundingBox(50, 50, 100, 100, confidence=0.9, track_id=8),
        ]
        self.assertEqual(tracks_in_zone(boxes, ZONE, 1000, 600, 0.5), [7])


if __name__ == "__main__":
    unittest.main()

