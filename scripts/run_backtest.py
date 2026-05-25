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
from kotorid.signals.regime import update_vix_regime
from kotorid.strategy.allocator import StrategyAllocator
from kotorid.analytics.stats import compute_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def run_backtest(
    data_dir: Path,
    start: date,
    end: date,
    ic_config: ICConfig | None = None,
    use_signals: bool = False,
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
            if mesh is not None:
                vix = provider.get_signal_data("VIXCLS", today)
                if vix is not None:
                    current_regime = update_vix_regime(mesh, vix, timestamp)

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
            if allocator and mesh and not allocator.should_trade(mesh, timestamp, current_regime):
                continue
            for symbol in symbols:
                chain = provider.get_chain(symbol, today, cfg.min_dte, cfg.max_dte)
                if len(chain) == 0:
                    continue
                candidate = select_ic_candidate(chain, cfg)
                if candidate is None:
                    continue
                pos_key = f"{symbol}_IC_{today.isoformat()}"
                if pos_key in portfolio.positions:
                    continue
                portfolio.open_position(
                    pos_key, candidate["credit"], candidate["max_loss"], 1,
                    candidate, timestamp,
                )
                portfolio.cash -= CostConfig().commission_per_contract * 4
                log.info("%s: opened %s credit=$%.2f", today, pos_key, candidate["credit"])

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
    args = parser.parse_args()
    stats = run_backtest(data_dir=args.data_dir, start=args.start, end=args.end,
                         symbols=args.symbols, use_signals=args.use_signals)
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
