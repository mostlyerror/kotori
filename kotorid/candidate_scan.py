"""Scan a watchlist for iron-condor candidates from live Tradier chains.

For each symbol in the watchlist:
  1. Find a target expiry in the configured DTE window (default 5-9 days)
  2. Pull the option chain with greeks=true to get per-strike delta
  3. Pick short strikes near the target delta (default 0.16 = ~16-delta)
  4. Pick wings $5 outside each short
  5. Compute mid-price credit and max_loss
  6. Filter by minimum credit / spread-width ratio
  7. Insert a candidate row + inbox card for user approval

Delta-based strike selection is more robust than fixed-distance because
it adapts to each symbol's IV regime: high-IV symbols get wider strikes
naturally (higher delta at greater distance), low-IV symbols tighter.
The user can edit constants below or pass overrides per call.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone

import asyncpg
import httpx

from kotorid.alerts_lib import create_alert

log = logging.getLogger(__name__)

DEFAULT_WATCHLIST = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "GOOGL"]
TARGET_SHORT_DELTA = 0.16  # ~84% probability of OTM at expiry
WING_WIDTH = 5             # dollars between short and long strike on each wing
MIN_DTE = 5
MAX_DTE = 9
MIN_CREDIT_RATIO = 0.20    # credit must be at least 20% of spread width


def get_watchlist() -> list[str]:
    """Read watchlist from KOTORI_WATCHLIST env (comma-separated) or default."""
    raw = os.environ.get("KOTORI_WATCHLIST", "").strip()
    if raw:
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    return DEFAULT_WATCHLIST


def _mid(leg: dict) -> float:
    """Mid price for a chain leg."""
    bid = leg.get("bid") or 0
    ask = leg.get("ask") or 0
    return (float(bid) + float(ask)) / 2


def select_strikes(
    chain: list[dict],
    target_delta: float = TARGET_SHORT_DELTA,
    wing_width: int = WING_WIDTH,
) -> dict | None:
    """Pick the 4 legs of a symmetric IC from a chain.

    Returns ``None`` if the chain lacks delta data or wing strikes aren't
    available. Otherwise returns ``{short_call, long_call, short_put, long_put}``,
    each value being the full leg dict from the chain.
    """
    calls = [
        o for o in chain
        if o.get("option_type") == "call"
        and o.get("greeks", {}).get("delta") is not None
    ]
    puts = [
        o for o in chain
        if o.get("option_type") == "put"
        and o.get("greeks", {}).get("delta") is not None
    ]
    if not calls or not puts:
        return None

    short_call = min(calls, key=lambda o: abs(o["greeks"]["delta"] - target_delta))
    long_call_strike = short_call["strike"] + wing_width
    long_call = next((o for o in calls if o["strike"] == long_call_strike), None)

    short_put = min(puts, key=lambda o: abs(o["greeks"]["delta"] + target_delta))
    long_put_strike = short_put["strike"] - wing_width
    long_put = next((o for o in puts if o["strike"] == long_put_strike), None)

    if long_call is None or long_put is None:
        return None
    return {
        "short_call": short_call,
        "long_call": long_call,
        "short_put": short_put,
        "long_put": long_put,
    }


def estimate_credit(legs: dict) -> float:
    """Net credit (per share) for selling the short legs and buying the longs."""
    sc, lc, sp, lp = legs["short_call"], legs["long_call"], legs["short_put"], legs["long_put"]
    return (_mid(sc) + _mid(sp)) - (_mid(lc) + _mid(lp))


async def _find_expiry(
    client: httpx.AsyncClient, symbol: str,
    min_dte: int = MIN_DTE, max_dte: int = MAX_DTE,
) -> str | None:
    """First expiry in [min_dte, max_dte] inclusive, or None."""
    resp = await client.get(
        "/markets/options/expirations", params={"symbol": symbol}
    )
    raw = resp.json().get("expirations", {}).get("date", []) or []
    if isinstance(raw, str):
        raw = [raw]
    today = date.today()
    for d in raw:
        try:
            dte = (date.fromisoformat(d) - today).days
        except ValueError:
            continue
        if min_dte <= dte <= max_dte:
            return d
    return None


async def _get_spot(client: httpx.AsyncClient, symbol: str) -> float | None:
    resp = await client.get("/markets/quotes", params={"symbols": symbol})
    quote = resp.json().get("quotes", {}).get("quote", {})
    if isinstance(quote, list):
        quote = quote[0] if quote else {}
    for key in ("last", "bid", "ask"):
        v = quote.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


async def scan_one_symbol(
    conn: asyncpg.Connection, client: httpx.AsyncClient, symbol: str,
) -> dict | None:
    """Scan a single symbol; return the candidate dict if one was written."""
    today_iso = date.today().isoformat()

    existing = await conn.fetchrow(
        "SELECT id FROM candidates WHERE symbol=$1 AND scan_date=$2",
        symbol, today_iso,
    )
    if existing:
        return None  # already scanned today

    expiry = await _find_expiry(client, symbol)
    if not expiry:
        log.info("candidate_scan: no expiry in [%d,%d] DTE for %s", MIN_DTE, MAX_DTE, symbol)
        return None

    spot = await _get_spot(client, symbol)
    if not spot:
        log.warning("candidate_scan: no spot quote for %s", symbol)
        return None

    chain_resp = await client.get(
        "/markets/options/chains",
        params={"symbol": symbol, "expiration": expiry, "greeks": "true"},
    )
    chain = chain_resp.json().get("options", {}).get("option", []) or []
    legs = select_strikes(chain)
    if not legs:
        log.info("candidate_scan: no valid strike layout for %s @ %s", symbol, expiry)
        return None

    credit = estimate_credit(legs)
    spread_width = legs["long_call"]["strike"] - legs["short_call"]["strike"]
    if credit <= 0:
        log.info("candidate_scan: skipping %s — inverted/zero credit (%.2f)", symbol, credit)
        return None
    if credit < spread_width * MIN_CREDIT_RATIO:
        log.info(
            "candidate_scan: skipping %s — credit %.2f below %.0f%% of spread width %.0f",
            symbol, credit, MIN_CREDIT_RATIO * 100, spread_width,
        )
        return None

    max_loss = (spread_width - credit) * 100  # per contract

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    agent_run_id = await conn.fetchval(
        """INSERT INTO agent_runs
           (symbol, earnings_date, scanner_output, strategist_output,
            risk_manager_output, devils_advocate_output, portfolio_manager_output,
            final_decision, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
        symbol, today_iso,
        json.dumps({
            "spot": spot, "expiry": expiry, "spread_width": spread_width,
            "short_call_delta": legs["short_call"]["greeks"]["delta"],
            "short_put_delta": legs["short_put"]["greeks"]["delta"],
            "short_call_iv": legs["short_call"]["greeks"].get("mid_iv"),
            "short_put_iv": legs["short_put"]["greeks"].get("mid_iv"),
        }),
        json.dumps({
            "recommendation": "manual_review",
            "reasoning": (
                f"Delta-screened ~{int(TARGET_SHORT_DELTA*100)}-delta short strikes; "
                f"${WING_WIDTH} wings; credit/width = {credit/spread_width:.0%}"
            ),
        }),
        json.dumps({"verdict": "pending", "reasoning": "No risk model yet (stub)"}),
        json.dumps({"flag": None, "reasoning": "No devils advocate yet (stub)"}),
        json.dumps({"decision": "pending", "reasoning": "Awaiting human approval"}),
        "pending", now_iso,
    )
    candidate_id = await conn.fetchval(
        """INSERT INTO candidates
           (agent_run_id, symbol, scan_date, order_status, expected_credit, contracts, max_loss,
            short_call, long_call, short_put, long_put)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id""",
        agent_run_id, symbol, today_iso, "pending_approval",
        round(credit, 2), 1, round(max_loss, 2),
        legs["short_call"]["strike"], legs["long_call"]["strike"],
        legs["short_put"]["strike"], legs["long_put"]["strike"],
    )
    await conn.execute(
        """INSERT INTO inbox_items
           (priority, item_type, symbol, title, body, actions, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7)""",
        "action_required", "ic_candidate", symbol,
        f"{symbol} IC Candidate — {expiry}",
        (
            f"SC{int(legs['short_call']['strike'])} / LC{int(legs['long_call']['strike'])} / "
            f"SP{int(legs['short_put']['strike'])} / LP{int(legs['long_put']['strike'])} · "
            f"Credit ${credit:.2f}/sh · Max loss ${max_loss:.0f}/contract · "
            f"Δ short call {legs['short_call']['greeks']['delta']:.2f} / "
            f"short put {legs['short_put']['greeks']['delta']:.2f}"
        ),
        json.dumps(["approve", "reject", "view_pipeline"]),
        now_iso,
    )
    return {
        "symbol": symbol, "expiry": expiry, "spot": spot,
        "short_call": legs["short_call"]["strike"], "long_call": legs["long_call"]["strike"],
        "short_put": legs["short_put"]["strike"], "long_put": legs["long_put"]["strike"],
        "credit": round(credit, 2), "max_loss": round(max_loss, 2),
        "agent_run_id": agent_run_id, "candidate_id": candidate_id,
    }


