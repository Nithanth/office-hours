"""Report domain package.

Convenience re-exports so callers can simply `from services.report import X`.
"""

from .report_generator import ReportGenerator

# Public re-exports – keep minimal to avoid import cycles during large refactor
__all__: list[str] = [
    "ReportGenerator",
]
