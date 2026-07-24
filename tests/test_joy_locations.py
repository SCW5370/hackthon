import unittest

from joy.locations import (
    ARM_HOME,
    DESTINATION_COORDS,
    PLATFORM_HOME_WORLD,
    RFID_TAGS,
    SAMPLE_STORAGE_COORDS,
    SAMPLE_IDS,
    arm_target,
    platform_target,
    resolve_location,
)


class JoyLocationTests(unittest.TestCase):
    def test_named_locations_resolve_to_spread_warehouse_coordinates(self) -> None:
        self.assertEqual(
            resolve_location("sample-A", "cold-storage"), (-5.45, -5.55, 0.6)
        )
        self.assertEqual(
            resolve_location("sample-B", "cold-storage"), (-4.95, -5.55, 0.6)
        )
        self.assertEqual(
            resolve_location("sample-A", "analyzer-01"), (-0.25, -5.55, 0.6)
        )
        self.assertEqual(
            resolve_location("sample-A", "waste-bin"), (5.75, 3.45, 0.6)
        )
        self.assertEqual(
            resolve_location("sample-A", "quarantine-zone"), (-0.25, 3.45, 0.6)
        )
        self.assertEqual(PLATFORM_HOME_WORLD, (-7.0, 0.0, 0.0))
        self.assertEqual(ARM_HOME, (0.0, 0.0, 2.0))

    def test_platform_targets_are_relative_to_home(self) -> None:
        self.assertEqual(platform_target("cold-storage"), (0.4, -5.0, 0.0))
        self.assertEqual(platform_target("analyzer-01"), (5.6, -5.0, 0.0))
        self.assertEqual(platform_target("waste-bin"), (11.6, 4.0, 0.0))

    def test_arm_targets_are_local_to_each_dock(self) -> None:
        lower = arm_target("sample-A", "analyzer-01", above=False)
        above = arm_target("sample-A", "analyzer-01", above=True)
        self.assertAlmostEqual(lower[0], 1.15)
        self.assertAlmostEqual(lower[1], -0.55)
        self.assertGreater(above[2], lower[2])

    def test_mapping_constants_are_not_aliased(self) -> None:
        self.assertIsNot(SAMPLE_STORAGE_COORDS, DESTINATION_COORDS)

    def test_six_samples_have_unique_slots_and_rfid_tags(self) -> None:
        self.assertEqual(len(SAMPLE_IDS), 6)
        self.assertEqual(len(set(SAMPLE_STORAGE_COORDS.values())), 6)
        self.assertEqual(len(set(RFID_TAGS.values())), 6)
        for slots in DESTINATION_COORDS.values():
            self.assertEqual(len(set(slots.values())), 6)

    def test_invalid_sample_and_location_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_location("sample-Z", "cold-storage")
        with self.assertRaises(ValueError):
            platform_target("parking-lot")


if __name__ == "__main__":
    unittest.main()
