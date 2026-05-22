"""Tests for the position-label formatter used in the briefing view."""
from datetime import date
from unittest.mock import patch

from kotori_tui.views.briefing_view import format_position_label


def test_stock_returns_raw_symbol():
    p = {"instrument_type": "stock", "symbol": "SPY"}
    assert format_position_label(p) == "SPY"


def test_option_current_year_drops_year():
    p = {
        "instrument_type": "option",
        "symbol": "SPY260529C00747000",
        "underlying": "SPY",
        "expiry": "2026-05-29",
        "strike": 747.0,
        "put_call": "C",
    }
    with patch("kotori_tui.views.briefing_view.date") as mock_date:
        mock_date.today.return_value = date(2026, 5, 22)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        assert format_position_label(p) == "SPY 5/29 747C"


def test_option_year_cross_includes_two_digit_year():
    p = {
        "instrument_type": "option",
        "symbol": "SPY270115P00500000",
        "underlying": "SPY",
        "expiry": "2027-01-15",
        "strike": 500.0,
        "put_call": "P",
    }
    with patch("kotori_tui.views.briefing_view.date") as mock_date:
        mock_date.today.return_value = date(2026, 5, 22)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        assert format_position_label(p) == "SPY 1/15/27 500P"


def test_half_strike_preserves_decimal():
    p = {
        "instrument_type": "option",
        "symbol": "SPY260529C00747500",
        "underlying": "SPY",
        "expiry": "2026-05-29",
        "strike": 747.5,
        "put_call": "C",
    }
    with patch("kotori_tui.views.briefing_view.date") as mock_date:
        mock_date.today.return_value = date(2026, 5, 22)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        assert format_position_label(p) == "SPY 5/29 747.5C"


def test_missing_option_columns_falls_back_to_symbol():
    p = {
        "instrument_type": "option",
        "symbol": "SPY260529C00747000",
        "underlying": None,
        "expiry": None,
        "strike": None,
        "put_call": None,
    }
    assert format_position_label(p) == "SPY260529C00747000"


def test_malformed_expiry_falls_back_to_symbol():
    p = {
        "instrument_type": "option",
        "symbol": "SPY260529C00747000",
        "underlying": "SPY",
        "expiry": "not-a-date",
        "strike": 747.0,
        "put_call": "C",
    }
    assert format_position_label(p) == "SPY260529C00747000"
