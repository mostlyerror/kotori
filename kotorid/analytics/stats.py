from __future__ import annotations
import math
from kotorid.portfolio.portfolio import Portfolio


def compute_stats(portfolio: Portfolio) -> dict:
    trades = portfolio.trade_log
    if not trades:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "total_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": None,
            "max_drawdown": 0.0,
            "sharpe": None,
        }

    wins = [t for t in trades if t.realized_pnl > 0]
    losses = [t for t in trades if t.realized_pnl <= 0]
    total_pnl = sum(t.realized_pnl for t in trades)
    gross_profit = sum(t.realized_pnl for t in wins) if wins else 0.0
    gross_loss = abs(sum(t.realized_pnl for t in losses)) if losses else 0.0

    pnls = [t.realized_pnl for t in trades]
    mean_pnl = total_pnl / len(trades)
    std_pnl = (
        math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls))
        if len(pnls) > 1
        else 0.0
    )
    sharpe = (mean_pnl / std_pnl) if std_pnl > 0 else None

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades),
        "total_pnl": total_pnl,
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": -(gross_loss / len(losses)) if losses else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "max_drawdown": portfolio.max_drawdown(),
        "sharpe": sharpe,
    }
