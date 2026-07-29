"""Time helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return current UTC-aware datetime.

    Returns:
        datetime: Current datetime with UTC timezone.
    """
    return datetime.now(timezone.utc)
