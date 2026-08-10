from datetime import date


def julian_trace_number(delivery_date: date) -> str:
    """YY + zero-padded day-of-year (e.g. 2026-09-08 → 26251)."""
    return f'{delivery_date.year % 100:02d}{delivery_date.timetuple().tm_yday:03d}'
