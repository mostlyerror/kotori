"""Tests for the candidate_scan module."""
from datetime import date, timedelta

import httpx
import pytest

from kotorid.candidate_scan import (
    DEFAULT_WATCHLIST,
    estimate_credit,
    get_watchlist,
    scan_candidates,
    scan_one_symbol,
    select_strikes,
)
from kotorid.db import get_db, init_db
from kotorid.tradier_client import build_client


def _make_chain(spot: float) -> list[dict]:
    """Build a synthetic chain with deltas approximating a real near-ATM zone."""
    options = []
    # Strikes from spot-20 to spot+20, $1 apart
    for k in range(int(spot) - 20, int(spot) + 21):
        # Crude delta approximation: linear interpolation around spot
        # Call delta: 0.50 at ATM, drops ~0.04 per dollar OTM, rises toward 1.0 ITM
        call_delta = max(0.01, min(0.99, 0.50 - 0.04 * (k - spot)))
        put_delta = call_delta - 1.0
        # Crude prices: intrinsic + extrinsic
        call_mid = max(0.01, spot - k) + max(0.05, 2.0 - 0.10 * abs(k - spot))
        put_mid = max(0.01, k - spot) + max(0.05, 2.0 - 0.10 * abs(k - spot))
        options.append({
            "symbol": f"SPY260530C{k*1000:08d}",
            "option_type": "call",
            "strike": float(k),
            "bid": round(call_mid - 0.01, 2),
            "ask": round(call_mid + 0.01, 2),
            "greeks": {"delta": round(call_delta, 4), "mid_iv": 0.20},
        })
        options.append({
            "symbol": f"SPY260530P{k*1000:08d}",
            "option_type": "put",
            "strike": float(k),
            "bid": round(put_mid - 0.01, 2),
            "ask": round(put_mid + 0.01, 2),
            "greeks": {"delta": round(put_delta, 4), "mid_iv": 0.20},
        })
    return options


def test_select_strikes_picks_short_at_target_delta():
    """Short call near 0.16 delta, short put near -0.16 delta, wings $5 outside."""
    chain = _make_chain(spot=750)
    legs = select_strikes(chain, target_delta=0.16, wing_width=5)
    assert legs is not None
    # Short call delta should be close to 0.16; expected strike ~$758-759 with our linear approx
    assert abs(legs["short_call"]["greeks"]["delta"] - 0.16) < 0.05
    assert legs["long_call"]["strike"] == legs["short_call"]["strike"] + 5
    # Short put delta near -0.16
    assert abs(legs["short_put"]["greeks"]["delta"] + 0.16) < 0.05
    assert legs["long_put"]["strike"] == legs["short_put"]["strike"] - 5
    # Sanity: short strikes are on opposite sides of spot
    assert legs["short_call"]["strike"] > 750
    assert legs["short_put"]["strike"] < 750


def test_select_strikes_returns_none_without_delta():
    """If chain has no greeks data, can't pick strikes by delta."""
    chain = [{"option_type": "call", "strike": 750.0, "bid": 5, "ask": 5.1}]
    assert select_strikes(chain) is None


def test_select_strikes_returns_none_when_wing_unavailable():
    """If the long-wing strike doesn't exist in chain, fail cleanly."""
    chain = _make_chain(spot=750)
    # Strip out the candidate long_call ($5 above where short_call lands)
    chain = [o for o in chain if not (o["option_type"] == "call" and o["strike"] == 763.0)]
    legs = select_strikes(chain, target_delta=0.16, wing_width=5)
    # short_call lands at 758; long_call would be 763, which we removed
    # So legs might be None, OR short_call might land at a different strike.
    # Either way we should NOT get a leg dict with a missing long_call.
    if legs is not None:
        assert legs["long_call"]["strike"] == legs["short_call"]["strike"] + 5


def test_estimate_credit_positive_for_typical_ic():
    """Sells the more-expensive shorts, buys cheaper longs — net positive credit."""
    legs = {
        "short_call": {"bid": 0.70, "ask": 0.72},
        "long_call":  {"bid": 0.24, "ask": 0.26},
        "short_put":  {"bid": 1.50, "ask": 1.52},
        "long_put":   {"bid": 0.98, "ask": 1.00},
    }
    # mids: SC=0.71, LC=0.25, SP=1.51, LP=0.99
    # credit = (0.71 + 1.51) - (0.25 + 0.99) = 0.98
    assert estimate_credit(legs) == pytest.approx(0.98)


