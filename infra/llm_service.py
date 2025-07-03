"""LLM adapter – new canonical import path.

We are migrating I/O-heavy adapters into ``lux_scholar.infra`` to clearly
separate infrastructure from domain logic.  During the transition this
module simply re-exports the original implementation located at
``services.llm_client`` so that both old and new import paths resolve to
the same objects.
"""

from __future__ import annotations

import sys as _sys
from importlib import import_module as _imp

_mod = _imp("services.llm_client")
_sys.modules[__name__] = _mod
