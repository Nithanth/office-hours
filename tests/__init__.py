# This file makes the 'tests' directory a Python package.

# Automatically add the project `src` directory to `sys.path` so tests can
# import top-level packages like `infra.*` without requiring the user to set
# PYTHONPATH or install the package in editable mode.
#
# This is a *testing convenience* only and should not leak into production
# code paths.

from __future__ import annotations

import sys
from pathlib import Path

# Compute the absolute path to the project root (two levels up from this file)
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"

# Prepend to sys.path to give it precedence over site-packages
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
