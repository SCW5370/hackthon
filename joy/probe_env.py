"""Read-only JOY/pyjop capability probe.

This module deliberately performs no spawn, movement, save, or editor mutation.
It is safe to run while the user has JOY's Level Editor open.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import inspect
import json
import platform
import socket
import sys
from typing import Any


REQUIRED_TYPES = (
    "LevelEditor",
    "RobotArm",
    "DataExchange",
    "RangeFinder",
    "SimEnvManager",
)

METHODS_TO_PROBE = {
    "LevelEditor": (
        "clear_all",
        "select_map",
        "spawn_entity",
        "spawn_static_mesh",
        "set_location",
        "set_rfid_tag",
        "on_begin_play",
        "on_level_reset",
        "on_tick",
        "run_editor_level",
    ),
    "RobotArm": (
        "set_grabber_location",
        "get_is_grabbing",
        "get_is_moving",
        "pickup",
        "release",
    ),
    "DataExchange": (
        "set_data",
        "get_data",
        "get_keys",
        "rpc",
        "on_rpc",
        "return_rpc",
    ),
    "RangeFinder": (
        "get_entity_name",
        "get_rfid_tag",
        "editor_set_can_read_rfid_tags",
    ),
    "SimEnvManager": (
        "reset",
        "get_sim_time",
        "get_time_dilation",
        "set_time_dilation",
    ),
}

ENUM_MEMBERS_TO_PROBE = {
    "SpawnableMaps": ("MinimalisticIndoor",),
    "SpawnableEntities": ("DataExchange", "RangeFinder", "RobotArm", "LEDStrip"),
    "SpawnableMeshes": ("Cube", "Cylinder",),
    "SpawnableMaterials": ("SimpleColor", "SimpleEmissive"),
    "Colors": ("Blue", "Yellow", "Green", "Red"),
}


def _signature(value: Any) -> str:
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return "<unavailable>"


def _public_methods(value: Any) -> list[str]:
    return sorted(
        name
        for name in dir(value)
        if not name.startswith("_") and callable(getattr(value, name, None))
    )


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def run_probe(host: str, port: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "read_only": True,
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "endpoint": {
            "host": host,
            "port": port,
            "tcp_open_before_connect": _port_is_open(host, port),
        },
    }

    try:
        import pyjop
    except Exception as exc:  # pragma: no cover - environment dependent
        result["pyjop"] = {
            "import_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return result

    try:
        version = importlib.metadata.version("pyjop")
    except importlib.metadata.PackageNotFoundError:
        version = getattr(pyjop, "__version__", "<unknown>")

    type_report: dict[str, Any] = {}
    for type_name in REQUIRED_TYPES:
        value = getattr(pyjop, type_name, None)
        type_report[type_name] = {
            "available": value is not None,
            "signature": _signature(value) if value is not None else None,
            "public_methods": _public_methods(value) if value is not None else [],
            "selected_method_signatures": {
                method_name: _signature(getattr(value, method_name, None))
                for method_name in METHODS_TO_PROBE[type_name]
            }
            if value is not None
            else {},
        }

    enum_report: dict[str, Any] = {}
    for enum_name, members in ENUM_MEMBERS_TO_PROBE.items():
        enum_type = getattr(pyjop, enum_name, None)
        enum_report[enum_name] = {
            "available": enum_type is not None,
            "members": {
                member: hasattr(enum_type, member) if enum_type is not None else False
                for member in members
            },
        }

    sim_env = getattr(pyjop, "SimEnv", None)
    result["pyjop"] = {
        "import_ok": True,
        "version": version,
        "module_path": getattr(pyjop, "__file__", None),
        "SimEnv": {
            "available": sim_env is not None,
            "connect_signature": (
                _signature(getattr(sim_env, "connect", None))
                if sim_env is not None
                else None
            ),
            "public_methods": _public_methods(sim_env) if sim_env is not None else [],
        },
        "types": type_report,
        "enums": enum_report,
    }

    if sim_env is None:
        result["connection"] = {
            "ok": False,
            "error": "pyjop.SimEnv is unavailable",
        }
        return result

    try:
        sim_env.connect(host, port)
        result["connection"] = {
            "ok": True,
            "external_python_client": True,
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        result["connection"] = {
            "ok": False,
            "external_python_client": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return result

    editor_type = getattr(pyjop, "LevelEditor", None)
    if editor_type is not None and callable(getattr(editor_type, "first", None)):
        try:
            editor = editor_type.first()
            result["level_editor"] = {
                "reachable": editor is not None,
                "instance_type": type(editor).__name__ if editor is not None else None,
            }
        except Exception as exc:  # pragma: no cover - environment dependent
            result["level_editor"] = {
                "reachable": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18189)
    args = parser.parse_args()

    result = run_probe(args.host, args.port)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    exit_code = 0 if result.get("connection", {}).get("ok") else 1
    if result.get("connection", {}).get("ok"):
        import pyjop

        pyjop.SimEnv.disconnect()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
