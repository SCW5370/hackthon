"""Local-only normal and Legacy baseline scenarios for BioLab_Guardian."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from .driver import JoyDriver


def _wait_for_destination(
    driver: JoyDriver,
    sample_id: str,
    destination: str,
    timeout: float = 45.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = driver.health()
    while time.monotonic() < deadline:
        last = driver.health()
        locations = last.get("sample_locations", {})
        if locations.get(sample_id) == destination and last.get("arm_state") in {
            "COMPLETED",
            "IDLE",
        }:
            return last
        time.sleep(0.25)
    raise TimeoutError(
        f"{sample_id} did not reach {destination}; last status={last!r}"
    )


def run_normal(driver: JoyDriver) -> dict[str, Any]:
    driver.reset()
    command = driver.transfer(
        "sample-A",
        "cold-storage",
        "analyzer-01",
        command_id="cmd-normal-001",
    )
    final = _wait_for_destination(driver, "sample-A", "analyzer-01")
    if final["unsafe_outcome"]:
        raise AssertionError("normal route unexpectedly produced unsafe_outcome=true")
    return {"command": command, "final": final}


def run_legacy_attack(driver: JoyDriver) -> dict[str, Any]:
    driver.reset()
    command = driver.transfer(
        "sample-A",
        "cold-storage",
        "waste-bin",
        command_id="cmd-legacy-001",
    )
    final = _wait_for_destination(driver, "sample-A", "waste-bin")
    if final["sample_locations"]["sample-A"] != "waste-bin":
        raise AssertionError("Legacy baseline did not move sample-A to waste-bin")
    if final["unsafe_outcome"] is not True:
        raise AssertionError("Legacy baseline did not set unsafe_outcome=true")
    return {"command": command, "final": final}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18189)
    subparsers = parser.add_subparsers(dest="scenario", required=True)
    subparsers.add_parser("normal")
    legacy = subparsers.add_parser("legacy-attack")
    legacy.add_argument(
        "--confirm-unsafe-demo",
        action="store_true",
        help="required acknowledgement for the intentionally unsafe simulation",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.scenario == "legacy-attack" and not args.confirm_unsafe_demo:
        raise SystemExit(
            "refusing unsafe simulation: add --confirm-unsafe-demo explicitly"
        )

    driver = JoyDriver(args.host, args.port).connect()
    if args.scenario == "normal":
        result = run_normal(driver)
    else:
        result = run_legacy_attack(driver)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    driver.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
