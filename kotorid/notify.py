"""Post alerts to Discord via a webhook.

Polls the ``alerts`` table for rows with ``notified_at IS NULL``, POSTs
each as a colored Discord embed, and marks them notified. Decoupled from
the alert-writing code paths (position_monitor, order_placement, etc.)
so a Discord outage doesn't break local alerting.

Activation: only runs when ``DISCORD_WEBHOOK_URL`` is set in the env.
The scheduler in ``__main__.py`` skips registering the job otherwise.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import aiosqlite
import httpx

from kotorid.alerts_lib import parse_alert_message

log = logging.getLogger(__name__)

# Discord embed colors (decimal RGB). Picked for at-a-glance reading.
_COLOR_GREEN = 3066993   # profit_target / ic_placed
_COLOR_RED = 15158332    # stop_loss
_COLOR_BLUE = 3447003    # force_close
_COLOR_ORANGE = 15105570 # gap_risk
_COLOR_GRAY = 9807270    # default / unknown alert types

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
    "order_placed":  ("🟢 Order Placed", _COLOR_GREEN),  # legacy
}


def format_alert_embed(alert: dict) -> dict:
    """Build the Discord webhook payload for an alert row.

    Returns the full payload dict (with an ``embeds`` array of one), not
    just the embed — so the caller can httpx.post(..., json=payload)
    directly without further wrapping.

    Structured alerts (created via ``alerts_lib.create_alert`` with
    ``body_lines`` / ``fields``) render the headline as the embed title
    and the body lines joined by newlines as the description. Legacy
    rows (plain string in ``message``) keep the previous behavior.
    """
    alert_type = alert.get("alert_type") or "unknown"
    title, color = _ALERT_STYLE.get(alert_type, (f"⚪ {alert_type}", _COLOR_GRAY))
    symbol = alert.get("symbol") or "—"
    raw_message = alert.get("message") or ""
    triggered_at = alert.get("triggered_at") or datetime.now(tz=timezone.utc).isoformat()

    plain, payload = parse_alert_message(raw_message)
    if payload:
        # Structured: first line of `plain` is the headline; body_lines follow.
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
    db: aiosqlite.Connection, client: httpx.AsyncClient, webhook_url: str,
) -> int:
    """Post every alert with notified_at IS NULL, mark them notified.

    Returns the count successfully posted. A single POST failure on one
    alert doesn't block the rest — we log and continue. The notified_at
    column gets set per-row, so a partial run is safe to resume.
    """
    cursor = await db.execute(
        "SELECT id, symbol, alert_type, message, triggered_at "
        "FROM alerts WHERE notified_at IS NULL ORDER BY id"
    )
    pending = await cursor.fetchall()
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
        await db.execute(
            "UPDATE alerts SET notified_at=? WHERE id=?", (now_iso, alert["id"]),
        )
        sent += 1

    await db.commit()
    log.info("notify_pending_alerts: posted %d alert(s) to Discord", sent)
    return sent


def webhook_url() -> str | None:
    """The configured Discord webhook URL, or None if not set."""
    raw = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    return raw or None
