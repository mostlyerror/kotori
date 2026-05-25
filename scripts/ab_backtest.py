"""Run A/B backtest: baseline (no signals) vs signal mesh.

Usage:
    python3 scripts/ab_backtest.py --data-dir data/daily --symbols AAPL NVDA
    python3 scripts/ab_backtest.py --data-dir data/daily --use-earnings --fixed-otm 0.05
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from scripts.run_backtest import run_backtest
from kotorid.strategy.config import ICConfig
from kotorid.analytics.compare import compare_runs, format_comparison
from kotorid.portfolio.portfolio import Portfolio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="A/B backtest: baseline vs signal mesh")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, default="2024-06-01")
    parser.add_argument("--end", type=date.fromisoformat, default="2026-05-01")
    parser.add_argument("--symbols", nargs="+", default=["AAPL"])
    parser.add_argument("--use-earnings", action="store_true")
    parser.add_argument("--fixed-otm", type=float, default=0.05)
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of table")
    args = parser.parse_args()

    ic_config = ICConfig(use_fixed_otm=True, short_otm_pct=args.fixed_otm)

    log.info("=== ARM A: Baseline (no signals) ===")
    stats_a = run_backtest(
        data_dir=args.data_dir, start=args.start, end=args.end,
        ic_config=ic_config, symbols=args.symbols,
        use_signals=False, use_earnings=args.use_earnings,
    )

    log.info("\n=== ARM B: Signal Mesh (VIX + HY + Yield Curve) ===")
    stats_b = run_backtest(
        data_dir=args.data_dir, start=args.start, end=args.end,
        ic_config=ic_config, symbols=args.symbols,
        use_signals=True, use_earnings=args.use_earnings,
    )

    # compare_runs expects Portfolio objects but we only have stats dicts
    # For now, print side-by-side directly
    print("\n")
    print("=" * 62)
    print("A/B COMPARISON")
    print("=" * 62)

    def _fmt(v, fmt=".2f"):
        return "—" if v is None else f"{v:{fmt}}"

    def _pct(v):
        return "—" if v is None else f"{v:.1%}"

    def _sign(v, fmt="+.2f"):
        return "—" if v is None else f"{v:{fmt}}"

    def _delta(key):
        a, b = stats_a.get(key), stats_b.get(key)
        if a is None or b is None:
            return None
        return b - a

    print(f"{'':20s} {'Baseline':>14s} {'Signals':>14s} {'Delta':>10s}")
    print("─" * 62)
    print(f"{'Trades':20s} {stats_a['total_trades']:>14d} {stats_b['total_trades']:>14d} {_sign(_delta('total_trades'), '+d'):>10s}")
    print(f"{'Win Rate':20s} {_pct(stats_a['win_rate']):>14s} {_pct(stats_b['win_rate']):>14s} {_sign(_delta('win_rate'), '+.1%'):>10s}")
    print(f"{'Total P&L':20s} {'$'+_fmt(stats_a['total_pnl']):>14s} {'$'+_fmt(stats_b['total_pnl']):>14s} {'$'+_sign(_delta('total_pnl')):>10s}")
    print(f"{'Sharpe':20s} {_fmt(stats_a['sharpe']):>14s} {_fmt(stats_b['sharpe']):>14s} {_sign(_delta('sharpe')):>10s}")
    print(f"{'Max Drawdown':20s} {_pct(stats_a['max_drawdown']):>14s} {_pct(stats_b['max_drawdown']):>14s} {_sign(_delta('max_drawdown'), '+.1%'):>10s}")
    print(f"{'Profit Factor':20s} {_fmt(stats_a['profit_factor']):>14s} {_fmt(stats_b['profit_factor']):>14s} {_sign(_delta('profit_factor')):>10s}")
    print(f"{'Avg Win':20s} {'$'+_fmt(stats_a['avg_win']):>14s} {'$'+_fmt(stats_b['avg_win']):>14s}")
    print(f"{'Avg Loss':20s} {'$'+_fmt(stats_a['avg_loss']):>14s} {'$'+_fmt(stats_b['avg_loss']):>14s}")

    if args.json:
        print("\n" + json.dumps({"baseline": stats_a, "signals": stats_b}, indent=2, default=str))


if __name__ == "__main__":
    main()
