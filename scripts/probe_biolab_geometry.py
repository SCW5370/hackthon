"""Read the live BioLab entity geometry without changing the scene."""

from __future__ import annotations

import inspect
import json
import os
import sys
import time
import traceback
from typing import Any, Callable


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pyjop import DataExchange, LevelEditor, MovablePlatform, RobotArm, SimEnv


RESULT_PATH = os.path.join(REPO_ROOT, ".run", "geometry.result.json")
ENTITY_NAMES = (
    "safeexec_mobile_base",
    "safeexec_arm",
    "sample-A",
    "sample-B",
    "cold-storage",
    "analyzer-01",
    "quarantine-zone",
    "waste-bin",
)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if hasattr(value, "__iter__"):
        try:
            return [_jsonable(item) for item in value]
        except TypeError:
            pass
    return repr(value)


def _call(label: str, call: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"ok": True, "value": _jsonable(call())}
    except Exception as exc:  # diagnostic probe deliberately records all failures
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "label": label,
        }


def _methods(value: Any, fragments: tuple[str, ...]) -> list[str]:
    return sorted(
        name
        for name in dir(value)
        if any(fragment in name.lower() for fragment in fragments)
    )


def main() -> dict[str, Any]:
    SimEnv.connect("127.0.0.1", 18189)
    editor = LevelEditor.first()
    arm = RobotArm.find("safeexec_arm")
    mobile_base = MovablePlatform.find("safeexec_mobile_base")
    exchange = DataExchange.find("safeexec_exchange")

    result: dict[str, Any] = {
        "ok": True,
        "captured_at": time.time(),
        "status": _call(
            "exchange.get_data(biolab_status)",
            lambda: exchange.get_data("biolab_status"),
        ),
        "inventory": _call(
            "exchange.get_data(biolab_inventory)",
            lambda: exchange.get_data("biolab_inventory"),
        ),
        "method_inventory": {
            "editor": _methods(
                editor, ("location", "bound", "transform", "entity", "attach")
            ),
            "arm": _methods(arm, ("location", "bound", "moving", "grab", "carry")),
            "mobile_base": _methods(
                mobile_base, ("location", "bound", "moving", "attach", "entity")
            ),
            "signatures": {
                "editor.set_location": _call(
                    "inspect.signature(editor.set_location)",
                    lambda: str(inspect.signature(editor.set_location)),
                ),
                "editor.get_bounds": _call(
                    "inspect.signature(editor.get_bounds)",
                    lambda: str(inspect.signature(editor.get_bounds)),
                ),
                "mobile_base.attach_entities": _call(
                    "inspect.signature(mobile_base.attach_entities)",
                    lambda: str(inspect.signature(mobile_base.attach_entities)),
                ),
            },
        },
        "entities": {},
        "devices": {
            "mobile_base.current_location": _call(
                "mobile_base.get_current_location",
                mobile_base.get_current_location,
            ),
            "mobile_base.is_moving": _call(
                "mobile_base.get_is_moving",
                mobile_base.get_is_moving,
            ),
            "arm.is_moving": _call("arm.get_is_moving", arm.get_is_moving),
            "arm.is_grabbing": _call("arm.get_is_grabbing", arm.get_is_grabbing),
        },
    }

    for name in ENTITY_NAMES:
        entity_result: dict[str, Any] = {}
        for method_name in (
            "get_location",
            "get_bounds",
            "get_entity_location",
            "get_entity_bounds",
            "get_transform",
            "get_entity_transform",
        ):
            method = getattr(editor, method_name, None)
            if callable(method):
                entity_result[method_name] = _call(
                    f"editor.{method_name}({name})",
                    lambda method=method, name=name: method(name),
                )
        result["entities"][name] = entity_result

    return result


if __name__ == "__main__":
    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    try:
        payload = main()
    except Exception as exc:
        payload = {
            "ok": False,
            "captured_at": time.time(),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    with open(RESULT_PATH, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    # pyjop owns a background connection thread; this diagnostic is one-shot.
    os._exit(0)
