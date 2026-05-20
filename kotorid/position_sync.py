"""Sync Tradier positions into the local SQLite positions table.

Strategy: Tradier is authoritative for what positions exist, so we DELETE
the entire positions table and re-INSERT every sync. This is simpler than
diffing and avoids stale rows for closed positions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging

import aiosqlite
import httpx

from kotorid.tradier_client import (
    get_positions,
    get_quotes,
    parse_occ_symbol,
)

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _pick_price(quote: dict | None) -> float:
    """Pick a usable current price from a Tradier quote dict.

    Prefers `last`, falls back to `bid`, then `ask`, then 0.0.
    """
    if not quote:
        return 0.0
    for key in ("last", "bid", "ask"):
        v = quote.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


async def sync_positions(
    db: aiosqlite.Connection,
    client: httpx.AsyncClient,
    account_id: str,
) -> int:
    """Pull positions from Tradier, normalize, upsert into positions table.

    Returns the number of positions synced.
    """
    raw_positions = await get_positions(client, account_id)

    # Collect unique symbols to quote. For options we still quote by the
    # OCC symbol — Tradier accepts both stock tickers and OCC option symbols
    # on /markets/quotes.
    symbols = sorted({p["symbol"] for p in raw_positions if p.get("symbol")})
    quotes = await get_quotes(client, symbols) if symbols else {}

    now = _now()
    rows = []
    for p in raw_positions:
        symbol = p.get("symbol")
        if not symbol:
            continue
        try:
            quantity = float(p.get("quantity", 0) or 0)
            cost_basis = float(p.get("cost_basis", 0) or 0)
        except (TypeError, ValueError):
            continue

        avg_cost = (cost_basis / quantity) if quantity else 0.0
        current_price = _pick_price(quotes.get(symbol))
        market_value = quantity * current_price
        unrealized_pnl = market_value - cost_basis
        unrealized_pnl_pct = (
            (unrealized_pnl / cost_basis) if cost_basis else 0.0
        )

        occ = parse_occ_symbol(symbol)
        if occ:
            instrument_type = "option"
            underlying = occ["underlying"]
            expiry = occ["expiry"]
            strike = occ["strike"]
            put_call = occ["put_call"]
        else:
            instrument_type = "stock"
            underlying = None
            expiry = None
            strike = None
            put_call = None

        rows.append(
            (
                symbol,
                quantity,
                avg_cost,
                current_price,
                market_value,
                unrealized_pnl,
                unrealized_pnl_pct,
                instrument_type,
                underlying,
                expiry,
                strike,
                put_call,
                now,
            )
        )

    # Full replace — Tradier is authoritative.
    await db.execute("DELETE FROM positions")
    if rows:
        await db.executemany(
            """INSERT INTO positions
               (symbol, quantity, avg_cost, current_price, market_value,
                unrealized_pnl, unrealized_pnl_pct, instrument_type,
                underlying, expiry, strike, put_call, last_updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    await db.commit()
    log.info("position_sync: %d positions synced", len(rows))
    return len(rows)
