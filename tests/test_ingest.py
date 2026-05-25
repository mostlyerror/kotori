import os
import polars as pl
import pytest
from pathlib import Path
from kotorid.data.ingest import fetch_fred_series, ingest_fred_signals

requires_fred = pytest.mark.skipif(
    not os.getenv("FRED_API_KEY"),
    reason="FRED_API_KEY env var not set",
)

@requires_fred
def test_fetch_fred_series_returns_dataframe():
    df = fetch_fred_series("VIXCLS", start="2024-01-01", end="2024-01-31")
    assert isinstance(df, pl.DataFrame)
    assert "date" in df.columns
    assert "value" in df.columns
    assert len(df) > 0

@requires_fred
def test_ingest_fred_signals_writes_parquet(tmp_path):
    out_dir = tmp_path / "daily" / "signals"
    ingest_fred_signals(out_dir=out_dir, start="2024-01-01", end="2024-01-31", series=["VIXCLS"])
    vix_path = out_dir / "VIXCLS.parquet"
    assert vix_path.exists()
    df = pl.read_parquet(vix_path)
    assert len(df) > 0
    assert "date" in df.columns
    assert "value" in df.columns
