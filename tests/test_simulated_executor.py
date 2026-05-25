import pytest
from kotorid.execution.executor import Order, OrderSide
from kotorid.execution.cost import CostConfig
from kotorid.execution.simulated import SimulatedExecutor


def test_sell_fills_at_bid():
    executor = SimulatedExecutor(CostConfig(commission_per_contract=0))
    order = Order(symbol="SPY_IC", side=OrderSide.SELL, contracts=1, legs=[
        {"type": "sell", "bid": 1.20, "ask": 1.40},
        {"type": "sell", "bid": 0.80, "ask": 0.95},
        {"type": "buy", "bid": 0.10, "ask": 0.20},
        {"type": "buy", "bid": 0.05, "ask": 0.15},
    ])
    fill = executor.execute(order)
    # Net credit = (1.20 + 0.80) - (0.20 + 0.15) = 1.65
    assert fill.net_credit == pytest.approx(1.65)
    assert fill.filled is True


def test_commission_deducted():
    executor = SimulatedExecutor(CostConfig(commission_per_contract=0.65))
    order = Order(symbol="SPY_IC", side=OrderSide.SELL, contracts=1, legs=[
        {"type": "sell", "bid": 1.00, "ask": 1.10},
        {"type": "sell", "bid": 0.50, "ask": 0.60},
        {"type": "buy", "bid": 0.05, "ask": 0.10},
        {"type": "buy", "bid": 0.03, "ask": 0.08},
    ])
    fill = executor.execute(order)
    assert fill.commission == pytest.approx(2.60)  # 4 legs × $0.65


def test_buy_to_close_fills_at_ask():
    executor = SimulatedExecutor(CostConfig(commission_per_contract=0))
    order = Order(symbol="SPY_IC", side=OrderSide.BUY, contracts=1, legs=[
        {"type": "buy", "bid": 0.50, "ask": 0.60},
        {"type": "buy", "bid": 0.30, "ask": 0.40},
        {"type": "sell", "bid": 0.02, "ask": 0.05},
        {"type": "sell", "bid": 0.01, "ask": 0.04},
    ])
    fill = executor.execute(order)
    # Net debit = (0.60 + 0.40) - (0.02 + 0.01) = 0.97
    assert fill.net_debit == pytest.approx(0.97)
