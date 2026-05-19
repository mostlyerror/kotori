import logging
from datetime import datetime, timezone
import aiosqlite
from portfoliod.config import DB_PATH
from portfoliod.db import get_db
from portfoliod.position_monitor import check_exit_trigger

log = logging.getLogger(__name__)


async def run_position_monitor(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, symbol, entry_credit, current_debit, contracts FROM ic_positions "
        "WHERE exit_reason IS NULL AND current_debit IS NOT NULL"
    )
    open_ics = await cursor.fetchall()
    closed = []

    for ic in open_ics:
        reason = check_exit_trigger(ic["entry_credit"], ic["current_debit"])
        if reason is None:
            continue

        now = datetime.now(tz=timezone.utc).isoformat()
        realized_pnl = (ic["entry_credit"] - ic["current_debit"]) * 100 * ic["contracts"]

        await db.execute(
            "UPDATE ic_positions SET exit_reason=?, exit_debit=?, realized_pnl=? WHERE id=?",
            (reason, ic["current_debit"], realized_pnl, ic["id"])
        )
        await db.execute(
            """INSERT INTO alerts (symbol, alert_type, message, triggered_at)
               VALUES (?,?,?,?)""",
            (ic["symbol"], reason,
             f"{ic['symbol']} IC: {reason.replace('_',' ')} — P&L ${realized_pnl:+.0f}",
             now)
        )
        await db.execute(
            """INSERT INTO inbox_items
               (priority, item_type, symbol, title, body, actions, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("urgent" if reason == "stop_loss" else "for_review",
             reason, ic["symbol"],
             f"{ic['symbol']} IC — {reason.replace('_', ' ').title()}",
             f"Exit at ${ic['current_debit']:.2f}. P&L {realized_pnl:+.0f}. Entry credit ${ic['entry_credit']:.2f}.",
             '["acknowledge"]', now)
        )
        await db.commit()
        closed.append({"symbol": ic["symbol"], "exit_reason": reason, "realized_pnl": realized_pnl})
        log.info("position_monitor: %s %s pnl=%.0f", ic["symbol"], reason, realized_pnl)

    # Force-close expired ICs
    from datetime import date
    today = date.today().isoformat()
    cursor2 = await db.execute(
        "SELECT id, symbol, entry_credit, contracts FROM ic_positions "
        "WHERE exit_reason IS NULL AND expiry <= ?", (today,)
    )
    expired = await cursor2.fetchall()
    for ic in expired:
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        await db.execute(
            "UPDATE ic_positions SET exit_reason='force_close', realized_pnl=0 WHERE id=?",
            (ic["id"],)
        )
        await db.execute(
            "INSERT INTO alerts (symbol, alert_type, message, triggered_at) VALUES (?,?,?,?)",
            (ic["symbol"], "force_close", f"{ic['symbol']} IC expired — force closed", now_ts)
        )
        closed.append({"symbol": ic["symbol"], "exit_reason": "force_close", "realized_pnl": 0.0})
    await db.commit()

    return closed


async def position_monitor():
    async with get_db(DB_PATH) as db:
        await run_position_monitor(db)


async def iv_ingest_morning():
    log.info("iv_ingest_morning: stub")

async def gap_monitor():
    log.info("gap_monitor: stub")

async def iv_ingest_preclose():
    log.info("iv_ingest_preclose: stub")

async def ic_scan():
    log.info("ic_scan: stub")

async def order_executor():
    log.info("order_executor: stub")

async def generate_briefing():
    log.info("generate_briefing: stub")
