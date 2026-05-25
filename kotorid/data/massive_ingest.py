"""Ingest historical options data from Massive (ex-Polygon) free tier.

Fetches daily OHLCV for options contracts around earnings dates,
computes Greeks via Black-Scholes, and writes per-symbol parquet files
matching the ParquetProvider schema.

Free tier: 5 API calls/min, 2 years history, EOD data only.
"""
from __future__ import annotations

import logging
import math
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
from scipy.stats import norm

log = logging.getLogger(__name__)

API_BASE = "https://api.polygon.io"
CALLS_PER_MINUTE = 5
CALL_INTERVAL = 60.0 / CALLS_PER_MINUTE + 0.5  # ~12.5s between calls


def _api_key() -> str:
    key = os.environ.get("POLYGON_API_KEY", "")
    if not key:
        raise ValueError("POLYGON_API_KEY not set")
    return key


class RateLimiter:
    """Sliding-window rate limiter: at most N calls per 60s."""

    def __init__(self, max_per_minute: int = CALLS_PER_MINUTE):
        self.max_per_minute = max_per_minute
        self._timestamps: list[float] = []
        self.call_count = 0

    def wait(self) -> None:
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < 60.0]
        if len(self._timestamps) >= self.max_per_minute:
            sleep_until = self._timestamps[0] + 60.0
            sleep_for = sleep_until - now + 0.5
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._timestamps.append(time.monotonic())
        self.call_count += 1


def _get(client: httpx.Client, path: str, params: dict, limiter: RateLimiter) -> dict:
    limiter.wait()
    params["apiKey"] = _api_key()
    r = client.get(f"{API_BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def list_contracts(
    client: httpx.Client,
    symbol: str,
    as_of: date,
    min_dte: int,
    max_dte: int,
    limiter: RateLimiter,
) -> list[dict]:
    """List option contracts for a symbol within a DTE window."""
    exp_min = as_of + timedelta(days=min_dte)
    exp_max = as_of + timedelta(days=max_dte)
    all_contracts = []
    params = {
        "underlying_ticker": symbol,
        "as_of": as_of.isoformat(),
        "expiration_date.gte": exp_min.isoformat(),
        "expiration_date.lte": exp_max.isoformat(),
        "limit": 250,
    }
    data = _get(client, "/v3/reference/options/contracts", params, limiter)
    all_contracts.extend(data.get("results", []))
    while data.get("next_url"):
        limiter.wait()
        r = client.get(data["next_url"], params={"apiKey": _api_key()}, timeout=30)
        r.raise_for_status()
        data = r.json()
        all_contracts.extend(data.get("results", []))
    return all_contracts


def get_underlying_close(
    client: httpx.Client, symbol: str, d: date, limiter: RateLimiter,
) -> float | None:
    data = _get(client, f"/v1/open-close/{symbol}/{d.isoformat()}", {"adjusted": "true"}, limiter)
    return data.get("close")


def get_contract_daily_bars(
    client: httpx.Client, ticker: str, start: date, end: date, limiter: RateLimiter,
) -> list[dict]:
    """Get daily OHLCV bars for a single options contract."""
    data = _get(
        client,
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        {"adjusted": "true", "limit": 50000},
        limiter,
    )
    return data.get("results", [])


# --- Black-Scholes Greeks ---

def bs_delta(S: float, K: float, T: float, r: float, sigma: float, opt_type: str) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    if opt_type == "call":
        return norm.cdf(d1)
    return norm.cdf(d1) - 1.0


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm.pdf(d1) / (S * sigma * math.sqrt(T))


