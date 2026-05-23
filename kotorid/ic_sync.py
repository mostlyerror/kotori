"""Refresh ic_positions state from live Tradier quotes.

For each open IC (exit_reason IS NULL), reconstructs the 4 OCC option
symbols from the stored strikes/expiry, fetches current quotes, and
updates current_debit + pct_max_profit. position_monitor consumes those
fields to decide whether to fire exit triggers — without this producer,
the IC monitoring pipeline sits idle.

The debit is computed using mid prices (bid+ask)/2 for each leg, then
combined as `(mid_SC + mid_SP) - (mid_LC + mid_LP)`. Mid pricing
reflects fair value and matches the convention encoded in
position_monitor.compute_exit_debit. Conservative bid/ask pricing would
overstate the closing cost (worst case if you cross the spread on every
leg), which would make profit-target / stop-loss exits fire incorrectly.
"""
from __future__ import annotations

import logging
from datetime import date

import aiosqlite
import httpx

from kotorid.alerts_lib import create_alert
from kotorid.position_monitor import compute_exit_debit
from kotorid.tradier_client import format_occ_symbol, get_quotes

log = logging.getLogger(__name__)


def _leg_quote(quote: dict | None) -> tuple[float, float] | None:
    """Extract (bid, ask) as floats; return None if the quote isn't usable.

    Rejects three failure modes that produce numerically valid but
    semantically garbage data:

    1. Missing field — bid or ask is null/absent
    2. Both-zero — bid=0 AND ask=0 simultaneously (markets closed or
       pre-market; Tradier returns this rather than null on some days)
    3. Crossed — bid > ask (stale or corrupt feed)

    A leg with bid=0 but ask>0 is *kept* — that's a legitimate "no
    resting bid, real ask" state for deep-OTM penny options. Mid pricing
    still produces a sensible half-penny value there.
    """
    if not quote:
        return None
    bid = quote.get("bid")
    ask = quote.get("ask")
    if bid is None or ask is None:
        return None
    try:
        bid_f, ask_f = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if bid_f == 0 and ask_f == 0:
        return None
    if bid_f > ask_f:
        return None
    return bid_f, ask_f


def _underlying_price(quote: dict | None) -> float | None:
    """Extract a usable underlying price from a Tradier stock quote.

    Prefers ``last`` (the trade print) over ``bid``/``ask`` so we measure
    distance-to-strike against where the market actually is, not where it
    *could* trade. Falls back to bid/ask for after-hours rows where last
    may be stale.
    """
    if not quote:
        return None
    for k in ("last", "bid", "ask"):
        v = quote.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


async def _maybe_fire_short_strike_threatened(
    db: aiosqlite.Connection,
    ic: aiosqlite.Row,
    quotes: dict[str, dict],
    debit: float,
) -> None:
    """Emit short_strike_threatened if underlying is within 1% of a short.

    Deduped once-per-day via ``ic_positions.short_strike_warned_at``. We
    cap the alert frequency because the underlying can hover near a strike
    for hours and we don't want a notification storm — one well-formed
    Discord ping per day per IC is enough to get the trader's eyes on it.
    """
    today_iso = date.today().isoformat()

    warned_cur = await db.execute(
        "SELECT short_strike_warned_at FROM ic_positions WHERE id=?",
        (ic["id"],),
    )
    warned_row = await warned_cur.fetchone()
    if warned_row and warned_row[0] == today_iso:
        return

    underlying_price = _underlying_price(quotes.get(ic["symbol"]))
    if underlying_price is None:
        return

    short_put = float(ic["short_put"])
    short_call = float(ic["short_call"])
    threatened_side: str | None = None
    threatened_strike: float | None = None
    if abs(underlying_price - short_put) / short_put <= 0.01:
        threatened_side = "put"
        threatened_strike = short_put
    elif abs(underlying_price - short_call) / short_call <= 0.01:
        threatened_side = "call"
        threatened_strike = short_call

    if threatened_side is None:
        return

    distance_pct = (underlying_price - threatened_strike) / threatened_strike
    await create_alert(
        db,
        alert_type="short_strike_threatened",
        symbol=ic["symbol"],
        headline=f"Short Strike Threatened — {ic['symbol']}",
        body_lines=[
            f"{ic['symbol']} at ${underlying_price:.2f}, "
            f"{distance_pct:+.2%} from short {threatened_side} {threatened_strike:.0f}.",
            f"Current debit ${debit:.2f}; if the strike breaches, the IC may stop out.",
        ],
        fields={
            "underlying_price": underlying_price,
            "short_strike": threatened_strike,
            "side": threatened_side,
            "distance_pct": distance_pct,
            "current_debit": debit,
        },
    )
    await db.execute(
        "UPDATE ic_positions SET short_strike_warned_at=? WHERE id=?",
        (today_iso, ic["id"]),
    )


