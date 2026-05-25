from __future__ import annotations
from datetime import date
from pathlib import Path
import polars as pl
from kotorid.data.provider import DataProvider

class ParquetProvider(DataProvider):
    def __init__(self, daily_dir: Path | str):
        self.daily_dir = Path(daily_dir)
        self._options_cache: dict[str, pl.DataFrame] = {}
        self._signal_cache: dict[str, pl.DataFrame] = {}

    def _load_options(self, symbol: str) -> pl.DataFrame:
        if symbol not in self._options_cache:
            path = self.daily_dir / "options" / f"{symbol}.parquet"
            self._options_cache[symbol] = pl.read_parquet(path)
        return self._options_cache[symbol]

    def _load_signal(self, name: str) -> pl.DataFrame:
        if name not in self._signal_cache:
            path = self.daily_dir / "signals" / f"{name}.parquet"
            self._signal_cache[name] = pl.read_parquet(path)
        return self._signal_cache[name]

    def get_chain(self, underlying: str, as_of: date, min_dte: int, max_dte: int) -> pl.DataFrame:
        df = self._load_options(underlying)
        return df.filter(
            (pl.col("date") == as_of)
            & ((pl.col("expiration") - pl.col("date")).dt.total_days().is_between(min_dte, max_dte))
        )

    def get_signal_data(self, signal_name: str, as_of: date) -> float | None:
        try:
            df = self._load_signal(signal_name)
        except FileNotFoundError:
            return None
        row = df.filter(pl.col("date") == as_of)
        if len(row) == 0:
            return None
        return row["value"][0]
