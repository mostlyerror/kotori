"""Tests for the strategy-view DB helpers (open/closed ICs + aggregate stats)."""
import pytest


async def _insert_ic(conn, **fields):
    defaults = {
        "symbol": "SPY", "entry_date": "2026-05-22", "expiry": "2026-05-29",
        "short_call": 760.0, "long_call": 765.0, "short_put": 735.0, "long_put": 730.0,
        "spread_width": 5.0, "entry_credit": 1.00, "contracts": 1, "max_loss": 400.0,
        "regime_at_entry": "normal",
        "current_debit": None, "pct_max_profit": None,
        "exit_reason": None, "exit_debit": None, "realized_pnl": None,
    }
    defaults.update(fields)
    cols = list(defaults.keys())
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    await conn.execute(
        f"INSERT INTO ic_positions ({','.join(cols)}) VALUES ({placeholders})",
        *[defaults[c] for c in cols],
    )


async def _get_strategy_stats(conn) -> dict:
    rows = await conn.fetch(
        """SELECT
              SUM(CASE WHEN exit_reason IS NULL THEN 1 ELSE 0 END) AS open_count,
              SUM(CASE WHEN exit_reason IS NOT NULL THEN 1 ELSE 0 END) AS closed_count,
              SUM(CASE WHEN exit_reason IS NOT NULL AND realized_pnl IS NOT NULL
                       THEN realized_pnl ELSE 0 END) AS total_realized_pnl,
              SUM(CASE WHEN exit_reason IS NOT NULL AND realized_pnl IS NOT NULL
                       THEN 1 ELSE 0 END) AS pnl_known_count,
              SUM(CASE WHEN exit_reason IS NOT NULL AND realized_pnl > 0
                       THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN exit_reason IS NULL AND current_debit IS NOT NULL
                       THEN (entry_credit - current_debit) * 100 * contracts
                       ELSE 0 END) AS unrealized_pnl
           FROM ic_positions"""
    )
    r = rows[0] if rows else {}
    open_count = r.get("open_count") or 0
    closed_count = r.get("closed_count") or 0
    pnl_known = r.get("pnl_known_count") or 0
    wins = r.get("wins") or 0
    return {
        "open_count": open_count,
        "closed_count": closed_count,
        "total_realized_pnl": float(r.get("total_realized_pnl") or 0),
        "unrealized_pnl": float(r.get("unrealized_pnl") or 0),
        "win_rate": (wins / pnl_known) if pnl_known else None,
        "pnl_known_count": pnl_known,
    }


async def _get_open_ics(conn) -> list[dict]:
    rows = await conn.fetch(
        """SELECT id, symbol, entry_date, expiry, short_call, long_call,
                  short_put, long_put, entry_credit, current_debit,
                  pct_max_profit, contracts, max_loss
           FROM ic_positions WHERE exit_reason IS NULL ORDER BY expiry, symbol"""
    )
    return [dict(r) for r in rows]


async def _get_closed_ics(conn) -> list[dict]:
    rows = await conn.fetch(
        """SELECT id, symbol, entry_date, expiry, exit_reason, exit_debit,
                  entry_credit, realized_pnl, contracts, max_loss
           FROM ic_positions WHERE exit_reason IS NOT NULL ORDER BY expiry DESC, symbol"""
    )
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_get_strategy_stats_empty_db(conn):
    stats = await _get_strategy_stats(conn)
    assert stats["open_count"] == 0
    assert stats["closed_count"] == 0
    assert stats["total_realized_pnl"] == 0.0
    assert stats["unrealized_pnl"] == 0.0
    assert stats["win_rate"] is None


@pytest.mark.asyncio
async def test_get_strategy_stats_mixed_open_and_closed(conn):
    await _insert_ic(conn, symbol="SPY", entry_credit=1.00, current_debit=0.80, contracts=1)
    await _insert_ic(conn, symbol="QQQ", entry_credit=1.20, current_debit=None, contracts=2)
    await _insert_ic(
        conn, symbol="META", exit_reason="profit_target",
        entry_credit=1.50, exit_debit=1.00, realized_pnl=50.0,
    )
    await _insert_ic(
        conn, symbol="AAPL", exit_reason="force_close",
        entry_credit=1.30, exit_debit=0.50, realized_pnl=80.0,
    )
    await _insert_ic(
        conn, symbol="TSLA", exit_reason="stop_loss",
        entry_credit=1.00, exit_debit=3.00, realized_pnl=-100.0,
    )
    await _insert_ic(
        conn, symbol="NVDA", exit_reason="force_close",
        entry_credit=1.10, exit_debit=None, realized_pnl=None,
    )

    stats = await _get_strategy_stats(conn)
    assert stats["open_count"] == 2
    assert stats["closed_count"] == 4
    assert stats["total_realized_pnl"] == pytest.approx(30.0)
    assert stats["unrealized_pnl"] == pytest.approx(20.0)
    assert stats["pnl_known_count"] == 3
    assert stats["win_rate"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_get_open_and_closed_partition(conn):
    await _insert_ic(conn, symbol="SPY")
    await _insert_ic(conn, symbol="QQQ", exit_reason="profit_target", realized_pnl=50.0)

    open_ics = await _get_open_ics(conn)
    closed_ics = await _get_closed_ics(conn)
    assert [r["symbol"] for r in open_ics] == ["SPY"]
    assert [r["symbol"] for r in closed_ics] == ["QQQ"]