async def refresh_ic_state(
    db: aiosqlite.Connection, client: httpx.AsyncClient
) -> int:
    """Update current_debit / pct_max_profit for every open IC.

    Returns the number of ICs refreshed (i.e. quotes were available for
    all 4 legs). ICs with any missing leg quote are skipped without
    error so a single bad symbol doesn't poison the whole pipeline.
    """
    cursor = await db.execute(
        """SELECT id, symbol, expiry, short_call, long_call, short_put, long_put,
                  entry_credit, contracts
           FROM ic_positions WHERE exit_reason IS NULL"""
    )
    ics = await cursor.fetchall()
    if not ics:
        return 0

    # Build the union of OCC symbols we need so one quote call covers everything.
    leg_keys = ("short_call", "long_call", "short_put", "long_put")
    leg_pc = {"short_call": "C", "long_call": "C", "short_put": "P", "long_put": "P"}
    occ_for: dict[int, dict[str, str]] = {}
    all_symbols: set[str] = set()
    for ic in ics:
        per_ic = {}
        for k in leg_keys:
            sym = format_occ_symbol(ic["symbol"], ic["expiry"], ic[k], leg_pc[k])
            per_ic[k] = sym
            all_symbols.add(sym)
        occ_for[ic["id"]] = per_ic
        # Include the underlying stock symbol so short_strike_threatened can
        # compare it against the short strikes without a second HTTP round-trip.
        all_symbols.add(ic["symbol"])

    quotes = await get_quotes(client, sorted(all_symbols))

    refreshed = 0
    for ic in ics:
        legs = occ_for[ic["id"]]
        sc = _leg_quote(quotes.get(legs["short_call"]))
        lc = _leg_quote(quotes.get(legs["long_call"]))
        sp = _leg_quote(quotes.get(legs["short_put"]))
        lp = _leg_quote(quotes.get(legs["long_put"]))
        if None in (sc, lc, sp, lp):
            log.warning(
                "refresh_ic_state: missing leg quote for IC id=%s symbol=%s; skipping",
                ic["id"], ic["symbol"],
            )
            continue

        debit = compute_exit_debit(
            sc_bid=sc[0], sc_ask=sc[1],
            sp_bid=sp[0], sp_ask=sp[1],
            lc_bid=lc[0], lc_ask=lc[1],
            lp_bid=lp[0], lp_ask=lp[1],
        )
        entry_credit = float(ic["entry_credit"]) if ic["entry_credit"] else 0.0
        pct_max_profit = (
            (entry_credit - debit) / entry_credit if entry_credit else None
        )
        await db.execute(
            "UPDATE ic_positions SET current_debit=?, pct_max_profit=? WHERE id=?",
            (debit, pct_max_profit, ic["id"]),
        )
        refreshed += 1

        await _maybe_fire_short_strike_threatened(db, ic, quotes, debit)

    await db.commit()
    log.info("refresh_ic_state: refreshed %d IC(s)", refreshed)
    return refreshed
