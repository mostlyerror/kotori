"""Earnings calendar: fetch, cache, and query upcoming earnings dates."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass

import asyncpg
import yfinance as yf

log = logging.getLogger(__name__)

ETFS = frozenset({"SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "GLD", "TLT", "HYG"})


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    earnings_date: date
    eps_estimate: float | None
    reported_eps: float | None
    surprise_pct: float | None
    is_confirmed: bool
    days_until: int


def is_etf(symbol: str) -> bool:
    return symbol.upper() in ETFS


def fetch_earnings_from_yahoo(symbol: str) -> list[dict]:
    """Fetch earnings dates for a single symbol from Yahoo Finance.

    Returns a list of dicts with keys: earnings_date, eps_estimate,
    reported_eps, surprise_pct, is_confirmed.
    """
    if is_etf(symbol):
        return []
    try:
        ticker = yf.Ticker(symbol)
        ed = ticker.earnings_dates
        if ed is None or ed.empty:
            return []
    except Exception:
        log.warning("fetch_earnings: failed for %s", symbol, exc_info=True)
        return []

    rows = []
    for ts, row in ed.iterrows():
        earn_date = ts.date() if hasattr(ts, "date") else ts
        eps_est = row.get("EPS Estimate")
        reported = row.get("Reported EPS")
        surprise = row.get("Surprise(%)")
        is_confirmed = reported is not None and not _is_nan(reported)
        rows.append({
            "earnings_date": earn_date,
            "eps_estimate": _clean_float(eps_est),
            "reported_eps": _clean_float(reported),
            "surprise_pct": _clean_float(surprise),
            "is_confirmed": is_confirmed,
        })
    return rows


def _is_nan(v) -> bool:
    try:
        import math
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def _clean_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        import math
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


async def refresh_earnings(
    conn: asyncpg.Connection, symbols: list[str],
) -> int:
    """Fetch earnings for all symbols and upsert into earnings_calendar.

    Returns count of rows upserted.
    """
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    count = 0
    for symbol in symbols:
        rows = fetch_earnings_from_yahoo(symbol)
        for r in rows:
            await conn.execute(
                """INSERT INTO earnings_calendar
                   (symbol, earnings_date, eps_estimate, reported_eps,
                    surprise_pct, is_confirmed, fetched_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (symbol, earnings_date)
                   DO UPDATE SET eps_estimate = EXCLUDED.eps_estimate,
                                 reported_eps = EXCLUDED.reported_eps,
                                 surprise_pct = EXCLUDED.surprise_pct,
                                 is_confirmed = EXCLUDED.is_confirmed,
                                 fetched_at = EXCLUDED.fetched_at""",
                symbol, r["earnings_date"], r["eps_estimate"],
                r["reported_eps"], r["surprise_pct"],
                r["is_confirmed"], now_iso,
            )
            count += 1
    log.info("refresh_earnings: upserted %d rows for %d symbols", count, len(symbols))
    return count


async def get_upcoming_earnings(
    conn: asyncpg.Connection,
    symbols: list[str] | None = None,
    within_days: int = 90,
) -> list[EarningsEvent]:
    """Return upcoming earnings events sorted by date."""
    today = date.today()
    end_date = today + timedelta(days=within_days)
    if symbols:
        rows = await conn.fetch(
            """SELECT symbol, earnings_date, eps_estimate, reported_eps,
                      surprise_pct, is_confirmed
               FROM earnings_calendar
               WHERE earnings_date >= $1
                 AND earnings_date <= $2
                 AND symbol = ANY($3)
               ORDER BY earnings_date, symbol""",
            today, end_date, symbols,
        )
    else:
        rows = await conn.fetch(
            """SELECT symbol, earnings_date, eps_estimate, reported_eps,
                      surprise_pct, is_confirmed
               FROM earnings_calendar
               WHERE earnings_date >= $1
                 AND earnings_date <= $2
               ORDER BY earnings_date, symbol""",
            today, end_date,
        )
    return [
        EarningsEvent(
            symbol=r["symbol"],
            earnings_date=r["earnings_date"],
            eps_estimate=r["eps_estimate"],
            reported_eps=r["reported_eps"],
            surprise_pct=r["surprise_pct"],
            is_confirmed=r["is_confirmed"],
            days_until=(r["earnings_date"] - today).days,
        )
        for r in rows
    ]


async def symbols_in_earnings_window(
    conn: asyncpg.Connection,
    symbols: list[str],
    window_start: int = 3,
    window_end: int = 14,
) -> list[EarningsEvent]:
    """Return symbols whose next earnings fall within the IC entry window.

    Default window: 3-14 days before earnings. This is where IV is
    elevated enough for good credit but gamma risk hasn't spiked yet.
    """
    today = date.today()
    start_date = today + timedelta(days=window_start)
    end_date = today + timedelta(days=window_end)
    rows = await conn.fetch(
        """SELECT symbol, earnings_date, eps_estimate, reported_eps,
                  surprise_pct, is_confirmed
           FROM earnings_calendar
           WHERE symbol = ANY($1)
             AND earnings_date >= $2
             AND earnings_date <= $3
           ORDER BY earnings_date, symbol""",
        symbols, start_date, end_date,
    )
    return [
        EarningsEvent(
            symbol=r["symbol"],
            earnings_date=r["earnings_date"],
            eps_estimate=r["eps_estimate"],
            reported_eps=r["reported_eps"],
            surprise_pct=r["surprise_pct"],
            is_confirmed=r["is_confirmed"],
            days_until=(r["earnings_date"] - today).days,
        )
        for r in rows
    ]
