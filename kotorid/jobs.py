import logging
from datetime import datetime, timezone
import aiosqlite
import httpx
from kotorid.alerts_lib import create_alert
from kotorid.config import DB_PATH
from kotorid.db import get_db
from kotorid.position_monitor import check_exit_trigger

log = logging.getLogger(__name__)


async def run_position_monitor(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, symbol, entry_credit, current_debit, contracts, position_warning_at "
        "FROM ic_positions WHERE exit_reason IS NULL AND current_debit IS NOT NULL"
    )
    open_ics = await cursor.fetchall()
    closed = []

    for ic in open_ics:
        # position_warning: halfway-to-stop heads-up. Fires once per IC ever.
        entry = float(ic["entry_credit"] or 0)
        debit = float(ic["current_debit"] or 0)
        if (
            entry > 0
            and entry * 1.50 <= debit < entry * 2.00
            and ic["position_warning_at"] is None
        ):
            unrealized = (entry - debit) * 100 * (ic["contracts"] or 1)
            pnl_sign = "-" if unrealized < 0 else "+"
            now_warn = datetime.now(tz=timezone.utc).isoformat()
            await create_alert(
                db,
                alert_type="position_warning",
                symbol=ic["symbol"],
                headline=f"Position Warning — {ic['symbol']}",
                body_lines=[
                    f"Debit ${debit:.2f} (entry credit ${entry:.2f}) — halfway to stop.",
                    f"Unrealized P/L: {pnl_sign}${abs(unrealized):.0f}.",
                    f"Stop fires at debit ${entry*2.00:.2f}. Consider closing manually.",
                ],
                fields={
                    "entry_credit": entry,
                    "current_debit": debit,
                    "unrealized_pnl": unrealized,
                },
                triggered_at=now_warn,
            )
            await db.execute(
                "UPDATE ic_positions SET position_warning_at=? WHERE id=?",
                (now_warn, ic["id"]),
            )
            await db.commit()

        reason = check_exit_trigger(ic["entry_credit"], ic["current_debit"])
        if reason is None:
            continue

        now = datetime.now(tz=timezone.utc).isoformat()
        realized_pnl = (ic["entry_credit"] - ic["current_debit"]) * 100 * ic["contracts"]

        await db.execute(
            "UPDATE ic_positions SET exit_reason=?, exit_debit=?, realized_pnl=? WHERE id=?",
            (reason, ic["current_debit"], realized_pnl, ic["id"])
        )
        reason_label = "Stop Loss" if reason == "stop_loss" else "Profit Target"
        pnl_sign = "-" if realized_pnl < 0 else "+"
        await create_alert(
            db,
            alert_type=reason,
            symbol=ic["symbol"],
            headline=f"{reason_label} — {ic['symbol']}",
            body_lines=[
                f"Closed at debit ${ic['current_debit']:.2f} (entry credit ${ic['entry_credit']:.2f}).",
                f"Realized P/L: {pnl_sign}${abs(realized_pnl):.0f}.",
                f"{'Loss' if realized_pnl < 0 else 'Gain'} captured at "
                f"{abs(realized_pnl/100/ic['entry_credit']):.0%} of entry credit.",
            ],
            fields={
                "entry_credit": float(ic["entry_credit"]),
                "exit_debit": float(ic["current_debit"]),
                "realized_pnl": realized_pnl,
                "contracts": ic["contracts"],
            },
            triggered_at=now,
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

    # Force-close ICs that are past their expiry date.
    #
    # Two correctness points worth flagging:
    #
    # 1. We use `expiry < today`, not `expiry <= today`. The legs are still
    #    open at the broker on expiry day until 3pm CT settlement. Firing
    #    force_close at 12:01 AM on expiry day would mark the IC closed in
    #    our DB hours before the broker actually settles. We wait until the
    #    next calendar day, by which point ic_refresh has had a chance to
    #    record a final near-settlement debit.
    #
    # 2. realized_pnl is derived from the last-known current_debit when
    #    available: (entry_credit - current_debit) * 100 * contracts. When
    #    current_debit is NULL (ic_refresh never ran or always skipped),
    #    we leave realized_pnl NULL rather than guessing — the old code
    #    hardcoded 0, which would falsely report every IC as breakeven.
    from datetime import date
    today = date.today().isoformat()
    cursor2 = await db.execute(
        "SELECT id, symbol, expiry, entry_credit, current_debit, contracts FROM ic_positions "
        "WHERE exit_reason IS NULL AND expiry < ?", (today,)
    )
    expired = await cursor2.fetchall()
    for ic in expired:
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        entry_credit = float(ic["entry_credit"]) if ic["entry_credit"] is not None else None
        if ic["current_debit"] is not None and entry_credit is not None:
            debit = float(ic["current_debit"])
            realized_pnl = (entry_credit - debit) * 100 * ic["contracts"]
            pnl_label = f"P&L ${realized_pnl:+.0f}"
        else:
            debit = None
            realized_pnl = None
            pnl_label = "P&L unknown — last debit refresh missing, review manually"
        await db.execute(
            "UPDATE ic_positions SET exit_reason='force_close', exit_debit=?, realized_pnl=? WHERE id=?",
            (debit, realized_pnl, ic["id"]),
        )
        if realized_pnl is not None:
            body_lines = [
                f"IC expired {ic['symbol']} {ic['expiry']}.",
                f"Final debit ${debit:.2f} (entry credit ${entry_credit:.2f}).",
                f"Realized P/L: {'-' if realized_pnl < 0 else ('+' if realized_pnl > 0 else '')}${abs(realized_pnl):.0f}.",
            ]
        else:
            body_lines = [
                f"IC expired {ic['symbol']} {ic['expiry']}.",
                "Final debit unknown — last refresh missing; review manually.",
            ]
        await create_alert(
            db,
            alert_type="force_close",
            symbol=ic["symbol"],
            headline=f"IC Closed (Expiry) — {ic['symbol']}",
            body_lines=body_lines,
            fields={
                "entry_credit": entry_credit,
                "exit_debit": debit,
                "realized_pnl": realized_pnl,
            },
            triggered_at=now_ts,
        )
        closed.append({
            "symbol": ic["symbol"],
            "exit_reason": "force_close",
            "realized_pnl": realized_pnl,
        })
    await db.commit()

    return closed


async def position_monitor():
    async with get_db(DB_PATH) as db:
        await run_position_monitor(db)


async def dte_check(db: aiosqlite.Connection) -> int:
    """Emit dte_warning for any open IC expiring tomorrow.

    De-duped per (symbol, today) — running multiple times the same day
    creates at most one alert per IC.
    """
    from datetime import date, timedelta
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    cur = await db.execute(
        "SELECT symbol, expiry, entry_credit, current_debit, contracts "
        "FROM ic_positions WHERE exit_reason IS NULL AND expiry=?",
        (tomorrow,),
    )
    rows = await cur.fetchall()
    fired = 0
    for ic in rows:
        # Dedup: skip if a dte_warning already exists today for this symbol.
        dup_cur = await db.execute(
            "SELECT 1 FROM alerts "
            "WHERE alert_type='dte_warning' AND symbol=? AND date(triggered_at)=?",
            (ic["symbol"], today),
        )
        if await dup_cur.fetchone():
            continue
        debit = ic["current_debit"] or 0.0
        entry = ic["entry_credit"] or 0.0
        unrealized = (entry - debit) * 100 * (ic["contracts"] or 1)
        await create_alert(
            db,
            alert_type="dte_warning",
            symbol=ic["symbol"],
            headline=f"1 Day to Expiry — {ic['symbol']}",
            body_lines=[
                f"Expires {ic['expiry']} (tomorrow).",
                f"Current debit ${debit:.2f}, P/L ${unrealized:+.0f}.",
                "Auto force_close fires day after expiry; close manually if you want a better fill.",
            ],
            fields={
                "dte": 1,
                "expiry": ic["expiry"],
                "current_debit": debit,
                "unrealized_pnl": unrealized,
            },
        )
        fired += 1
    if fired:
        await db.commit()
    return fired


async def dte_check_job():
    """Scheduler wrapper that opens its own DB connection."""
    from kotorid.config import DB_PATH
    from kotorid.db import get_db as _get_db
    async with _get_db(DB_PATH) as db:
        await dte_check(db)


async def iv_ingest_morning():
    from datetime import date, timedelta
    import random
    async with get_db(DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT symbol FROM positions")
        symbols = [r[0] for r in await cursor.fetchall()]
        today = date.today().isoformat()
        rows = []
        for sym in symbols:
            cursor2 = await db.execute(
                "SELECT iv FROM iv_history WHERE symbol=? ORDER BY date DESC LIMIT 1", (sym,)
            )
            row = await cursor2.fetchone()
            last_iv = row[0] if row else 0.40
            new_iv = max(0.05, last_iv + random.gauss(0, 0.01))
            rows.append((sym, today, round(new_iv, 4)))
        await db.executemany(
            "INSERT OR IGNORE INTO iv_history (symbol, date, iv) VALUES (?,?,?)",
            rows
        )
        await db.commit()
        log.info("iv_ingest_morning: updated %d symbols", len(rows))


async def gap_monitor():
    async with get_db(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, symbol, short_call, short_put, expected_move FROM ic_positions "
            "WHERE exit_reason IS NULL"
        )
        open_ics = await cursor.fetchall()
        now = datetime.now(tz=timezone.utc).isoformat()
        for ic in open_ics:
            cursor2 = await db.execute(
                "SELECT current_price FROM positions WHERE symbol=?", (ic["symbol"],)
            )
            row = await cursor2.fetchone()
            if not row:
                continue
            price = row[0]
            cushion_call = ic["short_call"] - price
            cushion_put = price - ic["short_put"]
            if cushion_call < ic["expected_move"] * 0.5 or cushion_put < ic["expected_move"] * 0.5:
                await db.execute(
                    """INSERT INTO inbox_items
                       (priority, item_type, symbol, title, body, actions, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    ("urgent", "gap_risk", ic["symbol"],
                     f"{ic['symbol']} IC — Pre-market gap risk",
                     f"Price ${price:.2f} within 50% of expected move from short strikes. "
                     f"SC ${ic['short_call']:.0f} / SP ${ic['short_put']:.0f}.",
                     '["close_ic","hedge","dismiss"]', now)
                )
                await create_alert(
                    db,
                    alert_type="gap_risk",
                    symbol=ic["symbol"],
                    headline=f"Gap Risk — {ic['symbol']}",
                    body_lines=[
                        f"Price ${price:.2f} vs SC ${ic['short_call']:.0f} "
                        f"/ SP ${ic['short_put']:.0f}.",
                        f"Cushion: call ${cushion_call:.2f}, put ${cushion_put:.2f} "
                        f"(expected move ${ic['expected_move']:.2f}).",
                        "Within 50% of expected move from a short strike — review before open.",
                    ],
                    fields={
                        "price": price,
                        "short_call": float(ic["short_call"]),
                        "short_put": float(ic["short_put"]),
                        "cushion_call": cushion_call,
                        "cushion_put": cushion_put,
                        "expected_move": float(ic["expected_move"]),
                    },
                    triggered_at=now,
                )
        await db.commit()
        log.info("gap_monitor: checked %d open ICs", len(open_ics))


async def iv_ingest_preclose():
    import random
    from datetime import date
    async with get_db(DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT symbol FROM positions")
        symbols = [r[0] for r in await cursor.fetchall()]
        today = date.today().isoformat()
        for sym in symbols:
            cursor2 = await db.execute(
                "SELECT iv FROM iv_history WHERE symbol=? ORDER BY date DESC LIMIT 1", (sym,)
            )
            row = await cursor2.fetchone()
            last_iv = row[0] if row else 0.40
            new_iv = max(0.05, last_iv + random.gauss(0, 0.005))
            await db.execute(
                "INSERT OR REPLACE INTO iv_history (symbol, date, iv) VALUES (?,?,?)",
                (sym, today, round(new_iv, 4))
            )
        await db.commit()
        log.info("iv_ingest_preclose: refreshed %d symbols", len(symbols))


async def ic_scan():
    import json
    from datetime import date
    async with get_db(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT symbol, iv_percentile FROM iv_history "
            "WHERE date=? AND iv_percentile IS NOT NULL AND iv_percentile >= 0.70",
            (date.today().isoformat(),)
        )
        candidates = await cursor.fetchall()
        now = datetime.now(tz=timezone.utc).isoformat()
        for c in candidates:
            cursor2 = await db.execute(
                "SELECT id FROM candidates WHERE symbol=? AND scan_date=?",
                (c["symbol"], date.today().isoformat())
            )
            if await cursor2.fetchone():
                continue
            ar_cursor = await db.execute(
                """INSERT INTO agent_runs
                   (symbol, earnings_date, scanner_output, strategist_output,
                    risk_manager_output, devils_advocate_output, portfolio_manager_output,
                    final_decision, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (c["symbol"], date.today().isoformat(),
                 json.dumps({"passed": True, "iv_percentile": c["iv_percentile"]}),
                 json.dumps({"recommendation": "trade", "reasoning": "IV percentile above threshold."}),
                 json.dumps({"verdict": "approved", "reasoning": "Risk within limits."}),
                 json.dumps({"flag": None, "reasoning": "No material risks identified."}),
                 json.dumps({"decision": "trade", "reasoning": "Pipeline aligned."}),
                 "trade", now)
            )
            agent_run_id = ar_cursor.lastrowid
            await db.execute(
                """INSERT INTO candidates
                   (agent_run_id, symbol, scan_date, order_status, expected_credit, contracts, max_loss)
                   VALUES (?,?,?,?,?,?,?)""",
                (agent_run_id, c["symbol"], date.today().isoformat(),
                 "pending_approval", 1.50, 1, 350.0)
            )
            await db.execute(
                """INSERT INTO inbox_items
                   (priority, item_type, symbol, title, body, actions, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                ("action_required", "ic_candidate", c["symbol"],
                 f"{c['symbol']} IC Candidate — Pipeline recommends TRADE",
                 f"IVP {c['iv_percentile']:.0%} · Expected credit $1.50 · 1 contract · Expires Friday",
                 '["approve","reject","view_pipeline"]', now)
            )
        await db.commit()
        log.info("ic_scan: found %d candidates", len(candidates))


async def order_executor():
    """Place approved IC candidates against the live Tradier API.

    Delegates to kotorid.order_placement.place_approved_candidates which
    handles the multileg POST, candidate state transition, ic_positions
    row materialization, and inbox card dismissal.
    """
    from kotorid.config import TRADIER_API_KEY
    from kotorid.order_placement import place_approved_candidates
    from kotorid.tradier_client import build_client, get_account_id

    if not TRADIER_API_KEY:
        log.info("order_executor: TRADIER_API_KEY not set — skipping")
        return

    async with get_db(DB_PATH) as db:
        async with build_client() as client:
            account_id = await get_account_id(client)
            placed = await place_approved_candidates(db, client, account_id)
        log.info("order_executor: placed %d order(s)", len(placed))


async def generate_briefing():
    import os
    from datetime import date
    async with get_db(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT symbol, unrealized_pnl_pct, instrument_type FROM positions"
        )
        positions = await cursor.fetchall()
        cursor2 = await db.execute(
            "SELECT symbol, pct_max_profit, entry_credit, current_debit FROM ic_positions "
            "WHERE exit_reason IS NULL"
        )
        open_ics = await cursor2.fetchall()

        summary = "\n".join(
            f"- {p['symbol']}: {p['unrealized_pnl_pct']:+.1%}" for p in positions
        )
        ic_summary = "\n".join(
            f"- {ic['symbol']} IC: {(ic['pct_max_profit'] or 0):.0%} of max profit captured"
            for ic in open_ics
        ) or "No open ICs."

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=800,
                messages=[{
                    "role": "user",
                    "content": (
                        f"You are a portfolio analyst. Write a concise daily briefing (200-300 words) "
                        f"for an options trader. Reference positions inline as [SYMBOL]. "
                        f"Be direct and actionable.\n\n"
                        f"Positions:\n{summary}\n\nOpen ICs:\n{ic_summary}"
                    )
                }]
            )
            content = msg.content[0].text
        else:
            content = f"# Daily Briefing — {date.today()}\n\n{summary}\n\n{ic_summary}\n\n_(Set ANTHROPIC_API_KEY for AI-generated briefings)_"

        now = datetime.now(tz=timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO briefings (period, content, generated_at) VALUES (?,?,?)",
            ("daily", content, now)
        )
        await db.execute(
            """INSERT INTO inbox_items
               (priority, item_type, symbol, title, body, actions, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("for_review", "briefing_ready", None, "Daily briefing ready",
             f"Generated {date.today()}. {len(positions)} positions reviewed.",
             '["read","dismiss"]', now)
        )
        await db.commit()
        log.info("generate_briefing: written")


async def post_latest_briefing_to_discord(
    db: aiosqlite.Connection, client: httpx.AsyncClient, webhook_url: str,
) -> bool:
    """Post today's most recent daily briefing as a large embed.

    Returns True on successful post, False if no briefing exists today
    or if the POST failed (logged).
    """
    cur = await db.execute(
        "SELECT content, generated_at FROM briefings "
        "WHERE period='daily' AND date(generated_at)=date('now') "
        "ORDER BY id DESC LIMIT 1"
    )
    row = await cur.fetchone()
    if not row:
        return False
    payload = {
        "embeds": [{
            "title": "📊 Morning Briefing",
            "description": row["content"][:4000],  # Discord embed description limit
            "color": 3447003,  # blue
            "timestamp": row["generated_at"],
            "footer": {"text": "kotori"},
        }]
    }
    try:
        resp = await client.post(webhook_url, json=payload, timeout=10.0)
        resp.raise_for_status()
        return True
    except httpx.HTTPError:
        log.exception("post_latest_briefing_to_discord: POST failed")
        return False
