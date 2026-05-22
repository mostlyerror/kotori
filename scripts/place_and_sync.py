"""Place a sandbox order, run a sync, and dump the positions table.

Test scaffolding — not part of the kotorid package. Hits the real Tradier
sandbox API. Hardcoded to 1 share of SPY (market, day) for now; tweak the
constants below to vary.
"""
from __future__ import annotations

import asyncio
import json

from dotenv import load_dotenv

load_dotenv()

from kotorid.config import DB_PATH, TRADIER_ACCOUNT_ID
from kotorid.db import get_db, init_db
from kotorid.position_sync import sync_positions
from kotorid.tradier_client import build_client

SYMBOL = "SPY"
QUANTITY = 1


async def place_equity_order(client, account_id: str, symbol: str, quantity: int) -> dict:
    """POST a market-day equity buy order to the Tradier sandbox.

    Tradier order endpoints use application/x-www-form-urlencoded, not JSON.
    """
    resp = await client.post(
        f"/accounts/{account_id}/orders",
        data={
            "class": "equity",
            "symbol": symbol,
            "side": "buy",
            "quantity": str(quantity),
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
        order_resp = await place_equity_order(client, TRADIER_ACCOUNT_ID, SYMBOL, QUANTITY)
        print("--- order response ---")
        print(json.dumps(order_resp, indent=2))

        async with get_db(DB_PATH) as db:
            await init_db(db)
            count = await sync_positions(db, client, TRADIER_ACCOUNT_ID)
            print(f"\n--- sync_positions wrote {count} row(s) ---\n")

            cursor = await db.execute(
                """SELECT symbol, quantity, avg_cost, current_price, market_value,
                          unrealized_pnl, unrealized_pnl_pct, instrument_type, last_updated
                   FROM positions"""
            )
            rows = await cursor.fetchall()
            print("--- positions table ---")
            for r in rows:
                print(dict(r))


if __name__ == "__main__":
    asyncio.run(main())
