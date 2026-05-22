"""Tests for refresh_ic_state — the IC monitoring producer."""
import httpx
import pytest

from kotorid.db import get_db, init_db
from kotorid.ic_sync import refresh_ic_state
from kotorid.tradier_client import build_client


def _make_client(handler):
    transport = httpx.MockTransport(handler)
    return build_client(
        base_url="https://sandbox.tradier.com/v1",
        api_key="testkey",
        transport=transport,
    )


@pytest.mark.asyncio
async def test_refresh_updates_debit_and_pct_max_profit(tmp_path):
    """Open IC + full set of leg quotes => current_debit and pct_max_profit set."""
    def handler(request):
        if "/markets/quotes" in str(request.url):
            return httpx.Response(200, json={
                "quotes": {"quote": [
                    {"symbol": "SPY260529C00760000", "bid": 0.70, "ask": 0.72},
                    {"symbol": "SPY260529C00765000", "bid": 0.24, "ask": 0.26},
                    {"symbol": "SPY260529P00735000", "bid": 1.50, "ask": 1.52},
                    {"symbol": "SPY260529P00730000", "bid": 0.98, "ask": 1.00},
                ]}
            })
        raise AssertionError(f"unexpected request: {request.url}")

    db_path = str(tmp_path / "ic.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
                spread_width, entry_credit, contracts, max_loss, regime_at_entry)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SPY", "2026-05-22", "2026-05-29", 760.0, 765.0, 735.0, 730.0,
             5.0, 1.00, 1, 400.0, "normal"),
        )
        await db.commit()
        async with _make_client(handler) as c:
            n = await refresh_ic_state(db, c)
        assert n == 1

        row = await (await db.execute(
            "SELECT current_debit, pct_max_profit FROM ic_positions"
        )).fetchone()
    # mid prices: SC=0.71, LC=0.25, SP=1.51, LP=0.99
    # debit = (0.71 + 1.51) - (0.25 + 0.99) = 2.22 - 1.24 = 0.98
    assert row["current_debit"] == pytest.approx(0.98)
    # pct_max_profit = (entry_credit - debit) / entry_credit = (1.00 - 0.98) / 1.00 = 0.02
    assert row["pct_max_profit"] == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_refresh_skips_ic_when_a_leg_quote_is_missing(tmp_path):
    """Missing leg quote => skip this IC, no error, no partial update."""
    def handler(request):
        if "/markets/quotes" in str(request.url):
            # Missing the long_put quote entirely.
            return httpx.Response(200, json={
                "quotes": {"quote": [
                    {"symbol": "SPY260529C00760000", "bid": 0.70, "ask": 0.72},
                    {"symbol": "SPY260529C00765000", "bid": 0.24, "ask": 0.26},
                    {"symbol": "SPY260529P00735000", "bid": 1.50, "ask": 1.52},
                ]}
            })
        raise AssertionError(f"unexpected request: {request.url}")

    db_path = str(tmp_path / "ic_missing.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
                spread_width, entry_credit, contracts, max_loss, regime_at_entry,
                current_debit, pct_max_profit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SPY", "2026-05-22", "2026-05-29", 760.0, 765.0, 735.0, 730.0,
             5.0, 1.00, 1, 400.0, "normal", None, None),
        )
        await db.commit()
        async with _make_client(handler) as c:
            n = await refresh_ic_state(db, c)
        assert n == 0

        row = await (await db.execute(
            "SELECT current_debit, pct_max_profit FROM ic_positions"
        )).fetchone()
    # Should not have been touched.
    assert row["current_debit"] is None
    assert row["pct_max_profit"] is None


