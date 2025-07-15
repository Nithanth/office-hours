"""Lux Scholar Python package root."""

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
