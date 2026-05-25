from __future__ import annotations
import logging
import os
from pathlib import Path
import httpx
import polars as pl

log = logging.getLogger(__name__)
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

def fetch_fred_series(series_id: str, start: str = "2020-01-01", end: str = "2025-12-31", api_key: str | None = None) -> pl.DataFrame:
    if api_key is None:
        api_key = FRED_API_KEY
    if not api_key:
        raise ValueError("FRED_API_KEY env var is not set. Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html")
    resp = httpx.get(FRED_BASE, params={"series_id": series_id, "api_key": api_key, "file_type": "json", "observation_start": start, "observation_end": end}, timeout=30.0)
    resp.raise_for_status()
    observations = resp.json()["observations"]
    rows = []
    for obs in observations:
        val = obs["value"]
        if val == ".":
            continue
        rows.append({"date": obs["date"], "value": float(val)})
    return pl.DataFrame(rows).with_columns(pl.col("date").str.to_date())

def ingest_fred_signals(out_dir: Path, start: str = "2020-01-01", end: str = "2025-12-31", series: list[str] | None = None) -> None:
    series = series or ["VIXCLS", "BAMLH0A0HYM2", "T10Y2Y"]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for s in series:
        log.info("Fetching FRED series %s", s)
        df = fetch_fred_series(s, start=start, end=end)
        path = out_dir / f"{s}.parquet"
        df.write_parquet(path)
        log.info("Wrote %d rows to %s", len(df), path)

def ingest_philippdubach(out_dir: Path, symbols: list[str] | None = None) -> None:
    symbols = symbols or ["SPY", "QQQ", "IWM", "AAPL", "NVDA"]
    base_url = "https://static.philippdubach.com/data/options"
    options_dir = Path(out_dir) / "options"
    options_dir.mkdir(parents=True, exist_ok=True)
    for symbol in symbols:
        log.info("Downloading %s options data...", symbol)
        frames = []
        for year in range(2020, 2026):
            url = f"{base_url}/{symbol}/{year}.parquet"
            try:
                resp = httpx.get(url, timeout=120.0, follow_redirects=True)
                resp.raise_for_status()
                path = options_dir / f"{symbol}_{year}.parquet"
                path.write_bytes(resp.content)
                frames.append(pl.read_parquet(path))
                path.unlink()
                log.info("  %s/%d: %d rows", symbol, year, len(frames[-1]))
            except httpx.HTTPError:
                log.warning("  %s/%d: not available, skipping", symbol, year)
        if frames:
            combined = pl.concat(frames)
            out_path = options_dir / f"{symbol}.parquet"
            combined.write_parquet(out_path)
            log.info("Wrote %s: %d total rows", out_path, len(combined))
