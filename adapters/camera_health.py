from __future__ import annotations

from dataclasses import dataclass
from math import log2
from time import monotonic
from typing import Sequence


@dataclass(frozen=True)
class CameraHealth:
    healthy: bool
    reason: str
    brightness: float | None
    entropy: float | None
    frame_age_ms: float


def grayscale_entropy(samples: Sequence[int]) -> float:
    if not samples:
        return 0.0
    histogram = [0] * 256
    for value in samples:
        histogram[max(0, min(255, int(value)))] += 1
    total = len(samples)
    return -sum(
        (count / total) * log2(count / total) for count in histogram if count
    )


class CameraHealthTracker:
    def __init__(
        self,
        frame_timeout_ms: int = 1000,
        black_brightness_threshold: float = 12.0,
        low_entropy_threshold: float = 1.5,
        frozen_frame_count: int = 15,
    ) -> None:
        self.frame_timeout_ms = frame_timeout_ms
        self.black_brightness_threshold = black_brightness_threshold
        self.low_entropy_threshold = low_entropy_threshold
        self.frozen_frame_count = frozen_frame_count
        self.last_frame_at: float | None = None
        self.last_signature: tuple[int, ...] | None = None
        self.same_signature_count = 0
        self.last_brightness: float | None = None
        self.last_entropy: float | None = None

    def observe(self, grayscale_samples: Sequence[int], now: float | None = None) -> None:
        current = monotonic() if now is None else now
        samples = tuple(int(v) for v in grayscale_samples)
        if not samples:
            return

        signature_stride = max(1, len(samples) // 64)
        signature = tuple(samples[::signature_stride][:64])
        if signature == self.last_signature:
            self.same_signature_count += 1
        else:
            self.same_signature_count = 0
            self.last_signature = signature

        self.last_brightness = sum(samples) / len(samples)
        self.last_entropy = grayscale_entropy(samples)
        self.last_frame_at = current

    def status(self, now: float | None = None) -> CameraHealth:
        current = monotonic() if now is None else now
        if self.last_frame_at is None:
            return CameraHealth(False, "NO_FRAME", None, None, float("inf"))

        age_ms = (current - self.last_frame_at) * 1000.0
        if age_ms > self.frame_timeout_ms:
            return CameraHealth(
                False,
                "FRAME_TIMEOUT",
                self.last_brightness,
                self.last_entropy,
                age_ms,
            )
        if (
            self.last_brightness is not None
            and self.last_brightness < self.black_brightness_threshold
        ):
            return CameraHealth(
                False,
                "LOW_BRIGHTNESS",
                self.last_brightness,
                self.last_entropy,
                age_ms,
            )
        if (
            self.last_entropy is not None
            and self.last_entropy < self.low_entropy_threshold
        ):
            return CameraHealth(
                False,
                "LOW_ENTROPY",
                self.last_brightness,
                self.last_entropy,
                age_ms,
            )
        if self.same_signature_count >= self.frozen_frame_count:
            return CameraHealth(
                False,
                "FROZEN_FRAME",
                self.last_brightness,
                self.last_entropy,
                age_ms,
            )
        return CameraHealth(
            True,
            "HEALTHY",
            self.last_brightness,
            self.last_entropy,
            age_ms,
        )

