from __future__ import annotations
import polars as pl
from kotorid.strategy.config import ICConfig


def _select_delta_based(chain: pl.DataFrame, cfg: ICConfig) -> dict | None:
    """Select IC strikes by target delta (requires Greeks in data)."""
    calls = chain.filter(pl.col("type") == "call").sort("strike")
    puts = chain.filter(pl.col("type") == "put").sort("strike", descending=True)
    if len(calls) == 0 or len(puts) == 0:
        return None

    short_call_row = calls.filter(pl.col("delta") > 0).sort(
        (pl.col("delta") - cfg.target_delta).abs()
    ).head(1)
    short_put_row = puts.filter(pl.col("delta") < 0).sort(
        (pl.col("delta").abs() - cfg.target_delta).abs()
    ).head(1)
    if len(short_call_row) == 0 or len(short_put_row) == 0:
        return None

    sc_strike = short_call_row["strike"][0]
    sp_strike = short_put_row["strike"][0]
    lc_strike = sc_strike + cfg.wing_width
    lp_strike = sp_strike - cfg.wing_width

    lc_row = calls.filter(pl.col("strike") == lc_strike)
    lp_row = puts.filter(pl.col("strike") == lp_strike)
    if len(lc_row) == 0 or len(lp_row) == 0:
        return None

    sc_bid = short_call_row["bid"][0]
    sp_bid = short_put_row["bid"][0]
    lc_ask = lc_row["ask"][0]
    lp_ask = lp_row["ask"][0]
    credit = (sc_bid + sp_bid) - (lc_ask + lp_ask)
    if credit <= 0:
        return None

    credit_ratio = credit / cfg.wing_width
    if credit_ratio < cfg.min_credit_ratio:
        return None

    return {
        "short_call": sc_strike, "long_call": lc_strike,
        "short_put": sp_strike, "long_put": lp_strike,
        "credit": round(credit, 2),
        "max_loss": round((cfg.wing_width - credit) * 100, 2),
        "credit_ratio": round(credit_ratio, 4),
    }


def _nearest_strike(chain: pl.DataFrame, target: float, opt_type: str) -> pl.DataFrame:
    """Find the row with the strike closest to target."""
    subset = chain.filter(pl.col("type") == opt_type)
    if len(subset) == 0:
        return subset
    return subset.sort((pl.col("strike") - target).abs()).head(1)


def _select_fixed_otm(chain: pl.DataFrame, cfg: ICConfig) -> dict | None:
    """Select IC strikes by fixed % OTM from the underlying mid-price.

    Finds the ATM price from the chain (average of nearest call ask and
    put ask at the mid-strike), then places short strikes at ±otm_pct.
    No Greeks required.
    """
    calls = chain.filter(pl.col("type") == "call").sort("strike")
    puts = chain.filter(pl.col("type") == "put").sort("strike", descending=True)
    if len(calls) == 0 or len(puts) == 0:
        return None

    all_strikes = chain["strike"].unique().sort()
    spot = float(all_strikes.median())

    sc_target = spot * (1 + cfg.short_otm_pct)
    sp_target = spot * (1 - cfg.short_otm_pct)

    sc_row = _nearest_strike(chain, sc_target, "call")
    sp_row = _nearest_strike(chain, sp_target, "put")
    if len(sc_row) == 0 or len(sp_row) == 0:
        return None

    sc_strike = sc_row["strike"][0]
    sp_strike = sp_row["strike"][0]
    lc_strike = sc_strike + cfg.wing_width
    lp_strike = sp_strike - cfg.wing_width

    lc_row = calls.filter(pl.col("strike") == lc_strike)
    lp_row = puts.filter(pl.col("strike") == lp_strike)
    if len(lc_row) == 0 or len(lp_row) == 0:
        return None

    sc_bid = sc_row["bid"][0]
    sp_bid = sp_row["bid"][0]
    lc_ask = lc_row["ask"][0]
    lp_ask = lp_row["ask"][0]
    credit = (sc_bid + sp_bid) - (lc_ask + lp_ask)
    if credit <= 0:
        return None

    credit_ratio = credit / cfg.wing_width
    if credit_ratio < cfg.min_credit_ratio:
        return None

    return {
        "short_call": sc_strike, "long_call": lc_strike,
        "short_put": sp_strike, "long_put": lp_strike,
        "credit": round(credit, 2),
        "max_loss": round((cfg.wing_width - credit) * 100, 2),
        "credit_ratio": round(credit_ratio, 4),
    }


def select_ic_candidate(chain: pl.DataFrame, cfg: ICConfig) -> dict | None:
    if cfg.use_fixed_otm:
        return _select_fixed_otm(chain, cfg)
    return _select_delta_based(chain, cfg)

def check_exit(entry_credit: float, current_debit: float, cfg: ICConfig) -> str | None:
    if current_debit <= entry_credit * cfg.profit_target:
        return "profit_target"
    if current_debit >= entry_credit * cfg.stop_loss:
        return "stop_loss"
    return None
