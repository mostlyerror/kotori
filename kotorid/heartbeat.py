"""Heartbeat: a one-line state digest posted to Discord every 15 min.

Heartbeats are state snapshots, not events. They bypass the ``alerts``
table and post directly through ``post_heartbeat``. The whole point is
that a quiet period still produces predictable, scannable lines so the
user knows the system is alive.
"""
from __future__ import annotations

import logging
from datetime import date

import aiosqlite
import httpx

log = logging.getLogger(__name__)


def _short_expiry(iso: str) -> str:
    """Render '2026-05-29' as '5/29'."""
    try:
        y, m, d = iso.split("-")
        return f"{int(m)}/{int(d)}"
    except (ValueError, AttributeError):
        return iso


async def build_heartbeat_line(
    db: aiosqlite.Connection, *, now_ct_label: str,
) -> str:
    """Build the one-line heartbeat string from current DB state."""
    cur = await db.execute(
        "SELECT symbol, expiry, entry_credit, current_debit, contracts "
        "FROM ic_positions WHERE exit_reason IS NULL"
    )
    open_ics = await cur.fetchall()

    parts: list[str] = [f"ℹ️  {now_ct_label}"]
    parts.append(f"{len(open_ics)} IC{'s' if len(open_ics) != 1 else ''}")

    for ic in open_ics:
        sym = ic["symbol"]
        exp = _short_expiry(ic["expiry"])
        debit = ic["current_debit"]
        entry = ic["entry_credit"] or 0
        if debit is not None and entry > 0:
            pnl_dollars = (entry - debit) * 100 * (ic["contracts"] or 1)
            pnl_pct = (entry - debit) / entry
            parts.append(
                f"{sym} {exp} debit ${debit:.2f} "
                f"(P/L ${pnl_dollars:+.0f}, {pnl_pct:+.0%})"
            )
        else:
            parts.append(f"{sym} {exp} debit ?")

    # Last scan outcome (today's candidates row count)
    scan_cur = await db.execute(
        "SELECT COUNT(*) FROM candidates WHERE scan_date=?",
        (date.today().isoformat(),),
    )
    (today_candidates,) = await scan_cur.fetchone()
    if today_candidates > 0:
        parts.append(f"scan: {today_candidates} candidate(s) today")
    else:
        parts.append("scan: 0 candidates today")

    return " · ".join(parts)


async def post_heartbeat(
    client: httpx.AsyncClient, webhook_url: str, line: str,
) -> bool:
    """POST the heartbeat as a low-color embed; return True on success."""
    payload = {
        "embeds": [{
            "description": line,
            "color": 9807270,  # gray, distinct from event colors
            "footer": {"text": "kotori heartbeat"},
        }]
    }
    try:
        resp = await client.post(webhook_url, json=payload, timeout=10.0)
        resp.raise_for_status()
        return True
    except httpx.HTTPError:
        log.exception("post_heartbeat: POST failed; will retry next cycle")
        return False
