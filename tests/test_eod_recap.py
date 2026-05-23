import pytest
from datetime import date

from kotorid.db import get_db, init_db
from kotorid.jobs import build_eod_recap_payload


@pytest.mark.asyncio
async def test_eod_recap_includes_realized_pnl_today(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        # Closed today: profit_target, +$50 realized
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit, exit_reason,
                exit_debit, realized_pnl)
               VALUES ('SPY','2026-05-19','2026-05-22',
                       760,765,735,730,5,1.00,1,400,0.50,
                       'profit_target', 0.50, 50.0)"""
        )
        # Open: SPY 5/29, current debit $0.82
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,0.82)"""
        )
        # An alert row dated today for the profit_target close, so the recap query matches
        await db.execute(
            "INSERT INTO alerts (symbol, alert_type, message, triggered_at) "
            "VALUES ('SPY','profit_target','SPY closed', datetime('now'))"
        )
        await db.commit()

        payload = await build_eod_recap_payload(db)

    assert payload["embeds"][0]["title"].startswith("📈 EOD Recap")
    desc = payload["embeds"][0]["description"]
    assert "+$50" in desc
    assert "1 IC open" in desc or "1 ICs open" in desc
