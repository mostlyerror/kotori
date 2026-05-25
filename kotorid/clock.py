from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from enum import Enum, auto
from typing import Generator
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

_HOLIDAYS: set[date] = {
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19),
    date(2024, 3, 29), date(2024, 5, 27), date(2024, 6, 19),
    date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 28),
    date(2024, 12, 25),
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 12, 25),
}


class MarketState(Enum):
    GAP_OPEN = auto()
    MARKET_OPEN = auto()
    MARKET_CLOSE = auto()


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _HOLIDAYS


class BacktestClock:
    def __init__(self, start: date, end: date, step: timedelta = timedelta(minutes=15)):
        self.start = start
        self.end = end
        self.step = step

    def tick(self) -> Generator[tuple[datetime, MarketState], None, None]:
        current_date = self.start
        while current_date <= self.end:
            if not is_trading_day(current_date):
                current_date += timedelta(days=1)
                continue
            open_dt = datetime.combine(current_date, MARKET_OPEN, tzinfo=ET)
            close_dt = datetime.combine(current_date, MARKET_CLOSE, tzinfo=ET)
            yield (open_dt, MarketState.GAP_OPEN)
            ts = open_dt + self.step
            while ts <= close_dt:
                yield (ts, MarketState.MARKET_OPEN)
                ts += self.step
            current_date += timedelta(days=1)
