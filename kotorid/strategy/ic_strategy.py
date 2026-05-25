from __future__ import annotations
import polars as pl
from kotorid.strategy.config import ICConfig

def select_ic_candidate(chain: pl.DataFrame, cfg: ICConfig) -> dict | None:
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

def check_exit(entry_credit: float, current_debit: float, cfg: ICConfig) -> str | None:
    if current_debit <= entry_credit * cfg.profit_target:
        return "profit_target"
    if current_debit >= entry_credit * cfg.stop_loss:
        return "stop_loss"
    return None
