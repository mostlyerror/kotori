"""NYSE market calendar: holiday checks and earnings date lookups."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import lru_cache

import exchange_calendars as xcals

log = logging.getLogger(__name__)

_CALENDAR_NAME = "XNYS"


@lru_cache(maxsize=1)
def _nyse() -> xcals.ExchangeCalendar:
    return xcals.get_calendar(_CALENDAR_NAME)


def is_market_open(d: date | None = None) -> bool:
    d = d or date.today()
    return _nyse().is_session(d)


def next_trading_day(d: date | None = None) -> date:
    d = d or date.today()
    ts = _nyse().date_to_session(d, direction="next")
    return ts.date()


def previous_trading_day(d: date | None = None) -> date:
    d = d or date.today()
    ts = _nyse().date_to_session(d, direction="previous")
    return ts.date()


def is_early_close(d: date | None = None) -> bool:
    """True if the session is a half-day (e.g. day after Thanksgiving)."""
    d = d or date.today()
    cal = _nyse()
    if not cal.is_session(d):
        return False
    ts = cal.date_to_session(d, direction="none")
    close = cal.session_close(ts)
    # Normal NYSE close is 4pm ET = 20:00 UTC (EDT) or 21:00 UTC (EST).
    # Early close is 1pm ET = 17:00 or 18:00 UTC. Any close before 19:00 UTC
    # is definitively an early close regardless of DST.
    return close.hour < 19


def trading_days_between(start: date, end: date) -> list[date]:
    cal = _nyse()
    sessions = cal.sessions_in_range(start, end)
    return [s.date() for s in sessions]
