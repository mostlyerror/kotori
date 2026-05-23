import pytest
import httpx

from kotorid.db import get_db, init_db
from kotorid.jobs import post_latest_briefing_to_discord


@pytest.mark.asyncio
async def test_post_latest_briefing_posts_today_briefing(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            "INSERT INTO briefings (period, content, generated_at) "
            "VALUES ('daily', 'Today: hold positions, watch SPY 730 short put.', "
            "datetime('now'))"
        )
        await db.commit()

        posted_payloads = []
        def handler(request):
            posted_payloads.append(request.read())
            return httpx.Response(204)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ok = await post_latest_briefing_to_discord(
                db, client, "https://discord.test/webhook"
            )

    assert ok is True
    assert len(posted_payloads) == 1
    assert b"hold positions" in posted_payloads[0]


@pytest.mark.asyncio
async def test_post_latest_briefing_returns_false_when_no_briefing(tmp_path):
    """No briefing today -> returns False, no POST attempted."""
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)

        posted = []
        def handler(request):
            posted.append(1)
            return httpx.Response(204)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ok = await post_latest_briefing_to_discord(
                db, client, "https://discord.test/webhook"
            )
    assert ok is False
    assert posted == []
