"""Report domain package.

Convenience re-exports so callers can simply `from services.report import X`.
"""

from .direct_query_service import DirectQueryService
from .report_generator import ReportGenerator

__all__: list[str] = [
    "DirectQueryService",
    "ReportGenerator",
]
