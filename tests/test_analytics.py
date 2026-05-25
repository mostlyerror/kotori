import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from kotorid.analytics.stats import compute_stats
from kotorid.analytics.compare import compare_runs
from kotorid.portfolio.portfolio import Portfolio, TradeRecord

ET = ZoneInfo("America/New_York")
T0 = datetime(2024, 1, 2, 10, 0, tzinfo=ET)


def _make_portfolio(trades: list[tuple[float, str]]) -> Portfolio:
    p = Portfolio(initial_cash=100_000.0)
    for i, (pnl, reason) in enumerate(trades):
        credit = 1.00
        debit = credit - (pnl / 100)
        ts_open = T0 + timedelta(days=i * 7)
        ts_close = ts_open + timedelta(days=5)
        p.trade_log.append(TradeRecord(
            symbol=f"IC_{i}", entry_credit=credit, exit_debit=debit,
            contracts=1, realized_pnl=pnl, reason=reason,
            opened_at=ts_open, closed_at=ts_close,
        ))
        p.cash += pnl
        p.record_equity(ts_close)
    return p


def test_compute_stats_basic():
    p = _make_portfolio([(50.0, "profit_target"), (80.0, "profit_target"), (-100.0, "stop_loss"), (30.0, "profit_target")])
    stats = compute_stats(p)
    assert stats["total_trades"] == 4
    assert stats["wins"] == 3
    assert stats["losses"] == 1
    assert stats["win_rate"] == pytest.approx(0.75)
    assert stats["total_pnl"] == pytest.approx(60.0)
    assert stats["avg_win"] == pytest.approx(160.0 / 3)
    assert stats["avg_loss"] == pytest.approx(-100.0)


def test_compute_stats_empty():
    p = Portfolio(initial_cash=100_000.0)
    stats = compute_stats(p)
    assert stats["total_trades"] == 0
    assert stats["win_rate"] is None


def test_compare_runs():
    baseline = _make_portfolio([(50.0, "profit_target"), (-100.0, "stop_loss")])
    overlay = _make_portfolio([(50.0, "profit_target"), (30.0, "profit_target")])
    result = compare_runs(baseline, overlay, labels=("baseline", "vix_filter"))
    assert result["baseline"]["total_pnl"] == pytest.approx(-50.0)
    assert result["vix_filter"]["total_pnl"] == pytest.approx(80.0)
    assert result["improvement_pnl"] == pytest.approx(130.0)
