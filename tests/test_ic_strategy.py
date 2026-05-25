import pytest
import polars as pl
from kotorid.strategy.config import ICConfig
from kotorid.strategy.ic_strategy import select_ic_candidate, check_exit

def _make_chain() -> pl.DataFrame:
    return pl.DataFrame({
        "type": ["call"] * 4 + ["put"] * 4,
        "strike": [480.0, 485.0, 490.0, 495.0, 460.0, 465.0, 470.0, 475.0],
        "bid": [4.20, 1.80, 0.60, 0.15, 0.12, 0.45, 1.50, 3.80],
        "ask": [4.40, 2.00, 0.75, 0.25, 0.20, 0.55, 1.70, 4.00],
        "delta": [0.55, 0.30, 0.14, 0.05, -0.05, -0.13, -0.30, -0.55],
    })

def test_select_ic_candidate_picks_nearest_delta():
    cfg = ICConfig(target_delta=0.16, wing_width=5.0, min_credit_ratio=0.0)
    chain = _make_chain()
    candidate = select_ic_candidate(chain, cfg)
    assert candidate is not None
    assert candidate["short_call"] == 490.0  # delta 0.14, nearest to 0.16
    assert candidate["short_put"] == 465.0   # delta -0.13, nearest to 0.16
    assert candidate["long_call"] == 495.0
    assert candidate["long_put"] == 460.0

def test_select_ic_candidate_returns_none_when_no_wings():
    cfg = ICConfig(target_delta=0.16, wing_width=50.0)
    candidate = select_ic_candidate(_make_chain(), cfg)
    assert candidate is None

def test_select_ic_candidate_credit_ratio_filter():
    cfg = ICConfig(target_delta=0.16, wing_width=5.0, min_credit_ratio=0.90)
    candidate = select_ic_candidate(_make_chain(), cfg)
    assert candidate is None

def test_check_exit_profit_target():
    assert check_exit(entry_credit=1.00, current_debit=0.40, cfg=ICConfig()) == "profit_target"

def test_check_exit_stop_loss():
    assert check_exit(entry_credit=1.00, current_debit=2.10, cfg=ICConfig()) == "stop_loss"

def test_check_exit_no_trigger():
    assert check_exit(entry_credit=1.00, current_debit=0.80, cfg=ICConfig()) is None


def _make_chain_no_greeks() -> pl.DataFrame:
    """Chain with strikes around a ~500 underlying, no delta needed.

    Prices decrease monotonically away from ATM for each type,
    producing realistic credit spreads.
    """
    strikes = [k * 1.0 for k in range(460, 545, 5)]
    n = len(strikes)
    spot = 500.0
    # Calls: high bid near ATM, decaying OTM
    call_bids = [round(max(0.01, 20.0 * max(0, 1.0 - 0.03 * (k - spot)) if k >= spot
                           else spot - k + 5.0), 2) for k in strikes]
    call_asks = [round(b * 1.05 + 0.05, 2) for b in call_bids]
    # Puts: high bid near ATM, decaying OTM (lower strikes)
    put_bids = [round(max(0.01, 20.0 * max(0, 1.0 - 0.03 * (spot - k)) if k <= spot
                          else k - spot + 5.0), 2) for k in strikes]
    put_asks = [round(b * 1.05 + 0.05, 2) for b in put_bids]
    return pl.DataFrame({
        "type": ["call"] * n + ["put"] * n,
        "strike": strikes + strikes,
        "bid": call_bids + put_bids,
        "ask": call_asks + put_asks,
        "delta": [0.0] * (n * 2),
    })


def test_fixed_otm_picks_correct_strikes():
    cfg = ICConfig(use_fixed_otm=True, short_otm_pct=0.05, wing_width=5.0, min_credit_ratio=0.0)
    chain = _make_chain_no_greeks()
    candidate = select_ic_candidate(chain, cfg)
    assert candidate is not None
    # Median strike = 500, 5% OTM → call target 525, put target 475
    assert candidate["short_call"] == 525.0
    assert candidate["short_put"] == 475.0
    assert candidate["long_call"] == 530.0
    assert candidate["long_put"] == 470.0
    assert candidate["credit"] > 0


def test_fixed_otm_returns_none_when_no_wings():
    cfg = ICConfig(use_fixed_otm=True, short_otm_pct=0.05, wing_width=50.0)
    candidate = select_ic_candidate(_make_chain_no_greeks(), cfg)
    assert candidate is None
