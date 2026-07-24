"""Reset the live JOY BioLab scene and persist a one-shot result."""

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

from joy.driver import JoyDriver


result_path = REPO_ROOT / ".run" / "reset.result.json"
result_path.parent.mkdir(exist_ok=True)
try:
    result = {
        "ok": True,
        "reset_at": time.time(),
        "status": JoyDriver().connect().reset(),
    }
except BaseException as exc:
    result = {
        "ok": False,
        "reset_at": time.time(),
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }

with result_path.open("w", encoding="utf-8") as output:
    json.dump(result, output, ensure_ascii=False, indent=2)
    output.flush()
    os.fsync(output.fileno())
os._exit(0)
