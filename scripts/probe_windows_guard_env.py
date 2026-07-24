"""Report whether the JOY embedded Python can host the SafeExec Guard."""

from __future__ import annotations

import importlib
import json
import sys


modules = {}
for module_name in ("nacl", "yaml", "pyjop"):
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        modules[module_name] = {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    else:
        modules[module_name] = {
            "available": True,
            "version": getattr(module, "__version__", None),
        }

print(
    json.dumps(
        {
            "python": sys.version,
            "executable": sys.executable,
            "modules": modules,
        },
        indent=2,
    ),
    flush=True,
)
