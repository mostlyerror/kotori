import pytest

from kotorid.db import get_db, init_db
from kotorid.heartbeat import build_heartbeat_line


@pytest.mark.asyncio
async def test_heartbeat_line_with_no_positions(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        line = await build_heartbeat_line(db, now_ct_label="14:30 CT")
    assert "14:30 CT" in line
    assert "0 ICs" in line


@pytest.mark.asyncio
async def test_heartbeat_line_with_one_ic_open(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,0.82)"""
        )
        await db.commit()
        line = await build_heartbeat_line(db, now_ct_label="14:30 CT")

    assert "1 IC" in line
    assert "SPY" in line
    assert "5/29" in line or "2026-05-29" in line
    # P/L per share = (1.00 - 0.82) = 0.18; dollars = 18.
    # Format may show "+$18" or "+18" — either acceptable.
    assert "+$18" in line or "+18" in line


@pytest.mark.asyncio
async def test_heartbeat_line_includes_last_scan_outcome(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc).isoformat()
        # Insert a candidates row to represent "today's scan"
        await db.execute(
            """INSERT INTO candidates (symbol, scan_date, order_status, expected_credit,
                                       contracts, max_loss, short_call, long_call,
                                       short_put, long_put)
               VALUES ('SPY', date('now'), 'pending_approval', 1.00, 1, 400,
                       760, 765, 735, 730)"""
        )
        await db.commit()
        line = await build_heartbeat_line(db, now_ct_label="14:30 CT")
    assert "scan:" in line.lower()


import httpx


@pytest.mark.asyncio
async def test_post_heartbeat_returns_true_on_204():
    from kotorid.heartbeat import post_heartbeat

    def handler(request):
        return httpx.Response(204)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await post_heartbeat(client, "https://discord.test/webhook", "ℹ️ test")
    assert ok is True


@pytest.mark.asyncio
async def test_post_heartbeat_returns_false_on_error():
    from kotorid.heartbeat import post_heartbeat

    def handler(request):
        return httpx.Response(500)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await post_heartbeat(client, "https://discord.test/webhook", "ℹ️ test")
    assert ok is False
