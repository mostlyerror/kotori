from __future__ import annotations
from kotorid.analytics.stats import compute_stats
from kotorid.portfolio.portfolio import Portfolio


def compare_runs(
    baseline: Portfolio,
    overlay: Portfolio,
    labels: tuple[str, str] = ("baseline", "overlay"),
) -> dict:
    b = compute_stats(baseline)
    o = compute_stats(overlay)

    def _delta(key: str) -> float | None:
        bv, ov = b.get(key), o.get(key)
        if bv is None or ov is None:
            return None
        return ov - bv

    return {
        labels[0]: b,
        labels[1]: o,
        "delta": {
            "pnl": _delta("total_pnl"),
            "win_rate": _delta("win_rate"),
            "sharpe": _delta("sharpe"),
            "max_drawdown": _delta("max_drawdown"),
            "profit_factor": _delta("profit_factor"),
            "trades": _delta("total_trades"),
        },
    }


def format_comparison(result: dict) -> str:
    """Human-readable A/B comparison table."""
    labels = [k for k in result if k != "delta"]
    if len(labels) != 2:
        return str(result)
    a_label, b_label = labels
    a, b = result[a_label], result[b_label]
    delta = result["delta"]

    def _fmt(v, fmt=".2f") -> str:
        if v is None:
            return "—"
        return f"{v:{fmt}}"

    def _pct(v) -> str:
        if v is None:
            return "—"
        return f"{v:.1%}"

    def _sign(v, fmt=".2f") -> str:
        if v is None:
            return "—"
        return f"{v:+{fmt}}"

    lines = [
        f"{'':20s} {a_label:>14s} {b_label:>14s} {'delta':>10s}",
        "─" * 62,
        f"{'Trades':20s} {a['total_trades']:>14d} {b['total_trades']:>14d} {_sign(delta['trades'], 'd'):>10s}",
        f"{'Win Rate':20s} {_pct(a['win_rate']):>14s} {_pct(b['win_rate']):>14s} {_sign(delta['win_rate'], '.1%'):>10s}",
        f"{'Total P&L':20s} {'$'+_fmt(a['total_pnl']):>14s} {'$'+_fmt(b['total_pnl']):>14s} {'$'+_sign(delta['pnl']):>10s}",
        f"{'Sharpe':20s} {_fmt(a['sharpe']):>14s} {_fmt(b['sharpe']):>14s} {_sign(delta['sharpe']):>10s}",
        f"{'Max Drawdown':20s} {_pct(a['max_drawdown']):>14s} {_pct(b['max_drawdown']):>14s} {_sign(delta['max_drawdown'], '.1%'):>10s}",
        f"{'Profit Factor':20s} {_fmt(a['profit_factor']):>14s} {_fmt(b['profit_factor']):>14s} {_sign(delta['profit_factor']):>10s}",
        f"{'Avg Win':20s} {'$'+_fmt(a['avg_win']):>14s} {'$'+_fmt(b['avg_win']):>14s}",
        f"{'Avg Loss':20s} {'$'+_fmt(a['avg_loss']):>14s} {'$'+_fmt(b['avg_loss']):>14s}",
    ]
    return "\n".join(lines)
