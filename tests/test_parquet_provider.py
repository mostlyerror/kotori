import polars as pl
import pytest
from datetime import date
from pathlib import Path
from kotorid.data.parquet_provider import ParquetProvider

@pytest.fixture
def sample_data(tmp_path):
    options_dir = tmp_path / "daily" / "options"
    options_dir.mkdir(parents=True)
    df = pl.DataFrame({
        "date": [date(2024, 1, 2)] * 8,
        "symbol": ["SPY"] * 8,
        "type": ["call", "call", "call", "call", "put", "put", "put", "put"],
        "strike": [470.0, 475.0, 480.0, 485.0, 460.0, 465.0, 470.0, 475.0],
        "expiration": [date(2024, 1, 8)] * 8,
        "bid": [8.50, 4.20, 1.30, 0.40, 0.35, 0.80, 2.10, 5.00],
        "ask": [8.70, 4.40, 1.50, 0.55, 0.45, 0.95, 2.30, 5.20],
        "delta": [0.85, 0.60, 0.25, 0.08, -0.08, -0.20, -0.55, -0.82],
        "gamma": [0.01] * 8,
        "theta": [-0.10] * 8,
        "vega": [0.15] * 8,
        "implied_volatility": [0.18] * 8,
        "volume": [1000] * 8,
        "open_interest": [5000] * 8,
    })
    df.write_parquet(options_dir / "SPY.parquet")
    signals_dir = tmp_path / "daily" / "signals"
    signals_dir.mkdir(parents=True)
    vix_df = pl.DataFrame({"date": [date(2024, 1, 2), date(2024, 1, 3)], "value": [13.20, 14.10]})
    vix_df.write_parquet(signals_dir / "VIXCLS.parquet")
    return tmp_path

def test_get_chain(sample_data):
    provider = ParquetProvider(sample_data / "daily")
    chain = provider.get_chain("SPY", date(2024, 1, 2), min_dte=5, max_dte=10)
    assert len(chain) == 8
    assert "strike" in chain.columns
    assert "delta" in chain.columns

def test_get_chain_filters_by_dte(sample_data):
    provider = ParquetProvider(sample_data / "daily")
    chain = provider.get_chain("SPY", date(2024, 1, 2), min_dte=5, max_dte=10)
    assert len(chain) == 8
    chain2 = provider.get_chain("SPY", date(2024, 1, 2), min_dte=1, max_dte=3)
    assert len(chain2) == 0

def test_get_signal_data(sample_data):
    provider = ParquetProvider(sample_data / "daily")
    vix = provider.get_signal_data("VIXCLS", date(2024, 1, 2))
    assert vix == pytest.approx(13.20)

def test_get_signal_data_missing_date(sample_data):
    provider = ParquetProvider(sample_data / "daily")
    vix = provider.get_signal_data("VIXCLS", date(2024, 6, 1))
    assert vix is None
