import unittest

from joy.locations import (
    ARM_HOME,
    DESTINATION_COORDS,
    PLATFORM_HOME_WORLD,
    SAMPLE_STORAGE_COORDS,
    arm_target,
    platform_target,
    resolve_location,
)


class JoyLocationTests(unittest.TestCase):
    def test_named_locations_resolve_to_spread_warehouse_coordinates(self) -> None:
        self.assertEqual(
            resolve_location("sample-A", "cold-storage"), (-5.2, -5.35, 0.6)
        )
        self.assertEqual(
            resolve_location("sample-B", "cold-storage"), (-5.2, -4.65, 0.6)
        )
        self.assertEqual(
            resolve_location("sample-A", "analyzer-01"), (0.0, -5.0, 0.6)
        )
        self.assertEqual(
            resolve_location("sample-A", "waste-bin"), (6.0, 4.0, 0.6)
        )
        self.assertEqual(
            resolve_location("sample-A", "quarantine-zone"), (0.0, 4.0, 0.6)
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
        self.assertAlmostEqual(lower[0], 1.4)
        self.assertAlmostEqual(lower[1], 0.0)
        self.assertGreater(above[2], lower[2])

    def test_mapping_constants_are_not_aliased(self) -> None:
        self.assertIsNot(SAMPLE_STORAGE_COORDS, DESTINATION_COORDS)

    def test_invalid_sample_and_location_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_location("sample-C", "cold-storage")
        with self.assertRaises(ValueError):
            platform_target("parking-lot")


if __name__ == "__main__":
    unittest.main()
