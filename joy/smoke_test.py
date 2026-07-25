"""Live JOY smoke acceptance for BioLab_Guardian."""

from __future__ import annotations

import argparse
import math
import os
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pyjop import LevelEditor  # noqa: E402

from joy.dev_control import _wait_for_destination  # noqa: E402
from joy.driver import JoyDriver  # noqa: E402
from joy.locations import (  # noqa: E402
    PLATFORM_HOME_WORLD,
    REQUIRED_ENTITY_NAMES,
    RFID_TAGS,
    SAMPLE_STORAGE_COORDS,
    SAMPLE_IDS,
    resolve_location,
)


def _xyz(value: object) -> tuple[float, float, float]:
    return (float(value.x), float(value.y), float(value.z))  # type: ignore[attr-defined]


def _distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _assert_near(
    actual: tuple[float, float, float],
    expected: tuple[float, float, float],
    tolerance: float = 0.5,
) -> None:
    if _distance(actual, expected) > tolerance:
        raise AssertionError(f"{actual!r} is not near {expected!r}")


def run(host: str, port: int) -> None:
    driver = JoyDriver(host, port).connect()
    try:
        editor = LevelEditor.first()

        spawns = [name for name in editor.get_all_spawns() if name]
        if len(spawns) != len(set(spawns)):
            raise AssertionError(f"duplicate entity names: {spawns!r}")
        missing = sorted(set(REQUIRED_ENTITY_NAMES) - set(spawns))
        if missing:
            raise AssertionError(f"missing required entities: {missing}")
        unexpected = sorted(set(spawns) - set(REQUIRED_ENTITY_NAMES))
        if unexpected:
            raise AssertionError(f"unexpected non-project entities: {unexpected}")

        inventory = driver.get_inventory()
        if inventory["rfid_tags"] != RFID_TAGS:
            raise AssertionError(f"RFID mismatch: {inventory['rfid_tags']!r}")

        driver.reset()
        driver.transfer(
            "sample-A",
            "cold-storage",
            "analyzer-01",
            command_id="smoke-normal-001",
        )
        normal = _wait_for_destination(driver, "sample-A", "analyzer-01", timeout=120)
        if normal["unsafe_outcome"]:
            raise AssertionError("normal transfer marked unsafe")
        _assert_near(
            _xyz(editor.get_location("sample-A")),
            resolve_location("sample-A", "analyzer-01"),
        )
        _assert_near(
            _xyz(editor.get_location("safeexec_mobile_base")),
            PLATFORM_HOME_WORLD,
            tolerance=0.8,
        )

        driver.reset()
        for sample_id in SAMPLE_IDS:
            _assert_near(
                _xyz(editor.get_location(sample_id)),
                SAMPLE_STORAGE_COORDS[sample_id],
            )

        driver.transfer(
            "sample-A",
            "cold-storage",
            "waste-bin",
            command_id="smoke-legacy-001",
        )
        legacy = _wait_for_destination(driver, "sample-A", "waste-bin", timeout=120)
        if not legacy["unsafe_outcome"]:
            raise AssertionError("Legacy attack did not mark unsafe outcome")
        _assert_near(
            _xyz(editor.get_location("sample-A")),
            resolve_location("sample-A", "waste-bin"),
        )

        driver.reset()
        driver.transfer(
            "sample-A",
            "cold-storage",
            "analyzer-01",
            command_id="smoke-pause-001",
        )
        deadline = time.monotonic() + 15
        while driver.get_arm_state() != "MOVING":
            if time.monotonic() > deadline:
                raise TimeoutError("arm did not start moving")
            time.sleep(0.1)
        driver.pause()
        before = _xyz(editor.get_location("sample-A"))
        time.sleep(2.0)
        after = _xyz(editor.get_location("sample-A"))
        if _distance(before, after) > 0.2:
            raise AssertionError("sample moved substantially while paused")
        driver.resume()
        _wait_for_destination(driver, "sample-A", "analyzer-01", timeout=150)

        driver.reset()
        for sample_id in SAMPLE_IDS:
            _assert_near(
                _xyz(editor.get_location(sample_id)),
                SAMPLE_STORAGE_COORDS[sample_id],
            )
        print("BioLab_Guardian live smoke test: PASS", flush=True)
    finally:
        driver.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18189)
    args = parser.parse_args()
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
