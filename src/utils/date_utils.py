"""Date helpers — user-input parsing and one display format ("Nov 6, 2024")."""

import re
from datetime import UTC, date, datetime

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
            return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)
        except ValueError:
            continue

    logger.debug("unrecognized date format", value=cleaned)
    return None


_MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

_ISO_PREFIX = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")


def human_date(value: object) -> str:
    """Format a datetime/date/ISO-ish string as 'Nov 6, 2024'.

    Accepts datetime/date objects and strings starting with an ISO date
    prefix ('YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS+00:00', 'YYYY-MM-DDTHH:MM:SSZ').
    Returns '' for missing or unparseable input so callers can omit it.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        d = value.date()
        return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"
    if isinstance(value, date):
        return f"{_MONTHS[value.month - 1]} {value.day}, {value.year}"
    m = _ISO_PREFIX.match(str(value).strip())
    if not m:
        return ""
    year, month, day = (int(p) for p in m.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return ""
    return f"{_MONTHS[month - 1]} {day}, {year}"
