"""Office Hours Python package root."""

import importlib as _imp

# ---------------------------------------------------------------------------
# Temporary shims – keep legacy imports working during the migration.
# These will be deleted once all modules are relocated under the new
# package layout and call-sites have been updated.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Runtime-only aliases for top-level packages
# ---------------------------------------------------------------------------

# Try to import a top-level `config` module (prev structure). If missing, fall back
# to `src.config` and expose it under the expected top-level name so
# `import config` works regardless of PYTHONPATH when running from the project
# root (e.g. `uvicorn src.main:app`).

config_module_name = "config"

try:
    config = _imp.import_module(config_module_name)
except ModuleNotFoundError:  # Legacy path not found — create runtime alias
    try:
        _fallback = _imp.import_module("src.config")
        import sys as _sys

        # Register under both names so subsequent imports resolve.
        _sys.modules[config_module_name] = _fallback
        globals()[config_module_name] = _fallback
    except ModuleNotFoundError:  # pragma: no cover – catastrophic mis-install
        # Leave unaliased; downstream import will raise with clearer message.
        pass
