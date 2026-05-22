"""Place a sandbox iron condor, sync positions, populate ic_positions.

Test scaffolding — not part of the kotorid package. Hits the real
Tradier sandbox API to place a 4-leg multileg order, then materializes
the corresponding ic_positions row from the entry parameters so the
TUI's IC code path can render against live data.

Hardcoded to SPY 5/29 with $760/$765 call wing and $735/$730 put wing,
1 contract. Tweak the constants to vary.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv("/Users/benjaminpoon/dev/kotori/.env")

from kotorid.config import DB_PATH, TRADIER_ACCOUNT_ID
from kotorid.db import get_db, init_db
from kotorid.position_sync import sync_positions
from kotorid.tradier_client import build_client

UNDERLYING = "SPY"
EXPIRY = "2026-05-29"
EXPIRY_OCC = "260529"  # YYMMDD form used in OCC symbols

SHORT_CALL = 760
LONG_CALL = 765
SHORT_PUT = 735
LONG_PUT = 730
CONTRACTS = 1


def occ_symbol(underlying: str, expiry_yymmdd: str, strike: int, put_call: str) -> str:
    """Build an OCC option symbol from parts (strike in dollars, no decimal)."""
    strike_padded = f"{strike * 1000:08d}"
    return f"{underlying}{expiry_yymmdd}{put_call}{strike_padded}"


async def place_iron_condor(client, account_id: str) -> dict:
    """POST a 4-leg multileg market order for an iron condor.

    Tradier multileg orders use indexed params: side[0], quantity[0],
    option_symbol[0] for leg 0, and so on. Order class = multileg.
    """
    legs = [
        # buy_to_open the protective puts/calls first (longs), then sell_to_open
        # the shorts. Order doesn't affect the broker; legs all fill together.
        ("buy_to_open", LONG_PUT, "P"),
        ("sell_to_open", SHORT_PUT, "P"),
        ("sell_to_open", SHORT_CALL, "C"),
        ("buy_to_open", LONG_CALL, "C"),
    ]
    data = {
        "class": "multileg",
        "symbol": UNDERLYING,
        "type": "market",
        "duration": "day",
    }
    for i, (side, strike, pc) in enumerate(legs):
        data[f"side[{i}]"] = side
        data[f"quantity[{i}]"] = str(CONTRACTS)
        data[f"option_symbol[{i}]"] = occ_symbol(UNDERLYING, EXPIRY_OCC, strike, pc)

    resp = await client.post(f"/accounts/{account_id}/orders", data=data)
    resp.raise_for_status()
    return resp.json()


async def insert_ic_row(db, entry_credit: float) -> None:
    """Materialize an ic_positions row mirroring the placed IC.

    Entry credit and max_loss are stored per-share (per the existing
    schema convention seen in mock_data and position_monitor).
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    spread_width = LONG_CALL - SHORT_CALL  # also == SHORT_PUT - LONG_PUT
    max_loss = (spread_width - entry_credit) * 100 * CONTRACTS
    await db.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
            spread_width, entry_credit, contracts, max_loss, regime_at_entry)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            UNDERLYING, now[:10], EXPIRY,
            float(SHORT_CALL), float(LONG_CALL), float(SHORT_PUT), float(LONG_PUT),
            float(spread_width), entry_credit, CONTRACTS, max_loss, "normal",
        ),
    )
    await db.commit()


async def estimate_credit(client) -> float:
    """Estimate the entry credit from current bid/ask quotes (per share)."""
    symbols = [
        occ_symbol(UNDERLYING, EXPIRY_OCC, SHORT_CALL, "C"),
        occ_symbol(UNDERLYING, EXPIRY_OCC, LONG_CALL, "C"),
        occ_symbol(UNDERLYING, EXPIRY_OCC, SHORT_PUT, "P"),
        occ_symbol(UNDERLYING, EXPIRY_OCC, LONG_PUT, "P"),
    ]
    resp = await client.get("/markets/quotes", params={"symbols": ",".join(symbols)})
    quotes = {q["symbol"]: q for q in resp.json()["quotes"]["quote"]}
    sc_bid = float(quotes[symbols[0]]["bid"])
    lc_ask = float(quotes[symbols[1]]["ask"])
    sp_bid = float(quotes[symbols[2]]["bid"])
    lp_ask = float(quotes[symbols[3]]["ask"])
    # Credit: collect bids on shorts, pay asks on longs.
    return sc_bid - lc_ask + sp_bid - lp_ask


async def main() -> None:
    if not TRADIER_ACCOUNT_ID:
        raise SystemExit("TRADIER_ACCOUNT_ID not set in .env")

    async with build_client() as client:
        credit = await estimate_credit(client)
        spread_width = LONG_CALL - SHORT_CALL
        max_loss = (spread_width - credit) * 100 * CONTRACTS
        print(f"--- iron condor plan ---")
        print(f"  expiry: {EXPIRY}")
        print(f"  short call {SHORT_CALL} / long call {LONG_CALL}")
        print(f"  short put  {SHORT_PUT} / long put  {LONG_PUT}")
        print(f"  spread width: ${spread_width}")
        print(f"  estimated credit: ${credit:.2f}/share = ${credit*100:.0f}/contract")
        print(f"  max loss: ${max_loss:.0f}/contract")
        print()

        order_resp = await place_iron_condor(client, TRADIER_ACCOUNT_ID)
        print("--- order response ---")
        print(json.dumps(order_resp, indent=2))
        print()

        async with get_db(DB_PATH) as db:
            await init_db(db)
            count = await sync_positions(db, client, TRADIER_ACCOUNT_ID)
            print(f"--- sync_positions wrote {count} row(s) ---")

            await insert_ic_row(db, credit)
            print(f"--- ic_positions row inserted ---\n")

            cursor = await db.execute(
                """SELECT symbol, quantity, avg_cost, current_price, market_value,
                          unrealized_pnl, instrument_type, strike, put_call
                   FROM positions ORDER BY instrument_type, strike, put_call, symbol"""
            )
            print("--- positions table ---")
            for r in await cursor.fetchall():
                print(dict(r))

            cursor = await db.execute(
                """SELECT symbol, expiry, short_call, long_call, short_put, long_put,
                          spread_width, entry_credit, contracts, max_loss
                   FROM ic_positions WHERE exit_reason IS NULL"""
            )
            print("\n--- ic_positions table ---")
            for r in await cursor.fetchall():
                print(dict(r))


if __name__ == "__main__":
    asyncio.run(main())
