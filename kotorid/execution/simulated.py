from __future__ import annotations
from kotorid.execution.executor import Order, OrderSide, Fill, OrderExecutor
from kotorid.execution.cost import CostConfig


class SimulatedExecutor(OrderExecutor):
    def __init__(self, cost_config: CostConfig | None = None):
        self.cost = cost_config or CostConfig()

    def execute(self, order: Order) -> Fill:
        credit = 0.0
        debit = 0.0
        for leg in order.legs:
            if leg["type"] == "sell":
                credit += leg["bid"]
            else:
                debit += leg["ask"]
        commission = self.cost.commission_per_contract * len(order.legs) * order.contracts
        if order.side == OrderSide.SELL:
            return Fill(symbol=order.symbol, filled=True, net_credit=credit - debit, commission=commission)
        else:
            return Fill(symbol=order.symbol, filled=True, net_debit=debit - credit, commission=commission)
