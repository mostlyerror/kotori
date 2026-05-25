from __future__ import annotations
from kotorid.portfolio.portfolio import Portfolio


class MaxPositionCount:
    def __init__(self, limit: int = 5):
        self.limit = limit

    def allows(self, portfolio: Portfolio) -> bool:
        return len(portfolio.positions) < self.limit


class MaxDrawdown:
    def __init__(self, max_dd: float = 0.10):
        self.max_dd = max_dd

    def allows(self, portfolio: Portfolio) -> bool:
        return portfolio.max_drawdown() < self.max_dd
