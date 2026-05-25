"""Tests for the market_calendar module."""
from datetime import date

from kotorid.market_calendar import (
    is_early_close,
    is_market_open,
    next_trading_day,
    previous_trading_day,
    trading_days_between,
)


def test_memorial_day_2026_is_closed():
    assert not is_market_open(date(2026, 5, 25))


def test_regular_friday_is_open():
    assert is_market_open(date(2026, 5, 22))


def test_weekend_is_closed():
    assert not is_market_open(date(2026, 5, 24))  # Saturday


def test_next_trading_day_from_holiday():
    nxt = next_trading_day(date(2026, 5, 25))
    assert nxt == date(2026, 5, 26)


def test_previous_trading_day_from_holiday():
    prev = previous_trading_day(date(2026, 5, 25))
    assert prev == date(2026, 5, 22)


def test_trading_days_between():
    days = trading_days_between(date(2026, 5, 18), date(2026, 5, 26))
    assert date(2026, 5, 25) not in days  # Memorial Day excluded
    assert date(2026, 5, 23) not in days  # Saturday
    assert date(2026, 5, 22) in days
    assert date(2026, 5, 26) in days


def test_christmas_2025_is_closed():
    assert not is_market_open(date(2025, 12, 25))


def test_day_before_thanksgiving_is_early_close():
    assert is_early_close(date(2025, 11, 28))
