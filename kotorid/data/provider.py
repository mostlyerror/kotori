from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date
import polars as pl

class DataProvider(ABC):
    @abstractmethod
    def get_chain(self, underlying: str, as_of: date, min_dte: int, max_dte: int) -> pl.DataFrame: ...

    @abstractmethod
    def get_signal_data(self, signal_name: str, as_of: date) -> float | None: ...

    def days_until_earnings(self, symbol: str, as_of: date) -> int | None:
        """Days until next earnings for symbol. Returns None if unknown."""
        return None

    def last_earnings_surprise(self, symbol: str, as_of: date) -> tuple[float, int] | None:
        """Most recent past earnings surprise for symbol.

        Returns (surprise_pct, days_since) or None if no past earnings.
        """
        return None
