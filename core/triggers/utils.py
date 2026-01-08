"""Trigger utilities for timezone, datetime, and recurrence handling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from dateutil import parser as date_parser
from dateutil.rrule import rrulestr
from zoneinfo import ZoneInfo

import logging

logger = logging.getLogger(__name__)


UTC = timezone.utc
DEFAULT_STATUS = "active"
VALID_STATUSES = {"active", "paused", "completed"}


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def to_storage_timestamp(moment: datetime) -> str:
    """Normalize timestamps before writing to database."""
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_timezone(timezone_name: Optional[str]) -> ZoneInfo:
    """Return a ZoneInfo instance, defaulting to UTC on errors."""
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except Exception:
            logger.warning(f"Unknown timezone provided: {timezone_name}, defaulting to UTC")
    return ZoneInfo("UTC")


def normalize_status(status: Optional[str]) -> str:
    """Clamp trigger status to the known set."""
    if not status:
        return DEFAULT_STATUS
    normalized = status.lower()
    if normalized not in VALID_STATUSES:
        logger.warning(f"Invalid status supplied: {status}, defaulting to active")
        return DEFAULT_STATUS
    return normalized


def parse_iso(timestamp: str) -> datetime:
    """Parse an ISO timestamp, defaulting to UTC when timezone is absent."""
    dt = date_parser.isoparse(timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def parse_datetime(timestamp: str, tz: ZoneInfo) -> datetime:
    """Parse a timestamp string into the provided timezone."""
    dt = date_parser.isoparse(timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt


def parse_natural_datetime(text: str, tz: ZoneInfo) -> Optional[datetime]:
    """Parse natural language datetime using dateparser."""
    try:
        import dateparser
        settings = {
            'TIMEZONE': str(tz),
            'RETURN_AS_TIMEZONE_AWARE': True,
            'PREFER_DATES_FROM': 'future',
        }
        parsed = dateparser.parse(text, settings=settings)
        return parsed
    except ImportError:
        logger.warning("dateparser not installed, falling back to ISO parsing")
        return None
    except Exception as e:
        logger.warning(f"Failed to parse natural datetime '{text}': {e}")
        return None


def coerce_start_datetime(
    start_time: Optional[str], tz: ZoneInfo, fallback: datetime
) -> datetime:
    """Return the desired start datetime in the agent's timezone."""
    if not start_time:
        return fallback.astimezone(tz)
    
    # Try natural language first
    natural_dt = parse_natural_datetime(start_time, tz)
    if natural_dt:
        return natural_dt.astimezone(tz)
    
    # Fall back to ISO parsing
    try:
        return parse_datetime(start_time, tz)
    except Exception:
        logger.warning(f"Could not parse start_time '{start_time}', using fallback")
        return fallback.astimezone(tz)


def build_recurrence(
    recurrence_rule: Optional[str],
    start_dt_local: datetime,
    tz: ZoneInfo,
) -> Optional[str]:
    """Embed DTSTART metadata into the supplied RRULE text."""
    if not recurrence_rule:
        return None
    
    # Normalize common natural language patterns to RRULE
    rule_upper = recurrence_rule.upper().strip()
    if not rule_upper.startswith("RRULE:") and not rule_upper.startswith("FREQ="):
        # Try to convert natural language to RRULE
        natural_rules = {
            "DAILY": "FREQ=DAILY",
            "WEEKLY": "FREQ=WEEKLY",
            "MONTHLY": "FREQ=MONTHLY",
            "YEARLY": "FREQ=YEARLY",
            "EVERY DAY": "FREQ=DAILY",
            "EVERY WEEK": "FREQ=WEEKLY",
            "EVERY MONTH": "FREQ=MONTHLY",
            "EVERY WEEKDAY": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
            "WEEKDAYS": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        }
        rule_upper = natural_rules.get(rule_upper, rule_upper)
    
    if start_dt_local.tzinfo is None:
        localized_start = start_dt_local.replace(tzinfo=tz)
    else:
        localized_start = start_dt_local.astimezone(tz)
    
    if localized_start.utcoffset() == timedelta(0):
        dt_line = f"DTSTART:{localized_start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    else:
        tz_name = getattr(tz, "key", "UTC")
        dt_line = f"DTSTART;TZID={tz_name}:{localized_start.strftime('%Y%m%dT%H%M%S')}"
    
    lines = [segment.strip() for segment in recurrence_rule.strip().splitlines() if segment.strip()]
    filtered = [segment for segment in lines if not segment.upper().startswith("DTSTART")]
    if not filtered:
        raise ValueError("recurrence_rule must contain an RRULE definition")
    
    if not filtered[0].upper().startswith("RRULE"):
        filtered[0] = f"RRULE:{filtered[0]}"
    
    return "\n".join([dt_line, *filtered])


def load_rrule(recurrence_text: str):
    """Parse a stored recurrence string into a dateutil rule instance."""
    return rrulestr(recurrence_text)


__all__ = [
    "UTC",
    "DEFAULT_STATUS",
    "VALID_STATUSES",
    "build_recurrence",
    "coerce_start_datetime",
    "load_rrule",
    "normalize_status",
    "parse_datetime",
    "parse_iso",
    "parse_natural_datetime",
    "resolve_timezone",
    "to_storage_timestamp",
    "utc_now",
]
