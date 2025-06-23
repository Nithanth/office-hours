from datetime import datetime, timezone


def parse_user_date(date_str: str | None) -> datetime | None:
    """
    Parses a date string from user input into a timezone-aware datetime object.

    Accepts 'YYYY-MM-DD' and 'MM/DD/YYYY' formats.
    Returns a datetime object representing the start of the day in UTC.
    Returns None if the input is None, empty, or invalid.
    """
    if not date_str or not date_str.strip():
        return None

    cleaned_date = date_str.strip()
    dt_obj = None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt_obj = datetime.strptime(cleaned_date, fmt)
            break
        except ValueError:
            continue

    if dt_obj:
        # Return as a timezone-aware datetime object at the start of the day UTC
        return dt_obj.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)

    return None
