"""Run a baseline IC backtest over daily options data.

Usage:
    uv run python scripts/run_backtest.py --data-dir data/daily --start 2023-01-01 --end 2024-12-31
    uv run python scripts/run_backtest.py --data-dir data/daily --start 2023-01-01 --end 2024-12-31 --use-signals
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kotorid.clock import BacktestClock, MarketState
from kotorid.data.parquet_provider import ParquetProvider
from kotorid.execution.simulated import SimulatedExecutor
from kotorid.execution.cost import CostConfig
from kotorid.execution.executor import Order, OrderSide
from kotorid.portfolio.portfolio import Portfolio
from kotorid.portfolio.risk import MaxPositionCount
from kotorid.strategy.config import ICConfig
from kotorid.strategy.ic_strategy import select_ic_candidate, check_exit
from kotorid.signals.mesh import SignalMesh
from kotorid.signals.regime import (
    should_hard_gate,
    update_hy_spread,
    update_pead,
    update_vix_regime,
    update_yield_curve,
)
from kotorid.strategy.allocator import StrategyAllocator
from kotorid.analytics.stats import compute_stats
from kotorid.earnings import is_etf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

EARNINGS_WINDOW_START = 3
EARNINGS_WINDOW_END = 14


def run_backtest(
    data_dir: Path,
    start: date,
    end: date,
    ic_config: ICConfig | None = None,
    use_signals: bool = False,
    use_earnings: bool = False,
    symbols: list[str] | None = None,
) -> dict:
    cfg = ic_config or ICConfig()
    provider = ParquetProvider(data_dir)
    executor = SimulatedExecutor(CostConfig())
    portfolio = Portfolio(initial_cash=100_000.0)
    risk = MaxPositionCount(limit=5)
    mesh = SignalMesh() if use_signals else None
    allocator = StrategyAllocator() if use_signals else None
    symbols = symbols or ["SPY"]

    clock = BacktestClock(start=start, end=end)
    last_scan_date: date | None = None
    current_regime = "normal"

    for timestamp, state in clock.tick():
        today = timestamp.date()

        # Daily signal update at market open
        if state == MarketState.GAP_OPEN:
            vix = provider.get_signal_data("VIXCLS", today)
            hy_spread = provider.get_signal_data("BAMLH0A0HYM2", today)
            yield_curve = provider.get_signal_data("T10Y2Y", today)
            if mesh is not None:
                if vix is not None:
                    current_regime = update_vix_regime(mesh, vix, timestamp)
                if hy_spread is not None:
                    update_hy_spread(mesh, hy_spread, timestamp)
                if yield_curve is not None:
                    update_yield_curve(mesh, yield_curve, timestamp)

        # Check exits every tick
        for sym in list(portfolio.positions.keys()):
            pos = portfolio.positions[sym]
            underlying = sym.split("_")[0]
            chain = provider.get_chain(underlying, today, min_dte=0, max_dte=60)
            if len(chain) == 0:
                continue
            legs = pos.legs
            sc = chain.filter((chain["strike"] == legs["short_call"]) & (chain["type"] == "call"))
            sp = chain.filter((chain["strike"] == legs["short_put"]) & (chain["type"] == "put"))
            lc = chain.filter((chain["strike"] == legs["long_call"]) & (chain["type"] == "call"))
            lp = chain.filter((chain["strike"] == legs["long_put"]) & (chain["type"] == "put"))
            if any(len(x) == 0 for x in [sc, sp, lc, lp]):
                continue
            debit = max(0.0, (sc["bid"][0] + sp["bid"][0]) - (lc["ask"][0] + lp["ask"][0]))
            pos.current_debit = debit
            trigger = check_exit(pos.entry_credit, debit, cfg)
            if trigger:
                order = Order(sym, OrderSide.BUY, pos.contracts, [
                    {"type": "buy", "bid": sc["bid"][0], "ask": sc["ask"][0]},
                    {"type": "buy", "bid": sp["bid"][0], "ask": sp["ask"][0]},
                    {"type": "sell", "bid": lc["bid"][0], "ask": lc["ask"][0]},
                    {"type": "sell", "bid": lp["bid"][0], "ask": lp["ask"][0]},
                ])
                fill = executor.execute(order)
                portfolio.close_position(sym, fill.net_debit, trigger, timestamp)
                portfolio.cash -= fill.commission
                log.info("%s: closed %s reason=%s debit=%.2f", today, sym, trigger, fill.net_debit)

        # Scan for new candidates once per day at gap open
        if state == MarketState.GAP_OPEN and today != last_scan_date:
            last_scan_date = today
            if not risk.allows(portfolio):
                continue

            # Hard gate: skip entirely in extreme conditions
            if use_signals and should_hard_gate(vix, hy_spread):
                log.info("%s: HARD GATE — VIX=%.1f HY=%.2f, skipping",
                         today, vix or 0, hy_spread or 0)
                continue

            # Earnings-aware tiering: only scan stocks in the earnings window
            if use_earnings:
                stock_syms = [s for s in symbols if not is_etf(s)]
                etf_syms = [s for s in symbols if is_etf(s)]
                earnings_syms = []
                for s in stock_syms:
                    days = provider.days_until_earnings(s, today)
                    if days is not None and EARNINGS_WINDOW_START <= days <= EARNINGS_WINDOW_END:
                        earnings_syms.append(s)
                scan_list = earnings_syms or etf_syms
            else:
                scan_list = symbols

            for symbol in scan_list:
                # Per-symbol PEAD signal update
                if mesh is not None:
                    pead_data = provider.last_earnings_surprise(symbol, today)
                    if pead_data:
                        update_pead(mesh, pead_data[0], pead_data[1], timestamp)

                # Mesh gate + sizing (evaluated per-symbol now that PEAD is loaded)
                if allocator and mesh and not allocator.should_trade(mesh, timestamp, current_regime):
                    continue
                scale = 1.0
                if allocator and mesh:
                    scale = allocator.position_scale(mesh, timestamp, current_regime)

                chain = provider.get_chain(symbol, today, cfg.min_dte, cfg.max_dte)
                if len(chain) == 0:
                    continue
                candidate = select_ic_candidate(chain, cfg)
                if candidate is None:
                    continue
                pos_key = f"{symbol}_IC_{today.isoformat()}"
                if pos_key in portfolio.positions:
                    continue
                tier = "earnings" if use_earnings and not is_etf(symbol) else "vrp"
                scaled_credit = candidate["credit"] * scale
                scaled_max_loss = candidate["max_loss"] * scale
                portfolio.open_position(
                    pos_key, scaled_credit, scaled_max_loss, 1,
                    candidate, timestamp,
                )
                portfolio.cash -= CostConfig().commission_per_contract * 4
                log.info("%s: opened %s [%s] credit=$%.2f scale=%.2f pead=%s",
                         today, pos_key, tier, candidate["credit"], scale,
                         f"{pead_data[0]:+.1f}%/{pead_data[1]}d" if mesh and pead_data else "n/a")

        # Record equity at 15:45
        if state == MarketState.MARKET_OPEN and timestamp.hour == 15 and timestamp.minute == 45:
            portfolio.record_equity(timestamp)

    stats = compute_stats(portfolio)
    return stats


def main():
    parser = argparse.ArgumentParser(description="Run IC backtest")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, default="2023-01-01")
    parser.add_argument("--end", type=date.fromisoformat, default="2024-12-31")
    parser.add_argument("--symbols", nargs="+", default=["SPY"])
    parser.add_argument("--use-signals", action="store_true")
    parser.add_argument("--use-earnings", action="store_true",
                        help="Only scan stocks in the earnings window (3-14 days before earnings)")
    parser.add_argument("--fixed-otm", type=float, default=None,
                        help="Use fixed %% OTM strikes instead of delta (e.g. 0.05 = 5%%)")
    args = parser.parse_args()
    ic_config = ICConfig()
    if args.fixed_otm is not None:
        ic_config = ICConfig(use_fixed_otm=True, short_otm_pct=args.fixed_otm)
    stats = run_backtest(data_dir=args.data_dir, start=args.start, end=args.end,
                         ic_config=ic_config, symbols=args.symbols,
                         use_signals=args.use_signals, use_earnings=args.use_earnings)
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
