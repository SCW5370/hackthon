"""Interactive-session smoke runner that persists a machine-readable result."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import traceback


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from joy.smoke_test import run  # noqa: E402


result_path = REPO_ROOT / ".run" / "smoke.result.json"
result_path.parent.mkdir(exist_ok=True)

started_at = time.time()
try:
    run("127.0.0.1", 18189)
except BaseException as exc:
    result = {
        "ok": False,
        "started_at": started_at,
        "finished_at": time.time(),
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    raise
else:
    result_path.write_text(
        json.dumps(
            {
                "ok": True,
                "started_at": started_at,
                "finished_at": time.time(),
                "message": "BioLab_Guardian live smoke test: PASS",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
