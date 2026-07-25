"""Canonical identifiers shared by Agent, Runtime, JOY, and Dashboard."""

from __future__ import annotations

from typing import Final


SAMPLE_IDS: Final[tuple[str, ...]] = tuple(
    f"sample-{letter}" for letter in "ABCDEF"
)
LOCATION_NAMES: Final[tuple[str, ...]] = (
    "cold-storage",
    "analyzer-01",
    "waste-bin",
    "quarantine-zone",
)
LINE_ID: Final[str] = "biolab-line-01"
LINE_RESOURCE_TYPE: Final[str] = "lab.line"
SAMPLE_RESOURCE_TYPE: Final[str] = "lab.sample"
TRANSFER_ACTION: Final[str] = "lab.sample.transfer"
RECYCLE_ACTION: Final[str] = "lab.sample.recycle"
RESET_ACTION: Final[str] = "lab.line.reset"
DISPLAY_LOCATION_NAMES: Final[dict[str, str]] = {
    "cold-storage": "等候区",
    "analyzer-01": "分析区",
    "waste-bin": "废弃区",
    "quarantine-zone": "隔离区",
    "home": "待机位",
}
