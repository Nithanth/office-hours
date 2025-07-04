"""Lux Scholar Python package root.

This file converts the repository root into an importable package so that
code can be referenced as ``lux_scholar.<module>`` during the progressive
re-organisation.  It intentionally re-exports a handful of top-level
modules for backwards-compatibility while we move them into their final
locations.
"""

import contextlib
import importlib as _imp

# ---------------------------------------------------------------------------
# Temporary shims – keep legacy imports working during the migration.
# These will be deleted once all modules are relocated under the new
# package layout and call-sites have been updated.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Runtime-only aliases for top-level packages
# ---------------------------------------------------------------------------

with contextlib.suppress(ModuleNotFoundError):
    config = _imp.import_module("config")
