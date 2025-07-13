"""Date parsing helpers for user-supplied input."""

from datetime import datetime, timezone

from loguru import logger

logger = logger.bind(component="DateUtils")


def parse_user_date(date_str: str | None) -> datetime | None:
    """Return UTC start-of-day datetime for *date_str*.

    Supports "YYYY-MM-DD" and "MM/DD/YYYY"; returns *None* when the input is
    missing or cannot be parsed.
    """
    if not date_str or not date_str.strip():
        return None

    cleaned = date_str.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        except ValueError:
            continue

    logger.debug("unrecognized date format", value=cleaned)
    return None
