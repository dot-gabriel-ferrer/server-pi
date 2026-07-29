"""Time helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return current UTC-aware datetime.

    Returns:
        datetime: Current datetime with UTC timezone.
    """
    return datetime.now(UTC)
