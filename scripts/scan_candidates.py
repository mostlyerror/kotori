"""Run the IC candidate scanner against the live Tradier sandbox.

Hits /markets/options/expirations + /markets/options/chains?greeks=true
+ /markets/quotes for each symbol in the watchlist. Writes candidate
rows + inbox cards for symbols that pass the minimum-credit filter.

Usage:  scripts/scan_candidates.py [SYMBOL ...]

With no args, uses KOTORI_WATCHLIST (or the default 10 names). With
arguments, scans only those symbols.
"""
from __future__ import annotations

import asyncio
import json
import sys

from dotenv import load_dotenv

load_dotenv("/Users/benjaminpoon/dev/kotori/.env")

from kotorid.candidate_scan import get_watchlist, scan_candidates
from kotorid.config import DB_PATH
from kotorid.db import get_db, init_db
from kotorid.tradier_client import build_client


async def main() -> None:
    symbols = sys.argv[1:] if len(sys.argv) > 1 else get_watchlist()
    print(f"--- scanning {len(symbols)} symbols: {', '.join(symbols)} ---\n")

    async with build_client() as client:
        async with get_db(DB_PATH) as db:
            await init_db(db)
            written = await scan_candidates(db, client, symbols=symbols)

    print(f"\n--- wrote {len(written)} candidate(s) ---\n")
    for c in written:
        print(json.dumps(c, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
