"""Application clock helpers.

Datetimes are stored as UTC-naive values for compatibility with the existing
SQLite schema. They are generated from timezone-aware UTC internally so the
code never depends on the host machine's local timezone. Calendar-day business
rules (request deadlines and donation dates) use the configured business
Timezone, which defaults to Asia/Dhaka.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime for legacy DB columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def business_timezone() -> ZoneInfo:
    """Resolve the configured business timezone or fail with a clear error."""
    try:
        return ZoneInfo(settings.business_timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"Invalid BUSINESS_TIMEZONE: {settings.business_timezone!r}"
        ) from exc


def business_today() -> date:
    """Return today's calendar date in the configured business timezone."""
    return datetime.now(business_timezone()).date()
