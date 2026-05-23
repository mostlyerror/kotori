import json

import pytest

from kotorid.alerts_lib import create_alert, ALERT_FIELDS_KEY
from kotorid.db import get_db, init_db


@pytest.mark.asyncio
async def test_create_alert_inserts_row_with_structured_fields(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        alert_id = await create_alert(
            db,
            alert_type="stop_loss",
            symbol="SPY",
            headline="Stop Loss — SPY 5/29",
            body_lines=[
                "Closed at debit $1.85 (entry credit $1.00).",
                "Loss: −$400 (100% of max).",
            ],
            fields={"entry_credit": 1.00, "exit_debit": 1.85, "realized_pnl": -400.0},
        )
        await db.commit()
        cur = await db.execute(
            "SELECT alert_type, symbol, message FROM alerts WHERE id=?",
            (alert_id,),
        )
        row = await cur.fetchone()

    assert row is not None
    assert row["alert_type"] == "stop_loss"
    assert row["symbol"] == "SPY"
    # The structured payload is embedded in `message` as a JSON tail after a marker.
    assert ALERT_FIELDS_KEY in row["message"]
    legacy, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    assert "Stop Loss — SPY 5/29" in legacy
    parsed = json.loads(json_tail)
    assert parsed["fields"]["entry_credit"] == 1.00
    assert parsed["body_lines"] == [
        "Closed at debit $1.85 (entry credit $1.00).",
        "Loss: −$400 (100% of max).",
    ]


@pytest.mark.asyncio
async def test_create_alert_without_fields_keeps_plain_message(tmp_path):
    """Legacy compat: callers that only pass a plain string still work."""
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await create_alert(
            db, alert_type="custom", symbol="X", headline="plain text only",
        )
        await db.commit()
        cur = await db.execute("SELECT message FROM alerts WHERE alert_type='custom'")
        row = await cur.fetchone()

    assert ALERT_FIELDS_KEY not in row["message"]
    assert "plain text only" in row["message"]
