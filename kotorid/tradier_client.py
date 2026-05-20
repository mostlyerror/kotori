"""Async Tradier API client.

Tradier's JSON responses are inconsistent: a field may be `null`, a single
dict, or a list. All parsing here is defensive — see `_as_list`.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from kotorid.config import (
    TRADIER_ACCOUNT_ID,
    TRADIER_API_KEY,
    TRADIER_BASE,
)

# OCC option symbol: {UNDERLYING}{YYMMDD}{C|P}{STRIKE*1000 8-digit}
# Example: NVDA250516C00880000 -> NVDA, 2025-05-16, C, 880.0
_OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def _as_list(x: Any) -> list:
    """Normalize Tradier's null/dict/list into a list.

    Tradier returns:
    - `null` when the collection is empty
    - a single dict when there's exactly one item
    - a list when there are multiple items
    """
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def parse_occ_symbol(occ: str) -> dict | None:
    """Parse an OCC option symbol into its components.

    Returns ``None`` for non-OCC symbols (i.e. plain stock tickers).

    Example: ``NVDA250516C00880000`` ->
        ``{"underlying": "NVDA", "expiry": "2025-05-16",
           "strike": 880.0, "put_call": "C"}``
    """
    if not isinstance(occ, str):
        return None
    m = _OCC_RE.match(occ)
    if not m:
        return None
    underlying, ymd, pc, strike_raw = m.groups()
    yy, mm, dd = ymd[0:2], ymd[2:4], ymd[4:6]
    # Tradier uses 2-digit year. Assume 20xx.
    expiry = f"20{yy}-{mm}-{dd}"
    strike = int(strike_raw) / 1000.0
    return {
        "underlying": underlying,
        "expiry": expiry,
        "strike": strike,
        "put_call": pc,
    }


def build_client(
    base_url: str | None = None,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.AsyncClient:
    """Construct an httpx.AsyncClient with Tradier auth headers preset.

    The optional ``transport`` parameter is used in tests to inject a
    ``httpx.MockTransport`` so no real network calls are made.
    """
    return httpx.AsyncClient(
        base_url=base_url or TRADIER_BASE,
        headers={
            "Authorization": f"Bearer {api_key or TRADIER_API_KEY}",
            "Accept": "application/json",
        },
        transport=transport,
        timeout=15.0,
    )


async def get_account_id(client: httpx.AsyncClient) -> str:
    """Return the Tradier account ID.

    If ``TRADIER_ACCOUNT_ID`` is set in the environment, return it directly.
    Otherwise call ``/user/profile`` and return the first account number.
    """
    if TRADIER_ACCOUNT_ID:
        return TRADIER_ACCOUNT_ID
    resp = await client.get("/user/profile")
    resp.raise_for_status()
    data = resp.json()
    profile = (data or {}).get("profile") or {}
    accounts = _as_list(profile.get("account"))
    if not accounts:
        raise RuntimeError("Tradier /user/profile returned no accounts")
    return accounts[0]["account_number"]


async def get_positions(
    client: httpx.AsyncClient, account_id: str
) -> list[dict]:
    """Return a list of raw position dicts for the given account.

    Handles Tradier's null/single/list response shapes.
    """
    resp = await client.get(f"/accounts/{account_id}/positions")
    resp.raise_for_status()
    data = resp.json() or {}
    positions = data.get("positions")
    if positions is None or positions == "null":
        return []
    if isinstance(positions, dict):
        return _as_list(positions.get("position"))
    # Should not happen, but be defensive.
    return _as_list(positions)


async def get_quotes(
    client: httpx.AsyncClient, symbols: list[str]
) -> dict[str, dict]:
    """Return a dict mapping symbol -> quote dict.

    Returns an empty dict if symbols is empty.
    """
    if not symbols:
        return {}
    resp = await client.get(
        "/markets/quotes",
        params={"symbols": ",".join(symbols)},
    )
    resp.raise_for_status()
    data = resp.json() or {}
    quotes_block = data.get("quotes")
    if quotes_block is None or quotes_block == "null":
        return {}
    if isinstance(quotes_block, dict):
        quote_list = _as_list(quotes_block.get("quote"))
    else:
        quote_list = _as_list(quotes_block)
    out: dict[str, dict] = {}
    for q in quote_list:
        if isinstance(q, dict) and "symbol" in q:
            out[q["symbol"]] = q
    return out