async def scan_candidates(
    conn: asyncpg.Connection, client: httpx.AsyncClient,
    symbols: list[str] | None = None,
) -> list[dict]:
    """Run scan_one_symbol for every symbol in the watchlist.

    Returns the list of candidate dicts that were actually written.
    Symbols that fail any filter (no expiry, no chain data, weak credit,
    duplicate-today) are skipped silently with a log line.
    """
    syms = symbols if symbols is not None else get_watchlist()
    written: list[dict] = []
    for sym in syms:
        try:
            result = await scan_one_symbol(conn, client, sym)
        except Exception:
            log.exception("candidate_scan: error scanning %s", sym)
            continue
        if result:
            written.append(result)
    if written:
        top = max(written, key=lambda c: c["credit"] / (c["max_loss"] / 100))
        body_lines = [
            f"{len(written)} candidate(s) ready for approval.",
            f"Top pick: {top['symbol']} {top['expiry']} — "
            f"credit ${top['credit']:.2f}, max loss ${top['max_loss']:.0f}.",
            f"Strikes: SC{int(top['short_call'])}/LC{int(top['long_call'])} "
            f"SP{int(top['short_put'])}/LP{int(top['long_put'])}.",
            "Approve in TUI or wait — auto-place fallback at 14:50 CT.",
        ]
        await create_alert(
            conn,
            alert_type="candidate_ready",
            symbol=top["symbol"],
            headline="Candidates Ready",
            body_lines=body_lines,
            fields={
                "count": len(written),
                "symbols": [c["symbol"] for c in written],
                "credit": top["credit"],
                "max_loss": top["max_loss"],
            },
        )
    log.info("candidate_scan: wrote %d candidate(s) from %d symbols", len(written), len(syms))
    return written
