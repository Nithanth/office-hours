"""Exa client adapter – canonical infra path.

Thin shim that re-exports ``services.exa_client`` during migration.
"""

from __future__ import annotations

import sys as _sys
from importlib import import_module as _imp

_mod = _imp("services.exa_client")
_sys.modules[__name__] = _mod
