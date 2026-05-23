"""Tests for the Discord notification module."""
import httpx
import pytest

from kotorid.db import get_db, init_db
from kotorid.notify import format_alert_embed, notify_pending_alerts, webhook_url


def test_format_alert_embed_known_type_uses_styled_title():
    """profit_target alerts get green color + green-circle prefix."""
    payload = format_alert_embed({
        "alert_type": "profit_target",
        "symbol": "SPY",
        "message": "P&L $+50",
        "triggered_at": "2026-05-23T19:00:00+00:00",
    })
    assert "embeds" in payload
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert embed["title"] == "🟢 Profit Target — SPY"
    assert embed["description"] == "P&L $+50"
    assert embed["color"] == 3066993  # green
    assert embed["timestamp"] == "2026-05-23T19:00:00+00:00"
    assert embed["footer"] == {"text": "kotori"}


def test_format_alert_embed_stop_loss_red():
    payload = format_alert_embed({
        "alert_type": "stop_loss",
        "symbol": "QQQ",
        "message": "P&L $-200",
        "triggered_at": "2026-05-23T19:00:00+00:00",
    })
    assert payload["embeds"][0]["color"] == 15158332  # red
    assert "Stop Loss" in payload["embeds"][0]["title"]


def test_format_alert_embed_unknown_type_falls_back_to_gray():
    payload = format_alert_embed({
        "alert_type": "weird_new_type",
        "symbol": None,
        "message": "something happened",
        "triggered_at": None,
    })
    embed = payload["embeds"][0]
    assert embed["color"] == 9807270  # gray
    assert "weird_new_type" in embed["title"]
    assert embed["title"].endswith("— —")  # NULL symbol renders as em-dash


def test_webhook_url_reads_env(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/abc")
    assert webhook_url() == "https://discord.com/api/webhooks/123/abc"


def test_webhook_url_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert webhook_url() is None


def test_webhook_url_returns_none_when_whitespace(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "   ")
    assert webhook_url() is None


def _capture_handler(captured: list):
    """Return an httpx handler that records each POST body and returns 204."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({
            "url": str(request.url),
            "body": request.content.decode(),
        })
        return httpx.Response(204)
    return handler


@pytest.mark.asyncio
async def test_notify_pending_alerts_posts_and_marks(tmp_path):
    """Every unnotified alert gets POSTed and marked notified_at."""
    captured: list = []
    db_path = str(tmp_path / "notify.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO alerts (symbol, alert_type, message, triggered_at)
               VALUES (?,?,?,?)""",
            ("SPY", "profit_target", "P&L $+50", "2026-05-23T19:00:00+00:00"),
        )
        await db.execute(
            """INSERT INTO alerts (symbol, alert_type, message, triggered_at)
               VALUES (?,?,?,?)""",
            ("QQQ", "stop_loss", "P&L $-200", "2026-05-23T19:01:00+00:00"),
        )
        await db.commit()

        transport = httpx.MockTransport(_capture_handler(captured))
        async with httpx.AsyncClient(transport=transport) as client:
            sent = await notify_pending_alerts(
                db, client, "https://discord.com/api/webhooks/X/Y"
            )
        assert sent == 2
        assert len(captured) == 2
        assert all(c["url"] == "https://discord.com/api/webhooks/X/Y" for c in captured)

        notified_count = await (await db.execute(
            "SELECT COUNT(*) AS n FROM alerts WHERE notified_at IS NOT NULL"
        )).fetchone()
        assert notified_count["n"] == 2


@pytest.mark.asyncio
async def test_notify_pending_alerts_skips_already_notified(tmp_path):
    """Alerts with notified_at set don't get re-posted."""
    captured: list = []
    db_path = str(tmp_path / "skip.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO alerts (symbol, alert_type, message, triggered_at, notified_at)
               VALUES (?,?,?,?,?)""",
            ("SPY", "profit_target", "P&L $+50",
             "2026-05-23T19:00:00+00:00", "2026-05-23T19:00:30+00:00"),
        )
        await db.commit()

        transport = httpx.MockTransport(_capture_handler(captured))
        async with httpx.AsyncClient(transport=transport) as client:
            sent = await notify_pending_alerts(
                db, client, "https://discord.com/api/webhooks/X/Y"
            )
        assert sent == 0
        assert captured == []


@pytest.mark.asyncio
async def test_notify_pending_alerts_continues_on_single_post_failure(tmp_path):
    """If Discord returns 500 on one alert, that alert stays unnotified,
    but the next alert still gets posted."""
    def flaky_handler(request: httpx.Request) -> httpx.Response:
        # First call fails, second succeeds
        if "SPY" in request.content.decode():
            return httpx.Response(500, text="discord ate it")
        return httpx.Response(204)

    db_path = str(tmp_path / "flaky.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO alerts (symbol, alert_type, message, triggered_at)
               VALUES (?,?,?,?)""",
            ("SPY", "profit_target", "this will fail",
             "2026-05-23T19:00:00+00:00"),
        )
        await db.execute(
            """INSERT INTO alerts (symbol, alert_type, message, triggered_at)
               VALUES (?,?,?,?)""",
            ("QQQ", "stop_loss", "this will succeed",
             "2026-05-23T19:01:00+00:00"),
        )
        await db.commit()

        transport = httpx.MockTransport(flaky_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            sent = await notify_pending_alerts(
                db, client, "https://discord.com/api/webhooks/X/Y"
            )
        assert sent == 1  # only the QQQ one made it

        rows = await (await db.execute(
            "SELECT symbol, notified_at FROM alerts ORDER BY symbol"
        )).fetchall()
        by_symbol = {r["symbol"]: r["notified_at"] for r in rows}
    assert by_symbol["QQQ"] is not None
    assert by_symbol["SPY"] is None  # left for retry on next cycle
