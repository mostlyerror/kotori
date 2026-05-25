from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto


class OrderSide(Enum):
    BUY = auto()
    SELL = auto()


@dataclass
class Order:
    symbol: str
    side: OrderSide
    contracts: int
    legs: list[dict]


@dataclass
class Fill:
    symbol: str
    filled: bool
    net_credit: float = 0.0
    net_debit: float = 0.0
    commission: float = 0.0

    @property
    def total_cost(self) -> float:
        return self.net_debit + self.commission - self.net_credit


class OrderExecutor(ABC):
    @abstractmethod
    def execute(self, order: Order) -> Fill: ...
