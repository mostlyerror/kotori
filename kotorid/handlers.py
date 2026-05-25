from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum, auto
from kotorid.clock import MarketState


class Frequency(Enum):
    EVERY_TICK = auto()
    DAILY_OPEN = auto()
    DAILY_CLOSE = auto()


class Handler(ABC):
    def __init__(self, frequency: Frequency = Frequency.EVERY_TICK):
        self.frequency = frequency

    def should_run(self, timestamp: datetime, state: MarketState, last_date: object) -> bool:
        if self.frequency == Frequency.EVERY_TICK:
            return True
        if self.frequency == Frequency.DAILY_OPEN:
            return state == MarketState.GAP_OPEN
        if self.frequency == Frequency.DAILY_CLOSE:
            return state == MarketState.MARKET_OPEN and timestamp.hour == 16 and timestamp.minute == 0
        return False

    @abstractmethod
    async def handle(self, timestamp: datetime, state: MarketState, context: dict) -> None: ...
