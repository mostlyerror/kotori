"""Poll Tradier order status for placed ICs; emit order_filled/order_failed.

Iterates ic_positions rows whose order_id is set but whose status hasn't
been resolved yet (we treat status as resolved once an order_filled or
order_failed alert exists for that order_id, OR the IC has exit_reason
set).

Tradier returns avg_fill_price for filled multileg orders as the net
credit per share. Slippage is computed against the candidate's estimated
entry_credit.
"""
from __future__ import annotations

import logging

import aiosqlite
import httpx

from kotorid.alerts_lib import create_alert

log = logging.getLogger(__name__)


async def _already_resolved(db: aiosqlite.Connection, order_id: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM alerts "
        "WHERE alert_type IN ('order_filled','order_failed') "
        "  AND symbol IN (SELECT symbol FROM ic_positions WHERE order_id=?) "
        "LIMIT 1",
        (order_id,),
    )
    return (await cur.fetchone()) is not None


async def check_open_orders(
    db: aiosqlite.Connection, client: httpx.AsyncClient, account_id: str,
) -> int:
    """Poll Tradier for each ic_position with an unresolved order_id.

    Returns the number of order alerts created in this pass.
    """
    cur = await db.execute(
        "SELECT id, symbol, order_id, entry_credit FROM ic_positions "
        "WHERE order_id IS NOT NULL AND exit_reason IS NULL"
    )
    rows = await cur.fetchall()
    created = 0

    for ic in rows:
        order_id = ic["order_id"]
        if await _already_resolved(db, order_id):
            continue

        try:
            resp = await client.get(
                f"/accounts/{account_id}/orders/{order_id}",
                params={"includeTags": "true"},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            log.warning("check_open_orders: failed to fetch order %s; will retry", order_id)
            continue

        order = resp.json().get("order", {}) or {}
        status = (order.get("status") or "").lower()

        if status == "filled":
            fill_credit = float(order.get("avg_fill_price") or 0.0)
            estimate = float(ic["entry_credit"] or 0.0)
            slippage = (fill_credit - estimate) / estimate if estimate else 0.0
            await create_alert(
                db,
                alert_type="order_filled",
                symbol=ic["symbol"],
                headline=f"Order Filled — {ic['symbol']}",
                body_lines=[
                    f"Multileg order {order_id} filled at credit ${fill_credit:.2f} "
                    f"(estimated ${estimate:.2f}, slippage {slippage:+.1%}).",
                ],
                fields={
                    "order_id": str(order_id),
                    "fill_credit": fill_credit,
                    "estimated_credit": estimate,
                    "slippage_pct": slippage,
                },
            )
            created += 1
        elif status in ("rejected", "canceled", "expired"):
            reason = order.get("reason_description") or status
            await create_alert(
                db,
                alert_type="order_failed",
                symbol=ic["symbol"],
                headline=f"Order Failed — {ic['symbol']}",
                body_lines=[
                    f"Multileg order {order_id} status: {status}.",
                    f"Reason: {reason}.",
                ],
                fields={
                    "order_id": str(order_id),
                    "status": status,
                    "reason": reason,
                },
            )
            created += 1
        # statuses like 'pending', 'open', 'partially_filled' — keep polling

    if created:
        await db.commit()
    return created
