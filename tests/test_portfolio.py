import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from kotorid.portfolio.portfolio import Portfolio, Position, TradeRecord

ET = ZoneInfo("America/New_York")


def test_initial_state():
    p = Portfolio(initial_cash=100_000.0)
    assert p.cash == 100_000.0
    assert p.positions == {}
    assert p.trade_log == []
    assert p.equity_curve == []


def test_open_position():
    p = Portfolio(initial_cash=100_000.0)
    ts = datetime(2024, 1, 2, 10, 0, tzinfo=ET)
    p.open_position(
        symbol="SPY_IC_20240108", entry_credit=1.00, max_loss=400.0,
        contracts=1, legs={"short_call": 480, "long_call": 485, "short_put": 465, "long_put": 460},
        timestamp=ts,
    )
    assert "SPY_IC_20240108" in p.positions
    pos = p.positions["SPY_IC_20240108"]
    assert pos.entry_credit == 1.00
    assert pos.contracts == 1
    assert p.cash == 100_100.0  # +credit*100*contracts


def test_close_position():
    p = Portfolio(initial_cash=100_000.0)
    ts1 = datetime(2024, 1, 2, 10, 0, tzinfo=ET)
    p.open_position("IC1", 1.00, 400.0, 1, {}, ts1)
    ts2 = datetime(2024, 1, 5, 14, 0, tzinfo=ET)
    p.close_position("IC1", exit_debit=0.50, reason="profit_target", timestamp=ts2)
    assert "IC1" not in p.positions
    assert p.cash == 100_050.0  # 100000 + 100 credit - 50 debit
    assert len(p.trade_log) == 1
    assert p.trade_log[0].realized_pnl == 50.0


def test_record_equity():
    p = Portfolio(initial_cash=100_000.0)
    ts = datetime(2024, 1, 2, 16, 0, tzinfo=ET)
    p.record_equity(ts)
    assert len(p.equity_curve) == 1
    assert p.equity_curve[0] == (ts, 100_000.0)


def test_max_drawdown_no_trades():
    p = Portfolio(initial_cash=100_000.0)
    assert p.max_drawdown() == 0.0
