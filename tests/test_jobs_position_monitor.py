from datetime import date, timedelta

import pytest
from kotorid.db import get_db, init_db
from kotorid.mock_data import seed_mock_data
from kotorid.jobs import run_position_monitor

@pytest.mark.asyncio
async def test_position_monitor_no_triggers_on_fresh_data(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        # Override to a debit safely between profit_target (0.925) and stop_loss (3.70)
        # so neither trigger fires. entry_credit=1.85, safe zone: 0.926 < debit < 3.70
        await db.execute(
            "UPDATE ic_positions SET current_debit = 1.20 WHERE symbol = 'TSLA'"
        )
        await db.commit()
        closed = await run_position_monitor(db)
    assert closed == []

@pytest.mark.asyncio
async def test_position_monitor_fires_profit_target(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        await db.execute(
            "UPDATE ic_positions SET current_debit = 0.925 WHERE symbol = 'TSLA'"
        )
        await db.commit()
        closed = await run_position_monitor(db)
    assert len(closed) == 1
    assert closed[0]["symbol"] == "TSLA"
    assert closed[0]["exit_reason"] == "profit_target"

@pytest.mark.asyncio
async def test_position_monitor_fires_stop_loss(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        await db.execute(
            "UPDATE ic_positions SET current_debit = 3.70 WHERE symbol = 'TSLA'"
        )
        await db.commit()
        closed = await run_position_monitor(db)
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "stop_loss"


# Use dead-zone debits in expiry tests so profit_target / stop_loss don't fire
# and pre-empt force_close (the actual code path under test).
# For entry_credit=$1.00, dead zone is $0.50 < debit < $2.00.


@pytest.mark.asyncio
async def test_force_close_does_not_fire_on_expiry_day(tmp_path):
    """expiry day is too early — broker legs are still open until 3pm CT.

    force_close must wait until the calendar day AFTER expiry. Old code
    used `expiry <= today` and fired prematurely.
    """
    db_path = str(tmp_path / "test.db")
    today = date.today().isoformat()
    async with get_db(db_path) as db:
        await init_db(db)
        # IC expires TODAY, debit in the dead zone (no profit/stop trigger).
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
                spread_width, entry_credit, contracts, max_loss, regime_at_entry,
                current_debit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SPY", today, today, 760.0, 765.0, 735.0, 730.0,
             5.0, 1.00, 1, 400.0, "normal", 0.75),
        )
        await db.commit()
        closed = await run_position_monitor(db)
    assert closed == []  # IC stays open on expiry day itself


@pytest.mark.asyncio
async def test_force_close_fires_day_after_expiry_with_real_pnl(tmp_path):
    """The day after expiry, force_close fires and computes realized P&L
    from the last-known current_debit instead of hardcoding 0."""
    db_path = str(tmp_path / "test.db")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    async with get_db(db_path) as db:
        await init_db(db)
        # IC expired yesterday, last debit refresh recorded $0.75 (dead zone
        # — modest gain, neither profit_target nor stop_loss had fired).
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
                spread_width, entry_credit, contracts, max_loss, regime_at_entry,
                current_debit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SPY", yesterday, yesterday, 760.0, 765.0, 735.0, 730.0,
             5.0, 1.00, 1, 400.0, "normal", 0.75),
        )
        await db.commit()
        closed = await run_position_monitor(db)

        row = await (await db.execute(
            "SELECT exit_reason, exit_debit, realized_pnl FROM ic_positions WHERE symbol='SPY'"
        )).fetchone()
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "force_close"
    # realized_pnl = (1.00 - 0.75) * 100 * 1 = $25 (modest gain captured)
    assert closed[0]["realized_pnl"] == pytest.approx(25.0)
    assert row["exit_reason"] == "force_close"
    assert row["exit_debit"] == pytest.approx(0.75)
    assert row["realized_pnl"] == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_force_close_leaves_pnl_null_when_debit_missing(tmp_path):
    """If ic_refresh never landed a debit, realized_pnl stays NULL rather
    than getting a misleading hardcoded 0."""
    db_path = str(tmp_path / "test.db")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
                spread_width, entry_credit, contracts, max_loss, regime_at_entry,
                current_debit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("SPY", yesterday, yesterday, 760.0, 765.0, 735.0, 730.0,
             5.0, 1.00, 1, 400.0, "normal", None),
        )
        await db.commit()
        closed = await run_position_monitor(db)

        row = await (await db.execute(
            "SELECT exit_reason, exit_debit, realized_pnl FROM ic_positions WHERE symbol='SPY'"
        )).fetchone()
    assert len(closed) == 1
    assert closed[0]["realized_pnl"] is None
    assert row["exit_reason"] == "force_close"
    assert row["exit_debit"] is None
    assert row["realized_pnl"] is None
