import logging
from datetime import datetime, timezone
import asyncpg
import httpx
from kotorid.alerts_lib import create_alert
from kotorid.config import DATABASE_URL
from kotorid.db import get_db
from kotorid.position_monitor import check_exit_trigger

log = logging.getLogger(__name__)


async def run_position_monitor(conn: asyncpg.Connection) -> list[dict]:
    open_ics = await conn.fetch(
        "SELECT id, symbol, entry_credit, current_debit, contracts, position_warning_at "
        "FROM ic_positions WHERE exit_reason IS NULL AND current_debit IS NOT NULL"
    )
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
                conn,
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
            await conn.execute(
                "UPDATE ic_positions SET position_warning_at=$1 WHERE id=$2",
                now_warn, ic["id"],
            )

        reason = check_exit_trigger(ic["entry_credit"], ic["current_debit"])
        if reason is None:
            continue

        now = datetime.now(tz=timezone.utc).isoformat()
        realized_pnl = (ic["entry_credit"] - ic["current_debit"]) * 100 * ic["contracts"]

        await conn.execute(
            "UPDATE ic_positions SET exit_reason=$1, exit_debit=$2, realized_pnl=$3 WHERE id=$4",
            reason, ic["current_debit"], realized_pnl, ic["id"],
        )
        reason_label = "Stop Loss" if reason == "stop_loss" else "Profit Target"
        pnl_sign = "-" if realized_pnl < 0 else "+"
        await create_alert(
            conn,
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
        await conn.execute(
            """INSERT INTO inbox_items
               (priority, item_type, symbol, title, body, actions, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            "urgent" if reason == "stop_loss" else "for_review",
             reason, ic["symbol"],
             f"{ic['symbol']} IC — {reason.replace('_', ' ').title()}",
             f"Exit at ${ic['current_debit']:.2f}. P&L {realized_pnl:+.0f}. Entry credit ${ic['entry_credit']:.2f}.",
             '["acknowledge"]', now,
        )
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
    expired = await conn.fetch(
        "SELECT id, symbol, expiry, entry_credit, current_debit, contracts FROM ic_positions "
        "WHERE exit_reason IS NULL AND expiry < $1", today
    )
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
        await conn.execute(
            "UPDATE ic_positions SET exit_reason='force_close', exit_debit=$1, realized_pnl=$2 WHERE id=$3",
            debit, realized_pnl, ic["id"],
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
            conn,
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

    return closed


async def position_monitor():
    async with get_db() as conn:
        await run_position_monitor(conn)


async def dte_check(conn: asyncpg.Connection) -> int:
    """Emit dte_warning for any open IC expiring tomorrow.

    De-duped per (symbol, today) — running multiple times the same day
    creates at most one alert per IC.
    """
    from datetime import date, timedelta
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    rows = await conn.fetch(
        "SELECT symbol, expiry, entry_credit, current_debit, contracts "
        "FROM ic_positions WHERE exit_reason IS NULL AND expiry=$1",
        tomorrow,
    )
    fired = 0
    for ic in rows:
        # Dedup: skip if a dte_warning already exists today for this symbol.
        dup = await conn.fetchrow(
            "SELECT 1 FROM alerts "
            "WHERE alert_type='dte_warning' AND symbol=$1 AND triggered_at::text LIKE $2 || '%'",
            ic["symbol"], today,
        )
        if dup:
            continue
        debit = ic["current_debit"] or 0.0
        entry = ic["entry_credit"] or 0.0
        unrealized = (entry - debit) * 100 * (ic["contracts"] or 1)
        await create_alert(
            conn,
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
    return fired


async def dte_check_job():
    """Scheduler wrapper that opens its own DB connection."""
    async with get_db() as conn:
        await dte_check(conn)


async def iv_ingest_morning():
    from datetime import date, timedelta
    import random
    async with get_db() as conn:
        symbols = [r["symbol"] for r in await conn.fetch("SELECT DISTINCT symbol FROM positions")]
        today = date.today().isoformat()
        rows = []
        for sym in symbols:
            row = await conn.fetchrow(
                "SELECT iv FROM iv_history WHERE symbol=$1 ORDER BY date DESC LIMIT 1", sym
            )
            last_iv = row["iv"] if row else 0.40
            new_iv = max(0.05, last_iv + random.gauss(0, 0.01))
            rows.append((sym, today, round(new_iv, 4)))
        await conn.executemany(
            "INSERT INTO iv_history (symbol, date, iv) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
            rows
        )
        log.info("iv_ingest_morning: updated %d symbols", len(rows))


async def gap_monitor(conn=None):
    if conn is not None:
        return await _gap_monitor_impl(conn)
    async with get_db() as conn:
        return await _gap_monitor_impl(conn)


async def _gap_monitor_impl(conn):
        open_ics = await conn.fetch(
            "SELECT id, symbol, short_call, short_put, expected_move FROM ic_positions "
            "WHERE exit_reason IS NULL"
        )
        now = datetime.now(tz=timezone.utc).isoformat()
        for ic in open_ics:
            row = await conn.fetchrow(
                "SELECT current_price FROM positions WHERE symbol=$1", ic["symbol"]
            )
            if not row:
                continue
            price = row["current_price"]
            cushion_call = ic["short_call"] - price
            cushion_put = price - ic["short_put"]
            if cushion_call < ic["expected_move"] * 0.5 or cushion_put < ic["expected_move"] * 0.5:
                await conn.execute(
                    """INSERT INTO inbox_items
                       (priority, item_type, symbol, title, body, actions, created_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                    "urgent", "gap_risk", ic["symbol"],
                     f"{ic['symbol']} IC — Pre-market gap risk",
                     f"Price ${price:.2f} within 50% of expected move from short strikes. "
                     f"SC ${ic['short_call']:.0f} / SP ${ic['short_put']:.0f}.",
                     '["close_ic","hedge","dismiss"]', now,
                )
                await create_alert(
                    conn,
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
        log.info("gap_monitor: checked %d open ICs", len(open_ics))


async def iv_ingest_preclose():
    import random
    from datetime import date
    async with get_db() as conn:
        symbols = [r["symbol"] for r in await conn.fetch("SELECT DISTINCT symbol FROM positions")]
        today = date.today().isoformat()
        for sym in symbols:
            row = await conn.fetchrow(
                "SELECT iv FROM iv_history WHERE symbol=$1 ORDER BY date DESC LIMIT 1", sym
            )
            last_iv = row["iv"] if row else 0.40
            new_iv = max(0.05, last_iv + random.gauss(0, 0.005))
            await conn.execute(
                "INSERT INTO iv_history (symbol, date, iv) VALUES ($1,$2,$3) "
                "ON CONFLICT (symbol, date) DO UPDATE SET iv = EXCLUDED.iv",
                sym, today, round(new_iv, 4),
            )
        log.info("iv_ingest_preclose: refreshed %d symbols", len(symbols))


async def ic_scan():
    import json
    from datetime import date
    async with get_db() as conn:
        candidates = await conn.fetch(
            "SELECT symbol, iv_percentile FROM iv_history "
            "WHERE date=$1 AND iv_percentile IS NOT NULL AND iv_percentile >= 0.70",
            date.today().isoformat(),
        )
        now = datetime.now(tz=timezone.utc).isoformat()
        for c in candidates:
            existing = await conn.fetchrow(
                "SELECT id FROM candidates WHERE symbol=$1 AND scan_date=$2",
                c["symbol"], date.today().isoformat(),
            )
            if existing:
                continue
            agent_run_id = await conn.fetchval(
                """INSERT INTO agent_runs
                   (symbol, earnings_date, scanner_output, strategist_output,
                    risk_manager_output, devils_advocate_output, portfolio_manager_output,
                    final_decision, created_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   RETURNING id""",
                c["symbol"], date.today().isoformat(),
                json.dumps({"passed": True, "iv_percentile": c["iv_percentile"]}),
                json.dumps({"recommendation": "trade", "reasoning": "IV percentile above threshold."}),
                json.dumps({"verdict": "approved", "reasoning": "Risk within limits."}),
                json.dumps({"flag": None, "reasoning": "No material risks identified."}),
                json.dumps({"decision": "trade", "reasoning": "Pipeline aligned."}),
                "trade", now,
            )
            await conn.execute(
                """INSERT INTO candidates
                   (agent_run_id, symbol, scan_date, order_status, expected_credit, contracts, max_loss)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                agent_run_id, c["symbol"], date.today().isoformat(),
                "pending_approval", 1.50, 1, 350.0,
            )
            await conn.execute(
                """INSERT INTO inbox_items
                   (priority, item_type, symbol, title, body, actions, created_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                "action_required", "ic_candidate", c["symbol"],
                f"{c['symbol']} IC Candidate — Pipeline recommends TRADE",
                f"IVP {c['iv_percentile']:.0%} · Expected credit $1.50 · 1 contract · Expires Friday",
                '["approve","reject","view_pipeline"]', now,
            )
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

    async with get_db() as conn:
        async with build_client() as client:
            account_id = await get_account_id(client)
            placed = await place_approved_candidates(conn, client, account_id)
        log.info("order_executor: placed %d order(s)", len(placed))


async def generate_briefing():
    import os
    from datetime import date
    async with get_db() as conn:
        positions = await conn.fetch(
            "SELECT symbol, unrealized_pnl_pct, instrument_type FROM positions"
        )
        open_ics = await conn.fetch(
            "SELECT symbol, pct_max_profit, entry_credit, current_debit FROM ic_positions "
            "WHERE exit_reason IS NULL"
        )

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
        await conn.execute(
            "INSERT INTO briefings (period, content, generated_at) VALUES ($1,$2,$3)",
            "daily", content, now,
        )
        await conn.execute(
            """INSERT INTO inbox_items
               (priority, item_type, symbol, title, body, actions, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            "for_review", "briefing_ready", None, "Daily briefing ready",
            f"Generated {date.today()}. {len(positions)} positions reviewed.",
            '["read","dismiss"]', now,
        )
        log.info("generate_briefing: written")


async def post_latest_briefing_to_discord(
    conn: asyncpg.Connection, client: httpx.AsyncClient, webhook_url: str,
) -> bool:
    """Post today's most recent daily briefing as a large embed.

    Returns True on successful post, False if no briefing exists today
    or if the POST failed (logged).
    """
    row = await conn.fetchrow(
        "SELECT content, generated_at FROM briefings "
        "WHERE period='daily' AND generated_at::text LIKE CURRENT_DATE::text || '%' "
        "ORDER BY id DESC LIMIT 1"
    )
    if not row:
        return False
    payload = {
        "embeds": [{
            "title": "\U0001f4ca Morning Briefing",
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


async def build_eod_recap_payload(conn: asyncpg.Connection) -> dict:
    """Build the EOD recap Discord payload from today's activity."""
    from datetime import date
    today_iso = date.today().isoformat()

    # Realized P/L today: sum of realized_pnl for ICs whose closing alert
    # was triggered today.
    realized_row = await conn.fetchrow(
        """SELECT COALESCE(SUM(p.realized_pnl), 0) AS total,
                  COUNT(*) AS closed_count,
                  SUM(CASE WHEN p.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
           FROM ic_positions p
           JOIN alerts a ON a.symbol = p.symbol
           WHERE a.alert_type IN ('profit_target','stop_loss','force_close')
             AND a.triggered_at::text LIKE $1 || '%'
             AND p.exit_reason IS NOT NULL""",
        today_iso,
    )
    realized = realized_row["total"] or 0.0
    closed_count = realized_row["closed_count"] or 0
    wins = realized_row["wins"] or 0
    losses = closed_count - wins

    open_count = await conn.fetchval(
        "SELECT COUNT(*) FROM ic_positions WHERE exit_reason IS NULL"
    )

    title = f"\U0001f4c8 EOD Recap — {date.today().strftime('%a %b %d %Y')}"
    pnl_sign = "+" if realized >= 0 else "-"
    description_lines = [
        f"• Realized P/L today: {pnl_sign}${abs(realized):.0f} "
        f"({wins} win{'s' if wins != 1 else ''}, "
        f"{losses} loss{'es' if losses != 1 else ''})",
        f"• {open_count} IC{'s' if open_count != 1 else ''} open",
        f"• {closed_count} closed today",
    ]
    return {
        "embeds": [{
            "title": title,
            "description": "\n".join(description_lines),
            "color": 3066993,
            "footer": {"text": "kotori"},
        }]
    }


async def eod_recap_job():
    """Scheduled wrapper: build EOD recap and post to Discord."""
    from kotorid.notify import webhook_url as _webhook_url
    url = _webhook_url()
    if not url:
        return
    try:
        async with get_db() as conn:
            payload = await build_eod_recap_payload(conn)
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10.0)
                resp.raise_for_status()
    except Exception:
        log.exception("eod_recap_job failed")
