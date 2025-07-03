"""TextExtractor adapter – canonical infra path.

Re-exports the original implementation in ``services.text_extractor``.
"""

from __future__ import annotations

import sys as _sys
from importlib import import_module as _imp

_mod = _imp("services.text_extractor")
_sys.modules[__name__] = _mod
