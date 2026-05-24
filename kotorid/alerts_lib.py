"""Centralized alert creation.

Every alert in kotori flows through ``create_alert``. The function writes
to the ``alerts`` table the existing ``notify_alerts`` job already polls,
but augments the legacy single-string ``message`` column with optional
*structured* fields (a JSON tail appended after a sentinel marker).

``notify.format_alert_embed`` parses the marker and renders rich Discord
embeds. Legacy rows without the marker render the bare ``message`` as
before — so this is fully backward compatible.

Format of message column when structured payload is present::

    <headline>\\n<body line 1>\\n<body line 2>...\\n<ALERT_FIELDS_KEY><json>

The sentinel keeps the payload parseable without a schema change.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg

ALERT_FIELDS_KEY = "\n---KOTORI_STRUCTURED---\n"


async def create_alert(
    conn: asyncpg.Connection,
    *,
    alert_type: str,
    symbol: str,
    headline: str,
    body_lines: list[str] | None = None,
    fields: dict[str, Any] | None = None,
    triggered_at: str | None = None,
) -> int:
    """Insert one row into the alerts table; return its id.

    asyncpg auto-commits each statement, so no explicit commit needed.
    """
    body_lines = body_lines or []
    now = triggered_at or datetime.now(tz=timezone.utc).isoformat()

    if fields or body_lines:
        plain = headline if not body_lines else headline + "\n" + "\n".join(body_lines)
        payload = {"body_lines": body_lines, "fields": fields or {}}
        message = plain + ALERT_FIELDS_KEY + json.dumps(payload, default=str)
    else:
        message = headline

    aid = await conn.fetchval(
        "INSERT INTO alerts (symbol, alert_type, message, triggered_at) "
        "VALUES ($1,$2,$3,$4) RETURNING id",
        symbol, alert_type, message, now,
    )
    return aid


def parse_alert_message(message: str) -> tuple[str, dict[str, Any]]:
    """Split a stored message into (plain_text, structured_payload).

    Returns ``(plain, {})`` for legacy rows without the marker.
    """
    if ALERT_FIELDS_KEY not in message:
        return message, {}
    plain, _, json_tail = message.partition(ALERT_FIELDS_KEY)
    try:
        payload = json.loads(json_tail)
    except json.JSONDecodeError:
        return message, {}
    return plain, payload
