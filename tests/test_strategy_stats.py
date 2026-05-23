"""Tests for the strategy-view DB helpers (open/closed ICs + aggregate stats).

These helpers live in kotori_tui.db but they connect to a real SQLite
file via the module-level DB_PATH. To test them in isolation we override
KOTORI_DB to a tmp_path and reimport the config module so the helpers
read the test DB instead of ~/.kotori/kotori.db.
"""
import importlib
import os

import pytest


@pytest.fixture
def tui_db(tmp_path, monkeypatch):
    """Spin up a fresh SQLite file, point kotori_tui.db at it, init the
    schema. Returns the kotori_tui.db module with the test DB_PATH active."""
    db_file = tmp_path / "strategy.db"
    monkeypatch.setenv("KOTORI_DB", str(db_file))
    import kotorid.config
    import kotorid.db
    import kotori_tui.db
    # Reload so DB_PATH is re-read from the new env var
    importlib.reload(kotorid.config)
    importlib.reload(kotorid.db)
    importlib.reload(kotori_tui.db)

    import asyncio
    async def _init():
        async with kotorid.db.get_db(str(db_file)) as conn:
            await kotorid.db.init_db(conn)
    asyncio.run(_init())
    return kotori_tui.db


async def _insert_ic(db_path, **fields):
    """Insert an ic_positions row with sensible defaults."""
    import aiosqlite
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
    placeholders = ",".join("?" for _ in cols)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"INSERT INTO ic_positions ({','.join(cols)}) VALUES ({placeholders})",
            tuple(defaults[c] for c in cols),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_get_strategy_stats_empty_db(tui_db):
    stats = await tui_db.get_strategy_stats()
    assert stats["open_count"] == 0
    assert stats["closed_count"] == 0
    assert stats["total_realized_pnl"] == 0.0
    assert stats["unrealized_pnl"] == 0.0
    assert stats["win_rate"] is None  # no settled trades to compute over


@pytest.mark.asyncio
async def test_get_strategy_stats_mixed_open_and_closed(tui_db, tmp_path):
    """Two open ICs (one with current_debit, one without) + three closed
    (two wins, one loss, one with NULL realized_pnl)."""
    db_path = str(tmp_path / "strategy.db")
    # 2 open ICs
    await _insert_ic(db_path, symbol="SPY", entry_credit=1.00, current_debit=0.80, contracts=1)
    await _insert_ic(db_path, symbol="QQQ", entry_credit=1.20, current_debit=None, contracts=2)
    # 3 closed ICs: 2 wins (+$50, +$80), 1 loss (-$100), 1 unknown
    await _insert_ic(
        db_path, symbol="META", exit_reason="profit_target",
        entry_credit=1.50, exit_debit=1.00, realized_pnl=50.0,
    )
    await _insert_ic(
        db_path, symbol="AAPL", exit_reason="force_close",
        entry_credit=1.30, exit_debit=0.50, realized_pnl=80.0,
    )
    await _insert_ic(
        db_path, symbol="TSLA", exit_reason="stop_loss",
        entry_credit=1.00, exit_debit=3.00, realized_pnl=-100.0,
    )
    await _insert_ic(
        db_path, symbol="NVDA", exit_reason="force_close",
        entry_credit=1.10, exit_debit=None, realized_pnl=None,
    )

    stats = await tui_db.get_strategy_stats()
    assert stats["open_count"] == 2
    assert stats["closed_count"] == 4
    # total_realized_pnl skips NULL: 50 + 80 - 100 = 30
    assert stats["total_realized_pnl"] == pytest.approx(30.0)
    # unrealized: only SPY counts (QQQ has no current_debit)
    # SPY: (1.00 - 0.80) * 100 * 1 = $20
    assert stats["unrealized_pnl"] == pytest.approx(20.0)
    # win_rate: 2 wins out of 3 settled (NVDA NULL excluded)
    assert stats["pnl_known_count"] == 3
    assert stats["win_rate"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_get_open_and_closed_partition(tui_db, tmp_path):
    """get_open_ics returns only exit_reason IS NULL rows;
    get_closed_ics returns only exit_reason IS NOT NULL rows."""
    db_path = str(tmp_path / "strategy.db")
    await _insert_ic(db_path, symbol="SPY")
    await _insert_ic(db_path, symbol="QQQ", exit_reason="profit_target", realized_pnl=50.0)

    open_ics = await tui_db.get_open_ics()
    closed_ics = await tui_db.get_closed_ics()
    assert [r["symbol"] for r in open_ics] == ["SPY"]
    assert [r["symbol"] for r in closed_ics] == ["QQQ"]
