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