@pytest.mark.asyncio
async def test_refresh_skips_when_market_closed_returns_zero_quotes(tmp_path):
    """Closed-market days: Tradier returns bid=0 ask=0 on legs.

    The IC must be skipped (not silently updated to debit=0, which would
    look like a 100% profit-capture and fire a false profit-target alert).
    """
    def handler(request):
        if "/markets/quotes" in str(request.url):
            return httpx.Response(200, json={
                "quotes": {"quote": [
                    {"symbol": "SPY260529C00760000", "bid": 0, "ask": 0},
                    {"symbol": "SPY260529C00765000", "bid": 0, "ask": 0},
                    {"symbol": "SPY260529P00735000", "bid": 0, "ask": 0},
                    {"symbol": "SPY260529P00730000", "bid": 0, "ask": 0},
                ]}
            })
        raise AssertionError(f"unexpected request: {request.url}")

    db_path = str(tmp_path / "ic_closed_market.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
                spread_width, entry_credit, contracts, max_loss, regime_at_entry,
                current_debit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SPY", "2026-05-22", "2026-05-29", 760.0, 765.0, 735.0, 730.0,
             5.0, 1.00, 1, 400.0, "normal", 1.04),
        )
        await db.commit()
        async with _make_client(handler) as c:
            n = await refresh_ic_state(db, c)
        assert n == 0

        row = await (await db.execute(
            "SELECT current_debit FROM ic_positions"
        )).fetchone()
    # Last known debit preserved — not overwritten with garbage 0.
    assert row["current_debit"] == pytest.approx(1.04)


@pytest.mark.asyncio
async def test_refresh_skips_when_quote_is_crossed(tmp_path):
    """Crossed quotes (bid > ask) are corrupt; skip rather than write nonsense."""
    def handler(request):
        if "/markets/quotes" in str(request.url):
            return httpx.Response(200, json={
                "quotes": {"quote": [
                    # Short call crossed — bid=5.00 > ask=0.50, garbage feed
                    {"symbol": "SPY260529C00760000", "bid": 5.00, "ask": 0.50},
                    {"symbol": "SPY260529C00765000", "bid": 0.24, "ask": 0.26},
                    {"symbol": "SPY260529P00735000", "bid": 1.50, "ask": 1.52},
                    {"symbol": "SPY260529P00730000", "bid": 0.98, "ask": 1.00},
                ]}
            })
        raise AssertionError(f"unexpected request: {request.url}")

    db_path = str(tmp_path / "ic_crossed.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
                spread_width, entry_credit, contracts, max_loss, regime_at_entry)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SPY", "2026-05-22", "2026-05-29", 760.0, 765.0, 735.0, 730.0,
             5.0, 1.00, 1, 400.0, "normal"),
        )
        await db.commit()
        async with _make_client(handler) as c:
            n = await refresh_ic_state(db, c)
        assert n == 0


@pytest.mark.asyncio
async def test_refresh_accepts_zero_bid_with_real_ask(tmp_path):
    """Deep-OTM penny options legitimately have bid=0 ask=0.01.

    That's not closed-market garbage — it's a real "no resting bid"
    state for a worth-half-a-penny option. Must NOT be rejected.
    """
    def handler(request):
        if "/markets/quotes" in str(request.url):
            return httpx.Response(200, json={
                "quotes": {"quote": [
                    {"symbol": "SPY260529C00760000", "bid": 0, "ask": 0.01},
                    {"symbol": "SPY260529C00765000", "bid": 0, "ask": 0.01},
                    {"symbol": "SPY260529P00735000", "bid": 0, "ask": 0.01},
                    {"symbol": "SPY260529P00730000", "bid": 0, "ask": 0.01},
                ]}
            })
        raise AssertionError(f"unexpected request: {request.url}")

    db_path = str(tmp_path / "ic_pennies.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
                spread_width, entry_credit, contracts, max_loss, regime_at_entry)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SPY", "2026-05-22", "2026-05-29", 760.0, 765.0, 735.0, 730.0,
             5.0, 1.00, 1, 400.0, "normal"),
        )
        await db.commit()
        async with _make_client(handler) as c:
            n = await refresh_ic_state(db, c)
        assert n == 1

        row = await (await db.execute(
            "SELECT current_debit FROM ic_positions"
        )).fetchone()
    # All legs at mid=0.005; shorts (2 × 0.005) − longs (2 × 0.005) = 0
    # This is a legitimate "IC at max profit" state — every leg worthless.
    assert row["current_debit"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_refresh_skips_closed_ics(tmp_path):
    """ICs with exit_reason set should not be touched."""
    def handler(request):
        raise AssertionError("should not call Tradier when no open ICs")

    db_path = str(tmp_path / "ic_closed.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
                spread_width, entry_credit, contracts, max_loss, regime_at_entry,
                exit_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SPY", "2026-05-22", "2026-05-29", 760.0, 765.0, 735.0, 730.0,
             5.0, 1.00, 1, 400.0, "normal", "profit_target"),
        )
        await db.commit()
        async with _make_client(handler) as c:
            n = await refresh_ic_state(db, c)
    assert n == 0
