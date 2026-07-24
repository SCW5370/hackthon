"""Bootstrap SafeExec Guard inside JOY's isolated embedded Python."""

from __future__ import annotations

import os
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from guard.guard_http import main


if __name__ == "__main__":
    main()
