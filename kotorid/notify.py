"""Post alerts to Discord via a webhook."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import asyncpg
import httpx

from kotorid.alerts_lib import parse_alert_message

log = logging.getLogger(__name__)

_COLOR_GREEN = 3066993
_COLOR_RED = 15158332
_COLOR_BLUE = 3447003
_COLOR_ORANGE = 15105570
_COLOR_GRAY = 9807270

_ALERT_STYLE = {
    "profit_target": ("🟢 Profit Target", _COLOR_GREEN),
    "stop_loss":     ("🔴 Stop Loss", _COLOR_RED),
    "force_close":   ("🔵 IC Closed (Expiry)", _COLOR_BLUE),
    "ic_placed":     ("🟢 IC Placed", _COLOR_GREEN),
    "gap_risk":      ("🟠 Gap Risk", _COLOR_ORANGE),
    "candidate_ready": ("⚠️ Candidates Ready", _COLOR_ORANGE),
    "dte_warning":   ("⚠️ 1 Day to Expiry", _COLOR_ORANGE),
    "position_warning": ("⚠️ Position Warning", _COLOR_ORANGE),
    "short_strike_threatened": ("🚨 Short Strike Threatened", _COLOR_RED),
    "order_placed":  ("🟢 Order Placed", _COLOR_GREEN),
    "order_filled":  ("🟢 Order Filled", _COLOR_GREEN),
    "order_failed":  ("⚠️ Order Failed", _COLOR_ORANGE),
}


def format_alert_embed(alert: dict) -> dict:
    alert_type = alert.get("alert_type") or "unknown"
    title, color = _ALERT_STYLE.get(alert_type, (f"⚪ {alert_type}", _COLOR_GRAY))
    symbol = alert.get("symbol") or "—"
    raw_message = alert.get("message") or ""
    triggered_at = alert.get("triggered_at") or datetime.now(tz=timezone.utc).isoformat()

    plain, payload = parse_alert_message(raw_message)
    if payload:
        body_lines = payload.get("body_lines") or []
        description = "\n".join(body_lines) if body_lines else plain
    else:
        description = plain

    return {
        "embeds": [{
            "title": f"{title} — {symbol}",
            "description": description,
            "color": color,
            "timestamp": triggered_at,
            "footer": {"text": "kotori"},
        }],
    }


async def notify_pending_alerts(
    conn: asyncpg.Connection, client: httpx.AsyncClient, webhook_url: str,
) -> int:
    pending = await conn.fetch(
        "SELECT id, symbol, alert_type, message, triggered_at "
        "FROM alerts WHERE notified_at IS NULL ORDER BY id"
    )
    if not pending:
        return 0

    sent = 0
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for alert in pending:
        payload = format_alert_embed(dict(alert))
        try:
            resp = await client.post(webhook_url, json=payload, timeout=10.0)
            resp.raise_for_status()
        except httpx.HTTPError:
            log.exception(
                "notify_pending_alerts: POST failed for alert id=%s; will retry next cycle",
                alert["id"],
            )
            continue
        await conn.execute(
            "UPDATE alerts SET notified_at=$1 WHERE id=$2", now_iso, alert["id"],
        )
        sent += 1

    log.info("notify_pending_alerts: posted %d alert(s) to Discord", sent)
    return sent


def webhook_url() -> str | None:
    raw = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    return raw or None
