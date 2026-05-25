"""Test ParquetProvider earnings integration."""
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from kotorid.data.parquet_provider import ParquetProvider


@pytest.fixture
def provider_with_earnings(tmp_path: Path) -> ParquetProvider:
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    df = pl.DataFrame({
        "symbol": ["AAPL", "AAPL", "AAPL", "NVDA", "NVDA"],
        "earnings_date": [
            date(2024, 1, 25), date(2024, 4, 25), date(2024, 7, 25),
            date(2024, 2, 21), date(2024, 5, 22),
        ],
        "eps_estimate": [2.1, 1.5, 1.4, 4.5, 5.0],
        "reported_eps": [2.18, 1.53, None, 4.93, None],
        "surprise_pct": [3.8, 2.0, None, 9.5, None],
    })
    df.write_parquet(events_dir / "earnings.parquet")
    return ParquetProvider(tmp_path)


def test_days_until_earnings_exact(provider_with_earnings):
    days = provider_with_earnings.days_until_earnings("AAPL", date(2024, 1, 15))
    assert days == 10  # Jan 25 - Jan 15


def test_days_until_earnings_on_date(provider_with_earnings):
    days = provider_with_earnings.days_until_earnings("AAPL", date(2024, 1, 25))
    assert days == 0


def test_days_until_earnings_between_events(provider_with_earnings):
    days = provider_with_earnings.days_until_earnings("AAPL", date(2024, 3, 1))
    assert days == 55  # Apr 25 - Mar 1


def test_days_until_earnings_past_all(provider_with_earnings):
    days = provider_with_earnings.days_until_earnings("AAPL", date(2024, 8, 1))
    assert days is None


def test_days_until_earnings_unknown_symbol(provider_with_earnings):
    days = provider_with_earnings.days_until_earnings("ZZZZ", date(2024, 1, 15))
    assert days is None


def test_days_until_earnings_no_file(tmp_path: Path):
    provider = ParquetProvider(tmp_path)
    assert provider.days_until_earnings("AAPL", date(2024, 1, 15)) is None
