from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


Point = tuple[float, float]


@dataclass(frozen=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0
    track_id: int | None = None

    def bottom_center(self, image_width: float, image_height: float) -> Point:
        if image_width <= 0 or image_height <= 0:
            raise ValueError("image dimensions must be positive")
        return (
            (self.x + self.width / 2.0) / image_width,
            (self.y + self.height) / image_height,
        )


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Return True when point is inside or on the boundary of polygon."""
    if len(polygon) < 3:
        raise ValueError("polygon requires at least three points")

    px, py = point
    inside = False
    previous = polygon[-1]

    for current in polygon:
        x1, y1 = previous
        x2, y2 = current

        cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
        if abs(cross) < 1e-9:
            if min(x1, x2) - 1e-9 <= px <= max(x1, x2) + 1e-9 and min(
                y1, y2
            ) - 1e-9 <= py <= max(y1, y2) + 1e-9:
                return True

        if (y1 > py) != (y2 > py):
            intersection_x = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px <= intersection_x:
                inside = not inside

        previous = current

    return inside


def tracks_in_zone(
    boxes: Iterable[BoundingBox],
    polygon: Sequence[Point],
    image_width: int,
    image_height: int,
    minimum_confidence: float,
) -> list[int]:
    result: list[int] = []
    for box in boxes:
        if box.confidence < minimum_confidence:
            continue
        if point_in_polygon(box.bottom_center(image_width, image_height), polygon):
            result.append(box.track_id if box.track_id is not None else -1)
    return result

