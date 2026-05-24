"""Sync Tradier positions into the positions table.

Strategy: Tradier is authoritative for what positions exist, so we DELETE
the entire positions table and re-INSERT every sync. This is simpler than
diffing and avoids stale rows for closed positions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging

import asyncpg
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


def _multiplier(quote: dict | None) -> int:
    if not quote:
        return 1
    cs = quote.get("contract_size")
    try:
        return int(cs) if cs else 1
    except (TypeError, ValueError):
        return 1


async def sync_positions(
    conn: asyncpg.Connection,
    client: httpx.AsyncClient,
    account_id: str,
) -> int:
    raw_positions = await get_positions(client, account_id)

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

        quote = quotes.get(symbol)
        current_price = _pick_price(quote)
        multiplier = _multiplier(quote)

        notional_qty = quantity * multiplier
        avg_cost = (cost_basis / notional_qty) if notional_qty else 0.0
        market_value = notional_qty * current_price
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

    await conn.execute("DELETE FROM positions")
    if rows:
        await conn.executemany(
            """INSERT INTO positions
               (symbol, quantity, avg_cost, current_price, market_value,
                unrealized_pnl, unrealized_pnl_pct, instrument_type,
                underlying, expiry, strike, put_call, last_updated)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
            rows,
        )
    log.info("position_sync: %d positions synced", len(rows))
    return len(rows)
