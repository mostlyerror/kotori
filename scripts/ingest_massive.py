"""Download historical options data from Massive (ex-Polygon) around earnings dates.

This is a slow overnight job — free tier is 5 calls/min.
Progress is saved per-symbol so you can interrupt and resume.

Usage:
    python3 scripts/ingest_massive.py
    python3 scripts/ingest_massive.py --symbols AAPL,NVDA
    python3 scripts/ingest_massive.py --dry-run   # show what would be fetched
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import httpx
import polars as pl

from kotorid.data.massive_ingest import (
    RateLimiter,
    ingest_symbol_earnings,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_SYMBOLS = [
    "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "GOOGL",
    "AMD", "NFLX", "CRM", "JPM", "XOM", "UNH", "DIS", "BA",
]

EARNINGS_PATH = Path("data/daily/events/earnings.parquet")
OUT_DIR = Path("data/daily/options")


def load_earnings(symbols: list[str]) -> dict[str, list[date]]:
    if not EARNINGS_PATH.exists():
        log.error("No earnings data at %s — run scripts/fetch_historical_earnings.py first", EARNINGS_PATH)
        sys.exit(1)
    df = pl.read_parquet(EARNINGS_PATH)
    cutoff = date.today()
    two_years_ago = date(cutoff.year - 2, cutoff.month, cutoff.day)
    result: dict[str, list[date]] = {}
    for sym in symbols:
        dates = (
            df.filter(
                (pl.col("symbol") == sym)
                & (pl.col("earnings_date") >= two_years_ago)
                & (pl.col("earnings_date") < cutoff)
            )
            .sort("earnings_date")["earnings_date"]
            .to_list()
        )
        if dates:
            result[sym] = dates
    return result


def main():
    parser = argparse.ArgumentParser(description="Ingest options data from Massive/Polygon")
    parser.add_argument("--symbols", type=str, help="Comma-separated symbols (default: 15-stock watchlist)")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Show plan without fetching")
    args = parser.parse_args()

    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS
    earnings_map = load_earnings(symbols)

    total_events = sum(len(v) for v in earnings_map.values())
    estimated_calls = total_events * 55  # ~55 calls per earnings event
    estimated_hours = estimated_calls * 12.5 / 3600

    log.info("Plan: %d symbols, %d earnings events", len(earnings_map), total_events)
    log.info("Estimated: ~%d API calls, ~%.1f hours", estimated_calls, estimated_hours)
    log.info("")

    for sym, dates in sorted(earnings_map.items()):
        log.info("  %s: %d earnings (%s to %s)", sym, len(dates),
                 dates[0].isoformat(), dates[-1].isoformat())

    if args.dry_run:
        log.info("\nDry run — exiting.")
        return

    args.out_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter()

    done = 0
    for sym, dates in sorted(earnings_map.items()):
        existing = args.out_dir / f"{sym}.parquet"
        if existing.exists():
            log.info("Skipping %s — %s already exists (delete to re-fetch)", sym, existing)
            done += 1
            continue

        log.info("[%d/%d] Fetching %s (%d earnings events)...",
                 done + 1, len(earnings_map), sym, len(dates))
        try:
            with httpx.Client() as client:
                count = ingest_symbol_earnings(client, sym, dates, args.out_dir, limiter)
            log.info("  %s complete: %d rows, %d API calls so far",
                     sym, count, limiter.call_count)
        except Exception:
            log.exception("  %s FAILED — continuing to next symbol", sym)
        done += 1

    log.info("\nDone. Total API calls: %d", limiter.call_count)


if __name__ == "__main__":
    main()
