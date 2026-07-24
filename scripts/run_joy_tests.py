"""Run JOY unit tests with the bundled Python's isolated path configuration."""

from __future__ import annotations

import os
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(
        os.path.join(REPO_ROOT, "tests"),
        pattern="test_joy_*.py",
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
