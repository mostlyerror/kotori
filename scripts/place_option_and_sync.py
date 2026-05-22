"""Place a sandbox option order, run a sync, and dump the positions table.

Test scaffolding — not part of the kotorid package. Picks a near-ATM SPY
call expiring at the first non-0DTE Friday, buys 1 contract, then syncs
so we can confirm the OCC parser + option branch of position_sync.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from kotorid.config import DB_PATH, TRADIER_ACCOUNT_ID
from kotorid.db import get_db, init_db
from kotorid.position_sync import sync_positions
from kotorid.tradier_client import build_client

UNDERLYING = "SPY"


async def pick_contract(client, underlying: str) -> dict:
    """Return a near-ATM call expiring at the first Friday >2 days out."""
    today = date.today()
    resp = await client.get(
        "/markets/options/expirations", params={"symbol": underlying}
    )
    expirations = resp.json().get("expirations", {}).get("date", []) or []
    if isinstance(expirations, str):
        expirations = [expirations]

    target_expiry = None
    for d in expirations:
        d_obj = date.fromisoformat(d)
        if d_obj - today >= timedelta(days=3) and d_obj.weekday() == 4:
            target_expiry = d
            break
    if not target_expiry:
        raise RuntimeError(f"no suitable expiry in: {expirations[:10]}")

    quote_resp = await client.get(
        "/markets/quotes", params={"symbols": underlying}
    )
    quote = quote_resp.json().get("quotes", {}).get("quote", {})
    spot = float(quote.get("last") or quote.get("bid") or 0)
    if not spot:
        raise RuntimeError("no spot price for underlying")

    chain_resp = await client.get(
        "/markets/options/chains",
        params={"symbol": underlying, "expiration": target_expiry},
    )
    chain = chain_resp.json().get("options", {}).get("option", []) or []
    calls = [o for o in chain if o.get("option_type") == "call"]
    if not calls:
        raise RuntimeError(f"no calls in chain for {target_expiry}")

    best = min(calls, key=lambda o: abs(float(o["strike"]) - spot))
    print(f"--- selected contract ---")
    print(f"  expiry: {target_expiry}  spot: ${spot:.2f}  strike: ${best['strike']}")
    print(f"  occ: {best['symbol']}  bid: {best.get('bid')}  ask: {best.get('ask')}")
    return best


async def place_option_order(client, account_id: str, underlying: str, occ: str) -> dict:
    """POST a market-day buy_to_open option order to the Tradier sandbox."""
    resp = await client.post(
        f"/accounts/{account_id}/orders",
        data={
            "class": "option",
            "symbol": underlying,
            "option_symbol": occ,
            "side": "buy_to_open",
            "quantity": "1",
            "type": "market",
            "duration": "day",
        },
    )
    resp.raise_for_status()
    return resp.json()


async def main() -> None:
    if not TRADIER_ACCOUNT_ID:
        raise SystemExit("TRADIER_ACCOUNT_ID not set in .env")

    async with build_client() as client:
        contract = await pick_contract(client, UNDERLYING)
        order_resp = await place_option_order(
            client, TRADIER_ACCOUNT_ID, UNDERLYING, contract["symbol"]
        )
        print("\n--- order response ---")
        print(json.dumps(order_resp, indent=2))

        async with get_db(DB_PATH) as db:
            await init_db(db)
            count = await sync_positions(db, client, TRADIER_ACCOUNT_ID)
            print(f"\n--- sync_positions wrote {count} row(s) ---\n")

            cursor = await db.execute(
                """SELECT symbol, quantity, avg_cost, current_price, market_value,
                          unrealized_pnl, instrument_type, underlying, expiry,
                          strike, put_call, last_updated
                   FROM positions
                   ORDER BY instrument_type, symbol"""
            )
            rows = await cursor.fetchall()
            print("--- positions table ---")
            for r in rows:
                print(dict(r))


if __name__ == "__main__":
    asyncio.run(main())
