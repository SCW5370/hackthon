from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Iterable, Sequence

from adapters.vision_geometry import BoundingBox, tracks_in_zone


@dataclass(frozen=True)
class ZoneObservation:
    value: bool | None
    reason: str
    track_ids: tuple[int, ...]


def body_boxes_from_targets(targets: Iterable[object]) -> list[BoundingBox]:
    """Convert ai_msgs Target-like objects into dependency-free boxes."""
    boxes: list[BoundingBox] = []
    for target in targets:
        track_id = int(getattr(target, "track_id", -1))
        rois = list(getattr(target, "rois", ()))
        body_rois = [roi for roi in rois if getattr(roi, "type", "") == "body"]
        if not body_rois and getattr(target, "type", "") in {"person", "body"}:
            body_rois = rois

        for roi in body_rois:
            rect = getattr(roi, "rect")
            boxes.append(
                BoundingBox(
                    x=float(getattr(rect, "x_offset")),
                    y=float(getattr(rect, "y_offset")),
                    width=float(getattr(rect, "width")),
                    height=float(getattr(rect, "height")),
                    confidence=float(getattr(roi, "confidence", 1.0)),
                    track_id=track_id,
                )
            )
    return boxes


def grayscale_samples_from_image(
    data: bytes | bytearray | memoryview,
    encoding: str,
    sample_limit: int = 1024,
) -> list[int]:
    """Sample common ROS image encodings without requiring OpenCV or NumPy."""
    raw = memoryview(data)
    normalized = encoding.lower()
    channels = 1 if normalized in {"mono8", "8uc1"} else 3
    if normalized in {"rgba8", "bgra8"}:
        channels = 4
    if len(raw) < channels:
        return []

    pixel_count = len(raw) // channels
    pixel_stride = max(1, pixel_count // sample_limit)
    byte_stride = pixel_stride * channels
    samples: list[int] = []
    for offset in range(0, len(raw) - channels + 1, byte_stride):
        if channels == 1:
            samples.append(int(raw[offset]))
        else:
            samples.append(
                sum(int(raw[offset + index]) for index in range(3)) // 3
            )
        if len(samples) >= sample_limit:
            break
    return samples


class ZoneTracker:
    def __init__(
        self,
        polygon: Sequence[tuple[float, float]],
        image_width: int,
        image_height: int,
        minimum_confidence: float = 0.5,
        enter_frames: int = 2,
        clear_after_ms: int = 500,
        detection_timeout_ms: int = 1000,
    ) -> None:
        self.polygon = tuple(polygon)
        self.image_width = image_width
        self.image_height = image_height
        self.minimum_confidence = minimum_confidence
        self.enter_frames = max(1, enter_frames)
        self.clear_after_ms = clear_after_ms
        self.detection_timeout_ms = detection_timeout_ms
        self.last_detection_at: float | None = None
        self.last_occupied_at: float | None = None
        self.occupied_frames = 0
        self.track_ids: tuple[int, ...] = ()
        self.clear = True

    def set_dimensions(self, width: int, height: int) -> None:
        if width > 0 and height > 0:
            self.image_width = width
            self.image_height = height

    def observe(
        self, boxes: Iterable[BoundingBox], now: float | None = None
    ) -> ZoneObservation:
        current = monotonic() if now is None else now
        inside = tuple(
            tracks_in_zone(
                boxes,
                self.polygon,
                self.image_width,
                self.image_height,
                self.minimum_confidence,
            )
        )
        self.last_detection_at = current

        if inside:
            self.occupied_frames += 1
            self.last_occupied_at = current
            self.track_ids = inside
            if self.occupied_frames >= self.enter_frames:
                self.clear = False
        else:
            self.occupied_frames = 0
            if (
                self.last_occupied_at is None
                or (current - self.last_occupied_at) * 1000 >= self.clear_after_ms
            ):
                self.clear = True
                self.track_ids = ()
        return self.status(current)

    def status(self, now: float | None = None) -> ZoneObservation:
        current = monotonic() if now is None else now
        if self.last_detection_at is None:
            return ZoneObservation(None, "NO_DETECTION_MESSAGE", ())
        age_ms = (current - self.last_detection_at) * 1000
        if age_ms > self.detection_timeout_ms:
            return ZoneObservation(None, "DETECTION_TIMEOUT", ())
        if self.clear:
            return ZoneObservation(True, "CLEAR", ())
        return ZoneObservation(False, "TRACK_IN_ZONE", self.track_ids)
