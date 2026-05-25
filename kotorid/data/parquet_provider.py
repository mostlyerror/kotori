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
        self._earnings_cache: dict[str, list[date]] = {}
        self._earnings_detail: pl.DataFrame | None = None

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

    def _load_earnings(self, symbol: str) -> list[date]:
        if symbol not in self._earnings_cache:
            path = self.daily_dir / "events" / "earnings.parquet"
            if not path.exists():
                self._earnings_cache[symbol] = []
                return []
            df = pl.read_parquet(path)
            sym_dates = df.filter(pl.col("symbol") == symbol).sort("earnings_date")
            self._earnings_cache[symbol] = [
                d if isinstance(d, date) else d.date()
                for d in sym_dates["earnings_date"].to_list()
            ]
        return self._earnings_cache[symbol]

    def days_until_earnings(self, symbol: str, as_of: date) -> int | None:
        dates = self._load_earnings(symbol)
        if not dates:
            return None
        for d in dates:
            if d >= as_of:
                return (d - as_of).days
        return None

    def _load_earnings_detail(self) -> pl.DataFrame:
        if self._earnings_detail is None:
            path = self.daily_dir / "events" / "earnings.parquet"
            if path.exists():
                self._earnings_detail = pl.read_parquet(path)
            else:
                self._earnings_detail = pl.DataFrame({
                    "symbol": [], "earnings_date": [],
                    "surprise_pct": [], "reported_eps": [],
                })
        return self._earnings_detail

    def last_earnings_surprise(self, symbol: str, as_of: date) -> tuple[float, int] | None:
        df = self._load_earnings_detail()
        past = df.filter(
            (pl.col("symbol") == symbol)
            & (pl.col("earnings_date") < as_of)
            & (pl.col("surprise_pct").is_not_null())
        ).sort("earnings_date", descending=True)
        if len(past) == 0:
            return None
        row = past.row(0, named=True)
        days_since = (as_of - row["earnings_date"]).days
        return (row["surprise_pct"], days_since)
