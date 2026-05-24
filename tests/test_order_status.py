import json
import pytest
import httpx

from kotorid.alerts_lib import ALERT_FIELDS_KEY
from kotorid.order_status import check_open_orders


@pytest.mark.asyncio
async def test_check_open_orders_emits_order_filled_on_fill(conn):
    """Fully-filled order produces order_filled alert with slippage."""
    # IC placed with estimate $1.00, order_id 12345, fill_status NULL
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, order_id)
           VALUES ('SPY','2026-05-22','2026-05-29',
                   760,765,735,730,5,1.00,1,400,'12345')"""
    )

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
        await check_open_orders(conn, client, account_id="VA1")

    row = await conn.fetchrow("SELECT message FROM alerts WHERE alert_type='order_filled'")

    assert row is not None
    assert ALERT_FIELDS_KEY in row["message"]
    _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    fields = json.loads(json_tail)["fields"]
    assert fields["order_id"] == "12345"
    assert fields["fill_credit"] == 0.98
    assert fields["estimated_credit"] == 1.00
    # slippage = (fill - estimate) / estimate = -0.02
    assert fields["slippage_pct"] == pytest.approx(-0.02, abs=1e-4)


@pytest.mark.asyncio
async def test_check_open_orders_emits_order_failed_on_reject(conn):
    """Rejected order produces order_failed alert with reason."""
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, order_id)
           VALUES ('SPY','2026-05-22','2026-05-29',
                   760,765,735,730,5,1.00,1,400,'99999')"""
    )

    def handler(request):
        return httpx.Response(200, json={
            "order": {
                "id": 99999, "status": "rejected",
                "reason_description": "Insufficient buying power",
            }
        })
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://x/v1") as client:
        await check_open_orders(conn, client, account_id="VA1")

    row = await conn.fetchrow("SELECT message FROM alerts WHERE alert_type='order_failed'")
    assert row is not None
    _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    fields = json.loads(json_tail)["fields"]
    assert fields["status"] == "rejected"
    assert "Insufficient buying power" in fields["reason"]


@pytest.mark.asyncio
async def test_check_open_orders_dedup(conn):
    """Polling twice on the same filled order produces only one alert."""
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, order_id)
           VALUES ('SPY','2026-05-22','2026-05-29',
                   760,765,735,730,5,1.00,1,400,'77777')"""
    )

    def handler(request):
        return httpx.Response(200, json={
            "order": {"id": 77777, "status": "filled", "avg_fill_price": 1.00}
        })
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://x/v1") as client:
        await check_open_orders(conn, client, account_id="VA1")
        await check_open_orders(conn, client, account_id="VA1")

    count = await conn.fetchval(
        "SELECT COUNT(*) FROM alerts WHERE alert_type='order_filled'"
    )
    assert count == 1
