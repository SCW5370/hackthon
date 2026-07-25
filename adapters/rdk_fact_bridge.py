#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from math import isfinite
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import cv2
import numpy as np
import rclpy
from ai_msgs.msg import PerceptionTargets
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, CompressedImage

from adapters.camera_health import CameraHealthTracker
from adapters.vision_adapter_core import (
    ZoneTracker,
    body_boxes_from_targets,
    grayscale_samples_from_image,
)


class FactSink:
    def __init__(self, fact_url: str, frame_url: str | None) -> None:
        self.fact_url = fact_url
        self.frame_url = frame_url

    def publish(self, fact: dict) -> None:
        request = Request(
            self.fact_url,
            data=json.dumps(fact, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=0.35) as response:
            if response.status not in {200, 202}:
                raise RuntimeError(f"Fact endpoint returned HTTP {response.status}")

    def publish_frame(self, data: bytes) -> None:
        if not self.frame_url:
            return
        request = Request(
            self.frame_url,
            data=data,
            headers={"Content-Type": "image/jpeg"},
            method="POST",
        )
        with urlopen(request, timeout=0.35) as response:
            if response.status not in {200, 202}:
                raise RuntimeError(f"Frame endpoint returned HTTP {response.status}")


class RdkFactBridge(Node):
    def __init__(self, config: dict, args: argparse.Namespace) -> None:
        super().__init__("safeexec_rdk_fact_bridge")
        camera = config["camera"]
        vision = config["vision"]
        self.sink = FactSink(args.fact_url, args.frame_url)
        self.camera = CameraHealthTracker(
            frame_timeout_ms=camera["frame_timeout_ms"],
            black_brightness_threshold=camera["black_brightness_threshold"],
            low_entropy_threshold=camera["low_entropy_threshold"],
            frozen_frame_count=camera["frozen_frame_count"],
        )
        self.zone = ZoneTracker(
            polygon=config["danger_zone"],
            image_width=args.image_width,
            image_height=args.image_height,
            minimum_confidence=vision["minimum_confidence"],
            enter_frames=vision["enter_frames"],
            clear_after_ms=vision["clear_after_ms"],
            detection_timeout_ms=vision.get("detection_timeout_ms", 1000),
        )
        self.last_frame_id = ""
        self.frame_count = 0
        self.frame_upload_every = max(1, args.frame_upload_every)
        self.last_error_log_at = 0.0
        self.create_subscription(
            CameraInfo, args.camera_info_topic, self.on_camera_info, 10
        )
        self.create_subscription(
            CompressedImage, args.image_topic, self.on_compressed_image, 10
        )
        self.create_subscription(
            PerceptionTargets, args.detection_topic, self.on_detection, 10
        )
        self.create_timer(args.publish_interval_ms / 1000.0, self.publish_facts)
        self.get_logger().info(
            f"SafeExec bridge ready: {args.detection_topic} -> {args.fact_url}"
        )

    def on_camera_info(self, message: CameraInfo) -> None:
        self.zone.set_dimensions(int(message.width), int(message.height))

    def on_compressed_image(self, message: CompressedImage) -> None:
        self.last_frame_id = message.header.frame_id
        self.frame_count += 1
        jpeg = bytes(message.data)
        if self.frame_count % self.frame_upload_every == 0:
            try:
                self.sink.publish_frame(jpeg)
            except (OSError, URLError, RuntimeError) as error:
                self.report_sink_error(error)
        if self.frame_count % 5:
            return
        encoded = np.frombuffer(jpeg, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
        if image is None:
            return
        samples = grayscale_samples_from_image(image.tobytes(), "mono8")
        self.camera.observe(samples)

    def on_detection(self, message: PerceptionTargets) -> None:
        boxes = body_boxes_from_targets(message.targets)
        self.zone.observe(boxes)

    def publish_facts(self) -> None:
        now = time.time()
        zone = self.zone.status()
        camera = self.camera.status()
        facts = (
            {
                "key": "zone.clear",
                "value": zone.value,
                "source": "rdk-x5/mono2d-body",
                "confidence": 1.0,
                "timestamp": now,
                "ttl_ms": 500,
                "evidence": {
                    "reason": zone.reason,
                    "track_ids": list(zone.track_ids),
                    "image_width": self.zone.image_width,
                    "image_height": self.zone.image_height,
                },
            },
            {
                "key": "camera.healthy",
                "value": camera.healthy,
                "source": "rdk-x5/camera-health",
                "confidence": 1.0,
                "timestamp": now,
                "ttl_ms": 1000,
                "evidence": {
                    "reason": camera.reason,
                    "frame_id": self.last_frame_id,
                    "frame_age_ms": (
                        round(camera.frame_age_ms, 1)
                        if isfinite(camera.frame_age_ms)
                        else None
                    ),
                    "brightness": camera.brightness,
                    "entropy": camera.entropy,
                },
            },
        )
        try:
            for fact in facts:
                self.sink.publish(fact)
        except (OSError, URLError, RuntimeError) as error:
            self.report_sink_error(error)

    def report_sink_error(self, error: Exception) -> None:
        monotonic_now = time.monotonic()
        if monotonic_now - self.last_error_log_at >= 5:
            self.get_logger().warning(f"Mac sink unavailable: {error}")
            self.last_error_log_at = monotonic_now


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--fact-url", default="http://192.168.128.20:8790/v1/facts"
    )
    parser.add_argument(
        "--frame-url", default=""
    )
    parser.add_argument("--image-topic", default="/image")
    parser.add_argument("--camera-info-topic", default="/camera_info")
    parser.add_argument(
        "--detection-topic", default="/hobot_mono2d_body_detection"
    )
    parser.add_argument("--image-width", type=int, default=640)
    parser.add_argument("--image-height", type=int, default=480)
    parser.add_argument("--publish-interval-ms", type=int, default=250)
    parser.add_argument("--frame-upload-every", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = json.loads(Path(args.config).read_text())
    rclpy.init(args=sys.argv[:1])
    node = RdkFactBridge(config, args)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
