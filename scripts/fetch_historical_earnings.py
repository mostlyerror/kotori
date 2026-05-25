"""Fetch historical earnings dates from Yahoo Finance and save as parquet.

Writes data/daily/events/earnings.parquet with columns:
    symbol, earnings_date, eps_estimate, reported_eps, surprise_pct

Usage:
    python3 scripts/fetch_historical_earnings.py
    python3 scripts/fetch_historical_earnings.py --symbols AAPL,NVDA,TSLA
    python3 scripts/fetch_historical_earnings.py --out data/daily/events/earnings.parquet
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

import polars as pl
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

BACKTEST_UNIVERSE = [
    "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "GOOGL",
    "AMD", "NFLX", "CRM", "JPM", "XOM", "UNH", "DIS", "BA",
    "ADBE", "ORCL", "INTC", "BAC", "GS",
    "V", "MA", "HD", "WMT", "COST",
    "JNJ", "LLY", "ABBV", "PFE", "MRK",
    "CVX", "SLB", "CAT", "FDX", "UPS",
    "NKE", "SBUX", "MCD", "UBER", "SHOP",
]


def fetch_all_earnings(symbols: list[str]) -> pl.DataFrame:
    all_rows: list[dict] = []
    for sym in symbols:
        log.info("Fetching %s...", sym)
        try:
            t = yf.Ticker(sym)
            ed = t.earnings_dates
            if ed is None or ed.empty:
                log.info("  %s: no earnings data", sym)
                continue
            for ts, row in ed.iterrows():
                earn_date = ts.date() if hasattr(ts, "date") else ts
                eps_est = _clean(row.get("EPS Estimate"))
                reported = _clean(row.get("Reported EPS"))
                surprise = _clean(row.get("Surprise(%)"))
                all_rows.append({
                    "symbol": sym,
                    "earnings_date": earn_date,
                    "eps_estimate": eps_est,
                    "reported_eps": reported,
                    "surprise_pct": surprise,
                })
            log.info("  %s: %d earnings dates", sym, len([r for r in all_rows if r["symbol"] == sym]))
        except Exception:
            log.warning("  %s: failed", sym, exc_info=True)

    return pl.DataFrame(all_rows).sort("earnings_date", "symbol")


def _clean(v) -> float | None:
    if v is None:
        return None
    try:
        import math
        f = float(v)
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=str, help="Comma-separated symbols")
    parser.add_argument("--out", type=Path, default=Path("data/daily/events/earnings.parquet"))
    args = parser.parse_args()

    symbols = args.symbols.split(",") if args.symbols else BACKTEST_UNIVERSE
    df = fetch_all_earnings(symbols)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(args.out)
    log.info("\nWrote %d rows for %d symbols to %s", len(df), df["symbol"].n_unique(), args.out)
    log.info("Date range: %s to %s", df["earnings_date"].min(), df["earnings_date"].max())


if __name__ == "__main__":
    main()
