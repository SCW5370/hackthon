"""Generate and run the mobile BioLab Guardian warehouse level."""

from __future__ import annotations

import os
import sys
from typing import Any


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pyjop import (  # noqa: E402
    Colors,
    DataExchange,
    LEDStrip,
    LevelEditor,
    MovablePlatform,
    RangeFinder,
    RobotArm,
    SimEnv,
    SimEnvManager,
    SpawnableEntities,
    SpawnableMaps,
    SpawnableMaterials,
    SpawnableMeshes,
    sleep,
)

from joy.command import JoyCommand  # noqa: E402
from joy.locations import (  # noqa: E402
    ARM_BASE_WORLD_Z,
    ARM_HOME,
    DESTINATION_COORDS,
    PLATFORM_HOME_RELATIVE,
    PLATFORM_HOME_WORLD,
    REQUIRED_ENTITY_NAMES,
    RFID_TAGS,
    SAMPLE_STORAGE_COORDS,
)
from joy.state_machine import BioLabController, RobotAction  # noqa: E402


SimEnv.connect("127.0.0.1", 18189)
editor = LevelEditor.first()
env = SimEnvManager.first()
controller = BioLabController()

arm: RobotArm | None = None
mobile_base: MovablePlatform | None = None
exchange: DataExchange | None = None
status_light: LEDStrip | None = None
platform_move_until: float | None = None
PLATFORM_MOVE_DURATION = 3.0


def _spawn_floor(
    name: str,
    location: tuple[float, float, float],
    color: Colors,
    scale: tuple[float, float, float],
) -> None:
    editor.spawn_static_mesh(
        SpawnableMeshes.Cube,
        unique_name=name,
        location=location,
        scale=scale,
        material=SpawnableMaterials.SimpleColor,
        color=color,
    )


def _build_lanes() -> None:
    lane_segments = (
        ((-7.0, -2.5, 0.025), (0.12, 2.5, 0.025)),
        ((-4.0, -5.0, 0.025), (2.6, 0.12, 0.025)),
        ((-1.4, -0.5, 0.025), (0.12, 4.5, 0.025)),
        ((1.6, 4.0, 0.025), (3.0, 0.12, 0.025)),
    )
    for index, (location, scale) in enumerate(lane_segments):
        _spawn_floor(f"transport-lane-{index}", location, Colors.Silver, scale)


def _build_stations() -> None:
    _spawn_floor(
        "cold-storage",
        (-5.2, -5.0, 0.12),
        Colors.Lightblue,
        (1.0, 1.25, 0.12),
    )

    _spawn_floor(
        "analyzer-01",
        (0.0, -5.0, 0.12),
        Colors.Silver,
        (1.0, 1.1, 0.12),
    )

    _spawn_floor(
        "quarantine-zone",
        (0.0, 4.0, 0.08),
        Colors.Gold,
        (1.1, 1.2, 0.08),
    )

    _spawn_floor(
        "waste-bin",
        (6.0, 4.0, 0.12),
        Colors.Firebrick,
        (1.1, 1.25, 0.12),
    )


editor.clear_all()
sleep(0.5)
editor.select_map(SpawnableMaps.SmallWarehouse)
_build_lanes()
_build_stations()

editor.spawn_static_mesh(
    SpawnableMeshes.Cylinder,
    unique_name="sample-A",
    location=SAMPLE_STORAGE_COORDS["sample-A"],
    scale=(0.22, 0.22, 0.45),
    material=SpawnableMaterials.SimpleColor,
    color=Colors.Blue,
    rfid_tag=RFID_TAGS["sample-A"],
)
editor.spawn_static_mesh(
    SpawnableMeshes.Cylinder,
    unique_name="sample-B",
    location=SAMPLE_STORAGE_COORDS["sample-B"],
    scale=(0.22, 0.22, 0.45),
    material=SpawnableMaterials.SimpleColor,
    color=Colors.Yellow,
    rfid_tag=RFID_TAGS["sample-B"],
)

editor.spawn_entity(
    SpawnableEntities.MovablePlatform,
    unique_name="safeexec_mobile_base",
    location=PLATFORM_HOME_WORLD,
    scale=(1.5, 1.5, 0.45),
)
editor.spawn_entity(
    SpawnableEntities.RobotArm,
    unique_name="safeexec_arm",
    location=(PLATFORM_HOME_WORLD[0], PLATFORM_HOME_WORLD[1], 0.4),
)
editor.spawn_entity(
    SpawnableEntities.DataExchange,
    unique_name="safeexec_exchange",
    location=(-8.7, -0.8, 0.0),
)
editor.spawn_entity(
    SpawnableEntities.RangeFinder,
    unique_name="safeexec_rfid",
    location=(0.0, -5.8, 0.8),
    rotation=(0.0, 0.0, 90.0),
)
editor.spawn_entity(
    SpawnableEntities.LEDStrip,
    unique_name="safeexec_status",
    location=(-8.4, 0.8, 1.2),
)

sleep(1.0)
arm = RobotArm.find("safeexec_arm")
mobile_base = MovablePlatform.find("safeexec_mobile_base")
exchange = DataExchange.find("safeexec_exchange")
status_light = LEDStrip.find("safeexec_status")
rfid_reader = RangeFinder.find("safeexec_rfid")

mobile_base.editor_set_location_limits((14.0, 7.0, 0.0))
mobile_base.editor_set_rotation_limits((0.0, 0.0, 0.0))
mobile_base.editor_set_movement_speed(8.0)
mobile_base.editor_set_block_collisions(False)

