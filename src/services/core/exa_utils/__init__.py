"""Utility sub-package for stateless helpers shared across the *infra* layer.

Phase-1 of the ExaClient refactor moves pure helper functions here.  Modules
are intentionally kept very small and dependency-free so they can be reused by
other services without dragging in the full exa_client implementation.
"""

from importlib import import_module as _imp
from types import ModuleType as _Mod

# Lazily import sub-modules on attribute access so that optional helpers don’t
# impose unnecessary import costs.

_modules: dict[str, str] = {
    "url_utils": ".url_utils",
    "author_utils": ".author_utils",
    "merge_utils": ".merge_utils",
    "text_utils": ".text_utils",
}


def __getattr__(name: str) -> _Mod:  # pragma: no cover –
    if name in _modules:
        module = _imp(_modules[name], package=__name__)
        globals()[name] = module  # cache for next access
        return module
    raise AttributeError(name)