def bs_theta(S: float, K: float, T: float, r: float, sigma: float, opt_type: str) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    common = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
    if opt_type == "call":
        return (common - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365.0
    return (common + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365.0


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return S * norm.pdf(d1) * math.sqrt(T) / 100.0


def implied_vol(
    price: float, S: float, K: float, T: float, r: float, opt_type: str,
    tol: float = 1e-5, max_iter: int = 100,
) -> float:
    """Newton's method IV solver."""
    if price <= 0 or T <= 0 or S <= 0:
        return 0.0
    sigma = 0.3
    for _ in range(max_iter):
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if opt_type == "call":
            theo = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        else:
            theo = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        vega_val = S * norm.pdf(d1) * math.sqrt(T)
        if vega_val < 1e-12:
            break
        sigma -= (theo - price) / vega_val
        sigma = max(0.01, min(5.0, sigma))
        if abs(theo - price) < tol:
            break
    return sigma


def synthesize_bid_ask(close: float, volume: int) -> tuple[float, float]:
    """Estimate bid/ask from close price and volume.

    Wider spread for low-volume contracts, tighter for liquid ones.
    """
    if close <= 0:
        return 0.0, 0.0
    if volume >= 500:
        half_spread = close * 0.02
    elif volume >= 50:
        half_spread = close * 0.05
    else:
        half_spread = close * 0.10
    half_spread = max(half_spread, 0.01)
    return round(max(0.01, close - half_spread), 2), round(close + half_spread, 2)


def get_underlying_daily_bars(
    client: httpx.Client, symbol: str, start: date, end: date, limiter: RateLimiter,
) -> dict[date, float]:
    """Fetch all daily close prices for the underlying in one call."""
    data = _get(
        client,
        f"/v2/aggs/ticker/{symbol}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        {"adjusted": "true", "limit": 50000},
        limiter,
    )
    prices: dict[date, float] = {}
    for bar in data.get("results", []):
        ts = datetime.fromtimestamp(bar["t"] / 1000).date()
        prices[ts] = bar["c"]
    return prices


def ingest_symbol_earnings(
    client: httpx.Client,
    symbol: str,
    earnings_dates: list[date],
    out_dir: Path,
    limiter: RateLimiter,
    risk_free_rate: float = 0.05,
    dte_window: tuple[int, int] = (3, 30),
) -> int:
    """Fetch options data around earnings dates for one symbol.

    Minimizes API calls:
    - 1 call for underlying prices over the full period
    - 1 call per earnings date for contract listing
    - 1 call per unique contract for its daily bars
    """
    if not earnings_dates:
        return 0

    global_start = earnings_dates[0] - timedelta(days=dte_window[1])
    global_end = earnings_dates[-1] + timedelta(days=10)

    log.info("  %s: fetching underlying prices %s to %s", symbol, global_start, global_end)
    underlying_prices = get_underlying_daily_bars(client, symbol, global_start, global_end, limiter)
    if not underlying_prices:
        log.warning("  %s: no underlying price data", symbol)
        return 0

    # Collect unique contracts across all earnings dates
    seen_tickers: set[str] = set()
    contracts_to_fetch: list[dict] = []

    for earn_date in earnings_dates:
        scan_date = earn_date - timedelta(days=dte_window[1])
        spot = underlying_prices.get(scan_date)
        if not spot:
            nearest = min(underlying_prices.keys(), key=lambda d: abs((d - scan_date).days))
            spot = underlying_prices[nearest]

        contracts = list_contracts(client, symbol, scan_date, dte_window[0], dte_window[1], limiter)
        near_money = [
            c for c in contracts
            if abs(c["strike_price"] - spot) / spot < 0.12
        ]
        new_count = 0
        for c in near_money:
            if c["ticker"] not in seen_tickers:
                seen_tickers.add(c["ticker"])
                contracts_to_fetch.append(c)
                new_count += 1
        log.info("  %s %s: %d contracts, %d near-money, %d new",
                 symbol, earn_date, len(contracts), len(near_money), new_count)

    log.info("  %s: fetching bars for %d unique contracts...", symbol, len(contracts_to_fetch))

    all_rows: list[dict] = []
    for i, contract in enumerate(contracts_to_fetch):
        ticker = contract["ticker"]
        strike = contract["strike_price"]
        opt_type = contract["contract_type"]
        expiry = date.fromisoformat(contract["expiration_date"])

        bar_start = expiry - timedelta(days=dte_window[1] + 5)
        bar_end = expiry
        bars = get_contract_daily_bars(client, ticker, bar_start, bar_end, limiter)
        if not bars:
            continue

        for bar in bars:
            ts = datetime.fromtimestamp(bar["t"] / 1000).date()
            close = bar["c"]
            volume = bar.get("v", 0)

            S = underlying_prices.get(ts)
            if not S:
                continue

            T = max((expiry - ts).days, 1) / 365.0
            iv = implied_vol(close, S, strike, T, risk_free_rate, opt_type)
            delta = bs_delta(S, strike, T, risk_free_rate, iv, opt_type)
            gamma = bs_gamma(S, strike, T, risk_free_rate, iv)
            theta = bs_theta(S, strike, T, risk_free_rate, iv, opt_type)
            vega = bs_vega(S, strike, T, risk_free_rate, iv)
            bid, ask = synthesize_bid_ask(close, volume)

            all_rows.append({
                "date": ts,
                "symbol": symbol,
                "type": opt_type,
                "strike": strike,
                "expiration": expiry,
                "bid": bid,
                "ask": ask,
                "delta": round(delta, 4),
                "gamma": round(gamma, 6),
                "theta": round(theta, 4),
                "vega": round(vega, 4),
                "implied_volatility": round(iv, 4),
                "volume": volume,
                "open_interest": 0,
            })

        if (i + 1) % 20 == 0:
            log.info("  %s: %d/%d contracts done, %d rows so far",
                     symbol, i + 1, len(contracts_to_fetch), len(all_rows))

    if not all_rows:
        log.warning("  %s: no data collected", symbol)
        return 0

    df = pl.DataFrame(all_rows).sort("date", "type", "strike")
    out_path = out_dir / f"{symbol}.parquet"
    df.write_parquet(out_path)
    log.info("  %s: wrote %d rows to %s", symbol, len(df), out_path)
    return len(df)
