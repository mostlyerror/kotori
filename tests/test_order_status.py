import json
import pytest
import httpx

from kotorid.alerts_lib import ALERT_FIELDS_KEY
from kotorid.db import get_db, init_db
from kotorid.order_status import check_open_orders


@pytest.mark.asyncio
async def test_check_open_orders_emits_order_filled_on_fill(tmp_path):
    """Fully-filled order produces order_filled alert with slippage."""
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        # IC placed with estimate $1.00, order_id 12345, fill_status NULL
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, order_id)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,'12345')"""
        )
        await db.commit()

        # Mock Tradier: order status = filled, avg_fill_price = 0.98 net credit
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "order": {
                    "id": 12345, "status": "filled", "avg_fill_price": 0.98,
                    "class": "multileg",
                }
            })
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://x/v1") as client:
            await check_open_orders(db, client, account_id="VA1")

        cur = await db.execute("SELECT message FROM alerts WHERE alert_type='order_filled'")
        row = await cur.fetchone()

    assert row is not None
    assert ALERT_FIELDS_KEY in row["message"]
    _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    fields = json.loads(json_tail)["fields"]
    assert fields["order_id"] == "12345"
    assert fields["fill_credit"] == 0.98
    assert fields["estimated_credit"] == 1.00
    # slippage = (fill - estimate) / estimate = -0.02
    assert fields["slippage_pct"] == pytest.approx(-0.02, abs=1e-4)
