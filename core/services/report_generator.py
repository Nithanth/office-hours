"""ReportGenerator – migrated to core.services

During the transition, we simply re-export the implementation from the
legacy `services.report_generator` module.  Once all call-sites are
updated, the real source will be moved here and the old module will
become a thin shim.
"""

from __future__ import annotations

import sys as _sys
from importlib import import_module as _imp

_mod = _imp("services.report_generator")
_sys.modules[__name__] = _mod
