"""Named warehouse stations, platform docks, and arm-local coordinates."""

from __future__ import annotations

from typing import Final


Vector3 = tuple[float, float, float]

PLATFORM_HOME_WORLD: Final[Vector3] = (-7.0, 0.0, 0.0)
PLATFORM_HOME_RELATIVE: Final[Vector3] = (0.0, 0.0, 0.0)
ARM_HOME: Final[Vector3] = (0.0, 0.0, 2.0)
ARM_CARRY: Final[Vector3] = (1.4, 0.0, 1.45)
ARM_BASE_WORLD_Z: Final[float] = 0.4

SAMPLE_IDS: Final[tuple[str, ...]] = ("sample-A", "sample-B")
LOCATION_NAMES: Final[tuple[str, ...]] = (
    "cold-storage",
    "analyzer-01",
    "waste-bin",
    "quarantine-zone",
)

# Every station is 1.4 m east of its dock. The platform never rotates, keeping
# arm targets stable and visually placing the arm between the lane and station.
DOCK_COORDS: Final[dict[str, Vector3]] = {
    "cold-storage": (-6.6, -5.0, 0.0),
    "analyzer-01": (-1.4, -5.0, 0.0),
    "quarantine-zone": (-1.4, 4.0, 0.0),
    "waste-bin": (4.6, 4.0, 0.0),
}

SAMPLE_STORAGE_COORDS: Final[dict[str, Vector3]] = {
    "sample-A": (-5.2, -5.35, 0.6),
    "sample-B": (-5.2, -4.65, 0.6),
}

DESTINATION_COORDS: Final[dict[str, Vector3]] = {
    "analyzer-01": (0.0, -5.0, 0.6),
    "quarantine-zone": (0.0, 4.0, 0.6),
    "waste-bin": (6.0, 4.0, 0.6),
}

RFID_TAGS: Final[dict[str, str]] = {
    "sample-A": "LAB:SAMPLE:A",
    "sample-B": "LAB:SAMPLE:B",
}

REQUIRED_ENTITY_NAMES: Final[tuple[str, ...]] = (
    "transport-lane-0",
    "transport-lane-1",
    "transport-lane-2",
    "transport-lane-3",
    "safeexec_mobile_base",
    "safeexec_arm",
    "safeexec_exchange",
    "safeexec_rfid",
    "safeexec_status",
    "sample-A",
    "sample-B",
    "cold-storage",
    "analyzer-01",
    "waste-bin",
    "quarantine-zone",
)


def validate_sample_id(sample_id: str) -> str:
    if sample_id not in SAMPLE_IDS:
        raise ValueError(f"unknown sample_id: {sample_id!r}")
    return sample_id


def validate_location_name(location: str) -> str:
    if location not in LOCATION_NAMES:
        raise ValueError(f"unknown location: {location!r}")
    return location


def resolve_location(sample_id: str, location: str) -> Vector3:
    """Resolve a logical sample location to its world coordinate."""

    validate_sample_id(sample_id)
    validate_location_name(location)
    if location == "cold-storage":
        return SAMPLE_STORAGE_COORDS[sample_id]
    return DESTINATION_COORDS[location]


def platform_target(location: str) -> Vector3:
    """Return a MovablePlatform target relative to its warehouse home."""

    dock_x, dock_y, dock_z = DOCK_COORDS[validate_location_name(location)]
    home_x, home_y, home_z = PLATFORM_HOME_WORLD
    return (
        round(dock_x - home_x, 6),
        round(dock_y - home_y, 6),
        round(dock_z - home_z, 6),
    )


def arm_target(sample_id: str, location: str, *, above: bool) -> Vector3:
    """Return the grabber target relative to the arm at the station dock."""

    world_x, world_y, world_z = resolve_location(sample_id, location)
    dock_x, dock_y, _ = DOCK_COORDS[validate_location_name(location)]
    height_offset = 0.85 if above else 0.08
    return (
        world_x - dock_x,
        world_y - dock_y,
        world_z - ARM_BASE_WORLD_Z + height_offset,
    )
