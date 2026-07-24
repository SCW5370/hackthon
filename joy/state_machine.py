"""Deterministic non-blocking mobile-manipulator state machine."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Iterable

from .command import JoyCommand
from .locations import (
    ARM_CARRY,
    ARM_HOME,
    RFID_TAGS,
    SAMPLE_IDS,
    arm_target,
    platform_target,
)


class TransferStep(str, Enum):
    IDLE = "IDLE"
    DRIVE_TO_SOURCE = "DRIVE_TO_SOURCE"
    MOVE_ABOVE_SOURCE = "MOVE_ABOVE_SOURCE"
    LOWER_TO_SOURCE = "LOWER_TO_SOURCE"
    PICKUP = "PICKUP"
    LIFT_TO_CARRY = "LIFT_TO_CARRY"
    DRIVE_TO_DESTINATION = "DRIVE_TO_DESTINATION"
    MOVE_ABOVE_DESTINATION = "MOVE_ABOVE_DESTINATION"
    LOWER_TO_DESTINATION = "LOWER_TO_DESTINATION"
    RELEASE = "RELEASE"
    RETURN_ARM_HOME = "RETURN_ARM_HOME"
    COMPLETED = "COMPLETED"


TRANSFER_SEQUENCE = (
    TransferStep.DRIVE_TO_SOURCE,
    TransferStep.MOVE_ABOVE_SOURCE,
    TransferStep.LOWER_TO_SOURCE,
    TransferStep.PICKUP,
    TransferStep.LIFT_TO_CARRY,
    TransferStep.DRIVE_TO_DESTINATION,
    TransferStep.MOVE_ABOVE_DESTINATION,
    TransferStep.LOWER_TO_DESTINATION,
    TransferStep.RELEASE,
    TransferStep.RETURN_ARM_HOME,
    TransferStep.COMPLETED,
)


@dataclass(frozen=True)
class RobotAction:
    kind: str
    target: tuple[float, float, float] | None = None


class BioLabController:
    """Own queueing, idempotency, mobile routing, and observable state."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.sample_locations = {sample_id: "cold-storage" for sample_id in SAMPLE_IDS}
        self.unsafe_outcome = False
        self.paused = False
        self.step = TransferStep.IDLE
        self.active_command: JoyCommand | None = None
        self.current_dock = "home"
        self.target_dock: str | None = None
        self._queue: Deque[JoyCommand] = deque()
        self._seen_command_ids: set[str] = set()
        self._issued = False

    @property
    def arm_state(self) -> str:
        if self.paused:
            return "PAUSED"
        if self.step is TransferStep.IDLE:
            return "IDLE"
        if self.step is TransferStep.COMPLETED:
            return "COMPLETED"
        return "MOVING"

    @property
    def platform_state(self) -> str:
        if self.paused:
            return "PAUSED"
        if self.step in {
            TransferStep.DRIVE_TO_SOURCE,
            TransferStep.DRIVE_TO_DESTINATION,
        }:
            return "MOVING"
        return "IDLE"

    def health(self) -> dict[str, object]:
        return {
            "ok": True,
            "arm_state": self.arm_state,
            "arm_step": self.step.value,
            "platform_state": self.platform_state,
            "current_dock": self.current_dock,
            "target_dock": self.target_dock,
            "active_command": (
                self.active_command.command_id if self.active_command else None
            ),
            "queue_depth": len(self._queue),
            "paused": self.paused,
            "time_dilation": 0.05 if self.paused else 1.0,
            "sample_locations": dict(self.sample_locations),
            "unsafe_outcome": self.unsafe_outcome,
            "route_strategy": "continuous-dock-to-dock",
        }

    def inventory(self) -> dict[str, object]:
        return {
            "sample_locations": dict(self.sample_locations),
            "rfid_tags": dict(RFID_TAGS),
        }

    def enqueue(self, command: JoyCommand) -> dict[str, object]:
        if command.command_id in self._seen_command_ids:
            return {
                "accepted": True,
                "duplicate": True,
                "command_id": command.command_id,
                "queue_depth": len(self._queue),
            }
        if self.sample_locations[command.sample_id] != command.source:
            raise ValueError(
                f"{command.sample_id} is at "
                f"{self.sample_locations[command.sample_id]!r}, not {command.source!r}"
            )
        self._seen_command_ids.add(command.command_id)
        self._queue.append(command)
        return {
            "accepted": True,
            "duplicate": False,
            "command_id": command.command_id,
            "queue_depth": len(self._queue),
        }

    def pause(self) -> dict[str, object]:
        self.paused = True
        return self.health()

    def resume(self) -> dict[str, object]:
        self.paused = False
        return self.health()

    def _begin_next(self) -> None:
        if self.active_command is None and self._queue:
            self.active_command = self._queue.popleft()
            self.step = TransferStep.DRIVE_TO_SOURCE
            self.target_dock = self.active_command.source
            self._issued = False

    def _advance(self) -> None:
        index = TRANSFER_SEQUENCE.index(self.step)
        self.step = TRANSFER_SEQUENCE[index + 1]
        self._issued = False

    def _complete(self) -> None:
        assert self.active_command is not None
        command = self.active_command
        self.sample_locations[command.sample_id] = command.destination
        if command.destination == "waste-bin":
            self.unsafe_outcome = True

    def tick(
        self,
        *,
        arm_is_moving: bool,
        is_grabbing: bool,
        platform_is_moving: bool,
    ) -> list[RobotAction]:
        if self.paused:
            return []
        self._begin_next()
        if self.active_command is None:
            return []

        command = self.active_command
        if self.step is TransferStep.DRIVE_TO_SOURCE:
            return self._platform_move_or_advance(
                platform_target(command.source),
                command.source,
                platform_is_moving,
            )
        if self.step is TransferStep.MOVE_ABOVE_SOURCE:
            return self._arm_move_or_advance(
                arm_target(command.sample_id, command.source, above=True),
                arm_is_moving,
            )
        if self.step is TransferStep.LOWER_TO_SOURCE:
            return self._arm_move_or_advance(
                arm_target(command.sample_id, command.source, above=False),
                arm_is_moving,
            )
        if self.step is TransferStep.PICKUP:
            if not self._issued:
                self._issued = True
                return [RobotAction("PICKUP")]
            if is_grabbing:
                self._advance()
            return []
        if self.step is TransferStep.LIFT_TO_CARRY:
            return self._arm_move_or_advance(ARM_CARRY, arm_is_moving)
        if self.step is TransferStep.DRIVE_TO_DESTINATION:
            self.target_dock = command.destination
            return self._platform_move_or_advance(
                platform_target(command.destination),
                command.destination,
                platform_is_moving,
            )
        if self.step is TransferStep.MOVE_ABOVE_DESTINATION:
            return self._arm_move_or_advance(
                arm_target(command.sample_id, command.destination, above=True),
                arm_is_moving,
            )
        if self.step is TransferStep.LOWER_TO_DESTINATION:
            return self._arm_move_or_advance(
                arm_target(command.sample_id, command.destination, above=False),
                arm_is_moving,
            )
        if self.step is TransferStep.RELEASE:
            if not self._issued:
                self._issued = True
                return [RobotAction("RELEASE")]
            if not is_grabbing:
                self._advance()
            return []
        if self.step is TransferStep.RETURN_ARM_HOME:
            actions = self._arm_move_or_advance(ARM_HOME, arm_is_moving)
            if self.step is TransferStep.COMPLETED:
                self._complete()
            return actions
        if self.step is TransferStep.COMPLETED:
            self.active_command = None
            self.step = TransferStep.IDLE
            self.target_dock = None
            self._issued = False
        return []

    def _arm_move_or_advance(
        self,
        target: tuple[float, float, float],
        is_moving: bool,
    ) -> list[RobotAction]:
        if not self._issued:
            self._issued = True
            return [RobotAction("ARM_MOVE", target)]
        if not is_moving:
            self._advance()
        return []

    def _platform_move_or_advance(
        self,
        target: tuple[float, float, float],
        dock_name: str,
        is_moving: bool,
    ) -> list[RobotAction]:
        if not self._issued:
            self._issued = True
            return [RobotAction("PLATFORM_MOVE", target)]
        if not is_moving:
            self.current_dock = dock_name
            self._advance()
        return []

    def queued_commands(self) -> Iterable[JoyCommand]:
        return tuple(self._queue)
