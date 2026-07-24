import unittest

from joy.command import JoyCommand


VALID_COMMAND = {
    "command_id": "cmd-001",
    "action": "TRANSFER",
    "sample_id": "sample-A",
    "source": "cold-storage",
    "destination": "analyzer-01",
}


class JoyCommandTests(unittest.TestCase):
    def test_expected_handoff_object_is_accepted(self) -> None:
        command = JoyCommand.from_mapping(VALID_COMMAND)
        self.assertEqual(command.to_dict(), VALID_COMMAND)

    def test_unknown_fields_and_routes_are_rejected_structurally(self) -> None:
        with self.assertRaises(ValueError):
            JoyCommand.from_mapping({**VALID_COMMAND, "raw_coordinates": [1, 2, 3]})
        with self.assertRaises(ValueError):
            JoyCommand.from_mapping({**VALID_COMMAND, "sample_id": "sample-Z"})
        with self.assertRaises(ValueError):
            JoyCommand.from_mapping({**VALID_COMMAND, "destination": "outside"})

    def test_legacy_route_is_not_blocked(self) -> None:
        command = JoyCommand.from_mapping(
            {**VALID_COMMAND, "destination": "waste-bin"}
        )
        self.assertEqual(command.destination, "waste-bin")


if __name__ == "__main__":
    unittest.main()