rfid_reader.editor_set_can_read_rfid_tags(True)
rfid_reader.editor_set_can_read_entities(True)
rfid_reader.editor_set_max_range(300.0)
arm.editor_set_size_limit(0.8)
arm.editor_set_can_carry_non_physics(True)
arm.editor_set_block_collisions(False)
arm.editor_set_carry_collisions(False)


def _set_status_color(color: Colors) -> None:
    if status_light is not None:
        status_light.set_all_leds([(color, 1.0)] * 12)


def _published_status() -> dict[str, Any]:
    payload = controller.health()
    payload["level"] = "BioLab_Guardian_Warehouse"
    payload["required_entities"] = list(REQUIRED_ENTITY_NAMES)
    payload["physical_positions"] = {
        "mobile_base": editor.get_location("safeexec_mobile_base").tolist(),
        "arm_base": editor.get_location("safeexec_arm").tolist(),
    }
    return payload


def _publish() -> None:
    assert exchange is not None
    exchange.set_data("biolab_status", _published_status())
    exchange.set_data("biolab_inventory", controller.inventory())


def _reset_runtime() -> dict[str, Any]:
    global platform_move_until
    assert arm is not None
    assert mobile_base is not None
    controller.reset()
    platform_move_until = None
    arm.release()
    editor.set_location("safeexec_mobile_base", PLATFORM_HOME_WORLD)
    editor.set_location(
        "safeexec_arm",
        (PLATFORM_HOME_WORLD[0], PLATFORM_HOME_WORLD[1], ARM_BASE_WORLD_Z),
    )
    editor.set_location("sample-A", SAMPLE_STORAGE_COORDS["sample-A"])
    editor.set_location("sample-B", SAMPLE_STORAGE_COORDS["sample-B"])
    arm.set_grabber_location(ARM_HOME)
    env.set_time_dilation(1.0)
    _set_status_color(Colors.Green)
    _publish()
    return controller.health()


def _parse_transfer_request(args: tuple[Any, ...], kwargs: dict[str, Any]) -> JoyCommand:
    if len(args) == 1 and not kwargs and isinstance(args[0], dict):
        value = args[0]
    elif not args and isinstance(kwargs.get("command"), dict):
        value = kwargs["command"]
    else:
        raise ValueError("transfer expects exactly one command object")
    return JoyCommand.from_mapping(value)


def _handle_rpc(sender: DataExchange, request: Any) -> None:
    name = request.func_name
    try:
        if name == "health":
            result = controller.health()
        elif name == "inventory":
            result = controller.inventory()
        elif name == "transfer":
            command = _parse_transfer_request(request.args, request.kwargs)
            result = controller.enqueue(command)
        elif name == "pause":
            result = controller.pause()
            env.set_time_dilation(0.05)
            _set_status_color(Colors.Blue)
        elif name == "resume":
            env.set_time_dilation(1.0)
            result = controller.resume()
            _set_status_color(Colors.Yellow if controller.active_command else Colors.Green)
        elif name == "reset":
            result = _reset_runtime()
        else:
            raise ValueError(f"unknown RPC: {name!r}")
        response = {"ok": True, **result}
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        response = {"ok": False, "error": str(exc)}
    sender.return_rpc(name, response)
    _publish()


def _execute(actions: list[RobotAction], simtime: float) -> None:
    global platform_move_until
    assert arm is not None
    assert mobile_base is not None
    for action in actions:
        if action.kind == "ARM_MOVE":
            assert action.target is not None
            arm.set_grabber_location(action.target)
        elif action.kind == "PLATFORM_MOVE":
            assert action.target is not None
            world_target = tuple(
                PLATFORM_HOME_WORLD[index] + action.target[index]
                for index in range(3)
            )
            editor.set_location(
                "safeexec_mobile_base",
                world_target,
                duration=PLATFORM_MOVE_DURATION,
            )
            editor.set_location(
                "safeexec_arm",
                (world_target[0], world_target[1], ARM_BASE_WORLD_Z),
                duration=PLATFORM_MOVE_DURATION,
            )
            platform_move_until = simtime + PLATFORM_MOVE_DURATION
        elif action.kind == "PICKUP":
            arm.pickup()
        elif action.kind == "RELEASE":
            arm.release()


def _on_tick(simtime: float, deltatime: float) -> None:
    global platform_move_until
    del deltatime
    assert arm is not None
    assert mobile_base is not None
    platform_is_moving = (
        platform_move_until is not None and simtime < platform_move_until
    )
    if platform_move_until is not None and not platform_is_moving:
        platform_move_until = None
    actions = controller.tick(
        arm_is_moving=arm.get_is_moving(),
        is_grabbing=arm.get_is_grabbing(),
        platform_is_moving=platform_is_moving,
    )
    _execute(actions, simtime)
    if controller.unsafe_outcome:
        _set_status_color(Colors.Red)
    elif controller.paused:
        _set_status_color(Colors.Blue)
    elif controller.arm_state == "MOVING":
        _set_status_color(Colors.Yellow)
    else:
        _set_status_color(Colors.Green)
    _publish()


def _on_begin_play() -> None:
    assert exchange is not None
    assert mobile_base is not None
    exchange.on_rpc(_handle_rpc)
    _reset_runtime()


def _on_level_reset() -> None:
    _reset_runtime()


editor.set_goals_intro_text(
    "BioLab Guardian: route samples through the mobile laboratory."
)
editor.on_begin_play(_on_begin_play)
editor.on_level_reset(_on_level_reset)
editor.on_tick(_on_tick)
editor.set_template_code(
    new_code="""from pyjop import DataExchange, SimEnv

exchange = DataExchange.find("safeexec_exchange")
print(exchange.get_data("biolab_status"))

while SimEnv.run_main():
    pass
"""
)
editor.run_editor_level()
