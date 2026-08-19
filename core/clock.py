"""
All time goes through here — tz-aware UTC, always.

Naive datetimes were the original sin of v1: `datetime.utcnow()` returns
a naive object, SQLite stores whatever string you give it, and the
re-probe / digest-dedup logic silently compared naive against aware.
Every caller uses these four helpers instead.
"""
from datetime import date, datetime, timezone


def now() -> datetime:
    """Current time, timezone-aware, UTC."""
    return datetime.now(timezone.utc)


def iso() -> str:
    """Current time as a sortable ISO-8601 string (UTC, seconds)."""
    return now().isoformat(timespec="seconds")


def today() -> str:
    """Today's date (UTC) as YYYY-MM-DD — matches substr(seen_at,1,10)."""
    return now().date().isoformat()


def today_date() -> date:
    """Today's date (UTC) as a date object, for date arithmetic."""
    return now().date()
