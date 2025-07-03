"""Lux Scholar Python package root.

This file converts the repository root into an importable package so that
code can be referenced as ``lux_scholar.<module>`` during the progressive
re-organisation.  It intentionally re-exports a handful of top-level
modules for backwards-compatibility while we move them into their final
locations.
"""

import contextlib
import importlib as _imp
import sys as _sys

# Ensure `import app` continues to work
if "app" not in _sys.modules:
    with contextlib.suppress(ModuleNotFoundError):
        _sys.modules["app"] = _imp.import_module("main")

# ---------------------------------------------------------------------------
# Temporary shims – keep legacy imports working during the migration.
# These will be deleted once all modules are relocated under the new
# package layout and call-sites have been updated.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Runtime-only aliases for top-level packages
# ---------------------------------------------------------------------------
# External callers may still import e.g. ``lux_scholar.utils``; expose an alias
# so this works at runtime, but internal code should import the top-level
# package directly to keep mypy happy (avoids duplicate-module errors).
for _name in ("utils", "services", "api"):
    if f"lux_scholar.{_name}" not in _sys.modules:
        _sys.modules[f"lux_scholar.{_name}"] = _imp.import_module(_name)

with contextlib.suppress(ModuleNotFoundError):
    app = _imp.import_module("app")

with contextlib.suppress(ModuleNotFoundError):
    config = _imp.import_module("config")
