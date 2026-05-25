"""Tests for the earnings module."""
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

from kotorid.earnings import (
    EarningsEvent,
    fetch_earnings_from_yahoo,
    is_etf,
)


def test_etf_detection():
    assert is_etf("SPY")
    assert is_etf("QQQ")
    assert is_etf("IWM")
    assert not is_etf("AAPL")
    assert not is_etf("NVDA")
    assert not is_etf("TSLA")


def test_fetch_earnings_skips_etfs():
    assert fetch_earnings_from_yahoo("SPY") == []
    assert fetch_earnings_from_yahoo("QQQ") == []


def test_fetch_earnings_returns_dicts():
    """Integration-ish test that hits Yahoo. Skip in CI."""
    rows = fetch_earnings_from_yahoo("AAPL")
    assert isinstance(rows, list)
    if rows:
        r = rows[0]
        assert "earnings_date" in r
        assert "eps_estimate" in r
        assert "is_confirmed" in r
        assert isinstance(r["earnings_date"], date)


@pytest.mark.asyncio
async def test_refresh_and_query(tmp_path):
    """Test DB round-trip with a real asyncpg connection if available."""
    pytest.importorskip("asyncpg")
    import asyncpg
    import os

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS earnings_calendar (
                symbol TEXT NOT NULL,
                earnings_date DATE NOT NULL,
                eps_estimate DOUBLE PRECISION,
                reported_eps DOUBLE PRECISION,
                surprise_pct DOUBLE PRECISION,
                is_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, earnings_date)
            )
        """)

        from kotorid.earnings import refresh_earnings, get_upcoming_earnings
        count = await refresh_earnings(conn, ["AAPL"])
        assert count > 0

        events = await get_upcoming_earnings(conn, ["AAPL"])
        assert isinstance(events, list)
        for e in events:
            assert isinstance(e, EarningsEvent)
            assert e.symbol == "AAPL"
            assert e.days_until >= 0
    finally:
        await conn.close()
