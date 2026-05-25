from __future__ import annotations
from kotorid.analytics.stats import compute_stats
from kotorid.portfolio.portfolio import Portfolio


def compare_runs(
    baseline: Portfolio,
    overlay: Portfolio,
    labels: tuple[str, str] = ("baseline", "overlay"),
) -> dict:
    baseline_stats = compute_stats(baseline)
    overlay_stats = compute_stats(overlay)
    return {
        labels[0]: baseline_stats,
        labels[1]: overlay_stats,
        "improvement_pnl": overlay_stats["total_pnl"] - baseline_stats["total_pnl"],
        "improvement_win_rate": (overlay_stats["win_rate"] or 0) - (baseline_stats["win_rate"] or 0),
    }
