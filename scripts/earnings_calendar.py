"""Refresh and display the earnings calendar.

Usage:
    python scripts/earnings_calendar.py              # refresh + display
    python scripts/earnings_calendar.py --no-refresh # display cached only
    python scripts/earnings_calendar.py --symbols AAPL,NVDA,TSLA
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

import asyncpg

from kotorid.candidate_scan import get_watchlist
from kotorid.config import DATABASE_URL
from kotorid.db import init_db
from kotorid.earnings import (
    EarningsEvent,
    get_upcoming_earnings,
    is_etf,
    refresh_earnings,
    symbols_in_earnings_window,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

EARNINGS_WINDOW_START = 3
EARNINGS_WINDOW_END = 14


def render_calendar(events: list[EarningsEvent], window_syms: set[str]) -> None:
    today = date.today()
    print(f"\n{'Symbol':8s} {'Date':>10s} {'Days':>5s} {'Window':>7s} {'EPS Est':>8s} {'Last EPS':>9s} {'Surprise':>9s}")
    print("─" * 62)

    for e in events:
        in_window = e.symbol in window_syms
        window_marker = "  ●" if in_window else ""
        eps_est = f"{e.eps_estimate:.2f}" if e.eps_estimate is not None else "—"
        last_eps = f"{e.reported_eps:.2f}" if e.reported_eps is not None else "—"
        surprise = f"{e.surprise_pct:+.1f}%" if e.surprise_pct is not None else "—"

        print(f"{e.symbol:8s} {e.earnings_date.strftime('%b %d'):>10s} {e.days_until:>5d} {window_marker:>7s} {eps_est:>8s} {last_eps:>9s} {surprise:>9s}")

    if not events:
        print("  No upcoming earnings in the next 60 days.")

    print()


async def main(args: argparse.Namespace) -> None:
    symbols = args.symbols.split(",") if args.symbols else get_watchlist()
    stock_syms = [s for s in symbols if not is_etf(s)]

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await init_db(conn)

        if not args.no_refresh:
            log.info("Refreshing earnings data for %d symbols...", len(stock_syms))
            count = await refresh_earnings(conn, stock_syms)
            log.info("Fetched %d earnings rows.\n", count)

        events = await get_upcoming_earnings(conn, stock_syms)
        window_events = await symbols_in_earnings_window(
            conn, stock_syms, EARNINGS_WINDOW_START, EARNINGS_WINDOW_END
        )
        window_syms = {e.symbol for e in window_events}

        print(f"IC Entry Window: {EARNINGS_WINDOW_START}-{EARNINGS_WINDOW_END} days before earnings")
        render_calendar(events, window_syms)

        if window_syms:
            print(f"Symbols in IC window: {', '.join(sorted(window_syms))}")
        else:
            print("No symbols currently in the IC entry window.")
            etf_syms = [s for s in symbols if is_etf(s)]
            if etf_syms:
                print(f"ETF fallback available: {', '.join(etf_syms)}")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Earnings calendar viewer")
    parser.add_argument("--no-refresh", action="store_true", help="Skip Yahoo fetch, show cached data only")
    parser.add_argument("--symbols", type=str, help="Comma-separated symbols (default: watchlist)")
    asyncio.run(main(parser.parse_args()))