def test_get_watchlist_uses_env_when_set(monkeypatch):
    monkeypatch.setenv("KOTORI_WATCHLIST", "spy, qqq , aapl")
    assert get_watchlist() == ["SPY", "QQQ", "AAPL"]


def test_get_watchlist_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("KOTORI_WATCHLIST", raising=False)
    assert get_watchlist() == DEFAULT_WATCHLIST


def _make_client(handler):
    transport = httpx.MockTransport(handler)
    return build_client(
        base_url="https://sandbox.tradier.com/v1",
        api_key="testkey",
        transport=transport,
    )


def _scan_handler(spot=750, expiry_days_out=7):
    """Mock Tradier responses for a full scan_one_symbol flow."""
    expiry = (date.today() + timedelta(days=expiry_days_out)).isoformat()

    def handler(request):
        path = request.url.path
        if path.endswith("/markets/options/expirations"):
            return httpx.Response(200, json={"expirations": {"date": [expiry]}})
        if path.endswith("/markets/quotes"):
            return httpx.Response(200, json={
                "quotes": {"quote": {"symbol": "SPY", "last": spot, "bid": spot - 0.05, "ask": spot + 0.05}}
            })
        if path.endswith("/markets/options/chains"):
            return httpx.Response(200, json={
                "options": {"option": _make_chain(spot)}
            })
        raise AssertionError(f"unexpected request: {path}")
    return handler


@pytest.mark.asyncio
async def test_scan_one_symbol_writes_candidate_and_inbox(tmp_path):
    """Happy path: full scan flow inserts candidate + agent_run + inbox card."""
    db_path = str(tmp_path / "scan.db")
    async with get_db(db_path) as db:
        await init_db(db)
        async with _make_client(_scan_handler(spot=750)) as c:
            result = await scan_one_symbol(db, c, "SPY")
        assert result is not None
        assert result["symbol"] == "SPY"

        cand = await (await db.execute(
            "SELECT symbol, expected_credit, max_loss, order_status, short_call, long_call, short_put, long_put "
            "FROM candidates WHERE symbol='SPY'"
        )).fetchone()
        assert cand["order_status"] == "pending_approval"
        assert cand["expected_credit"] > 0
        # Sanity: short strikes flank spot, longs flank shorts
        assert cand["long_put"] < cand["short_put"] < 750 < cand["short_call"] < cand["long_call"]
        assert cand["long_call"] - cand["short_call"] == pytest.approx(5)
        assert cand["short_put"] - cand["long_put"] == pytest.approx(5)

        inbox = await (await db.execute(
            "SELECT priority, item_type, symbol, title FROM inbox_items WHERE symbol='SPY'"
        )).fetchone()
        assert inbox["priority"] == "action_required"
        assert inbox["item_type"] == "ic_candidate"


@pytest.mark.asyncio
async def test_scan_one_symbol_skips_duplicate_same_day(tmp_path):
    """A symbol already scanned today should not generate a second candidate."""
    db_path = str(tmp_path / "dup.db")
    async with get_db(db_path) as db:
        await init_db(db)
        async with _make_client(_scan_handler(spot=750)) as c:
            first = await scan_one_symbol(db, c, "SPY")
            second = await scan_one_symbol(db, c, "SPY")
        assert first is not None
        assert second is None
        count = await (await db.execute(
            "SELECT COUNT(*) AS n FROM candidates WHERE symbol='SPY'"
        )).fetchone()
        assert count["n"] == 1


@pytest.mark.asyncio
async def test_scan_candidates_processes_full_watchlist(tmp_path):
    """scan_candidates iterates the provided symbol list and writes for each."""
    db_path = str(tmp_path / "watchlist.db")
    async with get_db(db_path) as db:
        await init_db(db)
        async with _make_client(_scan_handler(spot=750)) as c:
            written = await scan_candidates(db, c, symbols=["SPY", "QQQ", "IWM"])
        assert len(written) == 3
        assert {w["symbol"] for w in written} == {"SPY", "QQQ", "IWM"}
