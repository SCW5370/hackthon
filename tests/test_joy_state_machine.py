import unittest

from biolab.catalog import SAMPLE_IDS
from joy.command import JoyCommand
from joy.state_machine import BioLabController, TRANSFER_SEQUENCE, TransferStep


def command(
    command_id: str = "cmd-001",
    sample_id: str = "sample-A",
    destination: str = "analyzer-01",
) -> JoyCommand:
    return JoyCommand(
        command_id=command_id,
        action="TRANSFER",
        sample_id=sample_id,
        source="cold-storage",
        destination=destination,
    )


def finish_active(controller: BioLabController) -> list[TransferStep]:
    observed: list[TransferStep] = []
    grabbing = False
    for _ in range(160):
        previous = controller.step
        actions = controller.tick(
            arm_is_moving=False,
            is_grabbing=grabbing,
            platform_is_moving=False,
        )
        if controller.step != previous and controller.step is not TransferStep.IDLE:
            observed.append(controller.step)
        for action in actions:
            if action.kind == "PICKUP":
                grabbing = True
            elif action.kind == "RELEASE":
                grabbing = False
        if controller.step is TransferStep.IDLE and controller.active_command is None:
            return observed
    raise AssertionError("state machine did not complete")


class JoyStateMachineTests(unittest.TestCase):
    def test_mobile_state_machine_order_and_normal_outcome(self) -> None:
        controller = BioLabController()
        controller.enqueue(command())
        observed = finish_active(controller)
        self.assertEqual(observed, list(TRANSFER_SEQUENCE))
        self.assertEqual(controller.sample_locations["sample-A"], "analyzer-01")
        self.assertEqual(controller.current_dock, "analyzer-01")
        self.assertFalse(controller.unsafe_outcome)
        self.assertNotIn("DRIVE_HOME", [step.value for step in observed])

    def test_drive_steps_emit_platform_actions(self) -> None:
        controller = BioLabController()
        controller.enqueue(command())
        actions = controller.tick(
            arm_is_moving=False,
            is_grabbing=False,
            platform_is_moving=False,
        )
        self.assertEqual(actions[0].kind, "PLATFORM_MOVE")
        self.assertEqual(actions[0].target, (0.4, -5.0, 0.0))

    def test_duplicate_command_id_is_not_queued_twice(self) -> None:
        controller = BioLabController()
        first = controller.enqueue(command())
        duplicate = controller.enqueue(command())
        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(tuple(controller.queued_commands())), 1)

    def test_pause_resume_and_reset(self) -> None:
        controller = BioLabController()
        controller.enqueue(command())
        paused = controller.pause()
        self.assertEqual(paused["arm_state"], "PAUSED")
        self.assertEqual(paused["platform_state"], "PAUSED")
        self.assertEqual(
            controller.tick(
                arm_is_moving=False,
                is_grabbing=False,
                platform_is_moving=False,
            ),
            [],
        )
        controller.resume()
        finish_active(controller)
        controller.reset()
        self.assertEqual(
            controller.sample_locations,
            {sample_id: "cold-storage" for sample_id in SAMPLE_IDS},
        )
        self.assertEqual(controller.current_dock, "home")
        self.assertFalse(controller.unsafe_outcome)

    def test_unsafe_outcomes_are_observable_not_blocked(self) -> None:
        waste = BioLabController()
        waste.enqueue(command(destination="waste-bin"))
        finish_active(waste)
        self.assertTrue(waste.unsafe_outcome)

        sample_b = BioLabController()
        sample_b.enqueue(command(sample_id="sample-B"))
        finish_active(sample_b)
        self.assertFalse(sample_b.unsafe_outcome)

    def test_queue_runs_all_six_commands_without_exiting(self) -> None:
        controller = BioLabController()
        for index, sample_id in enumerate(SAMPLE_IDS):
            controller.enqueue(command(f"cmd-{index}", sample_id))
        for _ in SAMPLE_IDS:
            finish_active(controller)
        self.assertEqual(
            controller.sample_locations,
            {sample_id: "analyzer-01" for sample_id in SAMPLE_IDS},
        )
        self.assertEqual(controller.arm_state, "IDLE")

    def test_source_must_match_current_inventory(self) -> None:
        controller = BioLabController()
        controller.sample_locations["sample-A"] = "analyzer-01"
        with self.assertRaises(ValueError):
            controller.enqueue(command())

    def test_analyzed_entity_can_be_recycled_only_while_idle(self) -> None:
        controller = BioLabController()
        controller.enqueue(command())
        with self.assertRaises(RuntimeError):
            controller.recycle("sample-A")
        finish_active(controller)
        result = controller.recycle("sample-A")
        self.assertEqual(result["sample_locations"]["sample-A"], "cold-storage")

        with self.assertRaises(ValueError):
            controller.recycle("sample-A")


if __name__ == "__main__":
    unittest.main()
