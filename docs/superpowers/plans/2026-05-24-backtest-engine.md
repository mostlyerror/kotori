# Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backtest engine that replays the IC strategy over historical daily options data to validate signal mesh overlays against a no-signal baseline.

**Architecture:** Simulation clock ticks through historical timestamps; registered handlers run in priority order each tick (data feed → signals → allocator → position manager → risk gate → executor). Same handler code runs in backtest and live — only the clock and data/execution backends differ.

**Tech Stack:** Python 3.13, polars (Parquet I/O), asyncpg (Postgres), scipy (Black-Scholes), pytest

**Spec:** `docs/specs/2026-05-24-backtest-engine-design.md`

---

## File Map

### New files

| File | Responsibility |
|------|---------------|
| `kotorid/strategy/config.py` | ICConfig dataclass — extracted from hardcoded constants |
| `kotorid/clock.py` | Clock ABC, BacktestClock, MarketState enum |
| `kotorid/handlers.py` | Handler ABC, Frequency declarations |
| `kotorid/engine.py` | Engine loop: clock.tick() → run handlers |
| `kotorid/data/provider.py` | DataProvider ABC |
| `kotorid/data/parquet_provider.py` | Read daily Parquet options data |
| `kotorid/data/ingest.py` | Download philippdubach dataset + FRED signals |
| `kotorid/execution/executor.py` | OrderExecutor ABC, Order/Fill dataclasses |
| `kotorid/execution/simulated.py` | SimulatedExecutor — fill at bid/ask + costs |
| `kotorid/execution/cost.py` | CostConfig dataclass |
| `kotorid/portfolio/portfolio.py` | Portfolio state: positions, cash, equity curve |
| `kotorid/portfolio/risk.py` | Risk gates: MaxDrawdown, MaxPositionCount |
| `kotorid/strategy/ic_strategy.py` | IC candidate selection + position management |
| `kotorid/strategy/allocator.py` | StrategyAllocator — reads mesh → trade decisions |
| `kotorid/signals/mesh.py` | SignalMesh state + exponential decay |
| `kotorid/signals/regime.py` | VixRegimeSignal handler (refactored regime_engine) |
| `kotorid/signals/iv_rank.py` | IvRankSignal handler (refactored iv_engine) |
| `kotorid/analytics/stats.py` | Sharpe, drawdown, win rate computation |
| `kotorid/analytics/compare.py` | A/B comparison: baseline vs with-signal |
| `tests/test_clock.py` | Clock tests |
| `tests/test_handlers.py` | Handler + engine tests |
| `tests/test_parquet_provider.py` | ParquetProvider tests |
| `tests/test_simulated_executor.py` | SimulatedExecutor tests |
| `tests/test_portfolio.py` | Portfolio tests |
| `tests/test_ic_strategy.py` | IC strategy handler tests |
| `tests/test_signal_mesh.py` | SignalMesh tests |
| `tests/test_analytics.py` | Analytics tests |
| `scripts/run_backtest.py` | CLI entrypoint to run a backtest |

### Existing files modified

| File | Change |
|------|--------|
| `pyproject.toml` | Add polars, scipy dependencies |
| `kotorid/strategy/__init__.py` | New package init |
| `kotorid/data/__init__.py` | New package init |
| `kotorid/execution/__init__.py` | New package init |
| `kotorid/portfolio/__init__.py` | New package init |
| `kotorid/signals/__init__.py` | New package init |
| `kotorid/analytics/__init__.py` | New package init |

---

### Task 1: Strategy Config + Dependencies

Extract hardcoded IC parameters into a dataclass. Add polars and scipy to deps. This is the foundation every other task imports.

**Files:**
- Create: `kotorid/strategy/__init__.py`
- Create: `kotorid/strategy/config.py`
- Create: `tests/test_strategy_config.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create package dirs**

```bash
mkdir -p kotorid/strategy kotorid/data kotorid/execution kotorid/portfolio kotorid/signals kotorid/analytics
touch kotorid/strategy/__init__.py kotorid/data/__init__.py kotorid/execution/__init__.py kotorid/portfolio/__init__.py kotorid/signals/__init__.py kotorid/analytics/__init__.py
```

- [ ] **Step 2: Add dependencies to pyproject.toml**

Add `polars` and `scipy` to the `dependencies` list in `pyproject.toml`:

```toml
dependencies = [
    "textual>=0.89",
    "asyncpg>=0.29",
    "httpx>=0.27",
    "apscheduler>=3.10",
    "anthropic>=0.30",
    "pytz>=2024.1",
    "python-dotenv>=1.0",
    "polars>=1.0",
    "scipy>=1.14",
]
```

Run: `uv sync`

- [ ] **Step 3: Write the test**

```python
# tests/test_strategy_config.py
from kotorid.strategy.config import ICConfig


def test_ic_config_defaults():
    cfg = ICConfig()
    assert cfg.target_delta == 0.16
    assert cfg.wing_width == 5.0
    assert cfg.min_dte == 5
    assert cfg.max_dte == 9
    assert cfg.min_credit_ratio == 0.20
    assert cfg.profit_target == 0.50
    assert cfg.stop_loss == 2.00


def test_ic_config_override():
    cfg = ICConfig(target_delta=0.20, wing_width=10.0)
    assert cfg.target_delta == 0.20
    assert cfg.wing_width == 10.0
    assert cfg.profit_target == 0.50  # unchanged default
```

- [ ] **Step 4: Run test — expect FAIL**

Run: `uv run python -m pytest tests/test_strategy_config.py -v`
Expected: `ModuleNotFoundError: No module named 'kotorid.strategy.config'`

- [ ] **Step 5: Implement ICConfig**

```python
# kotorid/strategy/config.py
from dataclasses import dataclass


@dataclass(frozen=True)
class ICConfig:
    target_delta: float = 0.16
    wing_width: float = 5.0
    min_dte: int = 5
    max_dte: int = 9
    min_credit_ratio: float = 0.20
    profit_target: float = 0.50
    stop_loss: float = 2.00
```

- [ ] **Step 6: Run test — expect PASS**

Run: `uv run python -m pytest tests/test_strategy_config.py -v`
Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add kotorid/strategy/ kotorid/data/ kotorid/execution/ kotorid/portfolio/ kotorid/signals/ kotorid/analytics/ tests/test_strategy_config.py pyproject.toml
git commit -m "add ICConfig dataclass and backtest package structure"
```

---

### Task 2: Clock

The simulation clock is the heartbeat of the engine. `BacktestClock` yields timestamps in 15-min steps, skipping non-market hours. It also injects a gap tick at each market open.

**Files:**
- Create: `kotorid/clock.py`
- Create: `tests/test_clock.py`

- [ ] **Step 1: Write clock tests**

```python
# tests/test_clock.py
from datetime import datetime, date
from zoneinfo import ZoneInfo

from kotorid.clock import BacktestClock, MarketState

ET = ZoneInfo("America/New_York")


def test_market_state_during_hours():
    clock = BacktestClock(
        start=date(2024, 1, 2),
        end=date(2024, 1, 2),
    )
    ticks = list(clock.tick())
    # First tick should be gap_open at 9:30 ET
    assert ticks[0][1] == MarketState.GAP_OPEN
    assert ticks[0][0].hour == 9 and ticks[0][0].minute == 30
    # Second tick is normal market_open
    assert ticks[1][1] == MarketState.MARKET_OPEN
    # Last tick should be at or before 16:00 ET
    last_ts, last_state = ticks[-1]
    assert last_ts.hour <= 16


def test_skips_weekends():
    clock = BacktestClock(
        start=date(2024, 1, 5),  # Friday
        end=date(2024, 1, 8),    # Monday
    )
    ticks = list(clock.tick())
    dates_seen = {t[0].date() for t in ticks}
    assert date(2024, 1, 6) not in dates_seen  # Saturday
    assert date(2024, 1, 7) not in dates_seen  # Sunday
    assert date(2024, 1, 5) in dates_seen
    assert date(2024, 1, 8) in dates_seen


def test_tick_count_single_day():
    clock = BacktestClock(
        start=date(2024, 1, 2),
        end=date(2024, 1, 2),
    )
    ticks = list(clock.tick())
    # 9:30 to 16:00 = 6.5 hours = 390 min / 15 = 26 ticks + 1 gap_open = 27
    assert len(ticks) == 27


def test_step_override():
    from datetime import timedelta
    clock = BacktestClock(
        start=date(2024, 1, 2),
        end=date(2024, 1, 2),
        step=timedelta(minutes=30),
    )
    ticks = list(clock.tick())
    # 390 min / 30 = 13 ticks + 1 gap_open = 14
    assert len(ticks) == 14
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run python -m pytest tests/test_clock.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement BacktestClock**

```python
# kotorid/clock.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from enum import Enum, auto
from typing import Generator
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# US market holidays (2024-2025). Extend as needed.
_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19),
    date(2024, 3, 29), date(2024, 5, 27), date(2024, 6, 19),
    date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 28),
    date(2024, 12, 25),
    # 2025
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
    def __init__(
        self,
        start: date,
        end: date,
        step: timedelta = timedelta(minutes=15),
    ):
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

            # Gap tick at market open
            yield (open_dt, MarketState.GAP_OPEN)

            # Regular ticks through the day
            ts = open_dt + self.step
            while ts <= close_dt:
                yield (ts, MarketState.MARKET_OPEN)
                ts += self.step

            current_date += timedelta(days=1)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_clock.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add kotorid/clock.py tests/test_clock.py
git commit -m "add BacktestClock with gap tick injection"
```

---

### Task 3: Handler Framework + Engine

The handler ABC, frequency declarations, and the engine loop that drives everything.

**Files:**
- Create: `kotorid/handlers.py`
- Create: `kotorid/engine.py`
- Create: `tests/test_handlers.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_handlers.py
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from kotorid.clock import BacktestClock, MarketState
from kotorid.engine import Engine
from kotorid.handlers import Handler, Frequency

ET = ZoneInfo("America/New_York")


class CountingHandler(Handler):
    def __init__(self, frequency: Frequency):
        super().__init__(frequency)
        self.call_count = 0

    async def handle(self, timestamp: datetime, state: MarketState, context: dict) -> None:
        self.call_count += 1


def test_every_tick_handler_runs_each_tick():
    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2))
    handler = CountingHandler(Frequency.EVERY_TICK)
    engine = Engine(clock, [handler])
    engine.run()
    expected_ticks = len(list(BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2)).tick()))
    assert handler.call_count == expected_ticks


def test_daily_handler_runs_once_per_day():
    clock = BacktestClock(
        start=date(2024, 1, 2),  # Tue
        end=date(2024, 1, 3),    # Wed
    )
    handler = CountingHandler(Frequency.DAILY_OPEN)
    engine = Engine(clock, [handler])
    engine.run()
    assert handler.call_count == 2  # once per trading day


def test_handler_priority_order():
    """Handlers run in the order they're registered."""
    call_order = []

    class OrderTracker(Handler):
        def __init__(self, name: str, frequency: Frequency):
            super().__init__(frequency)
            self.name = name
        async def handle(self, timestamp, state, context):
            call_order.append(self.name)

    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2))
    engine = Engine(clock, [
        OrderTracker("data", Frequency.EVERY_TICK),
        OrderTracker("signal", Frequency.EVERY_TICK),
        OrderTracker("allocator", Frequency.EVERY_TICK),
    ])
    engine.run()
    # Within each tick, order should be data → signal → allocator
    assert call_order[:3] == ["data", "signal", "allocator"]


def test_engine_passes_shared_context():
    """Handlers share a mutable context dict."""

    class Writer(Handler):
        async def handle(self, timestamp, state, context):
            context["written"] = True

    class Reader(Handler):
        def __init__(self):
            super().__init__(Frequency.EVERY_TICK)
            self.saw_written = False
        async def handle(self, timestamp, state, context):
            if context.get("written"):
                self.saw_written = True

    writer = Writer(Frequency.EVERY_TICK)
    reader = Reader()
    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2))
    engine = Engine(clock, [writer, reader])
    engine.run()
    assert reader.saw_written
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run python -m pytest tests/test_handlers.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement handlers.py**

```python
# kotorid/handlers.py
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
            return state == MarketState.MARKET_OPEN and timestamp.minute == 0 and timestamp.hour == 16
        return False

    @abstractmethod
    async def handle(self, timestamp: datetime, state: MarketState, context: dict) -> None: ...
```

- [ ] **Step 4: Implement engine.py**

```python
# kotorid/engine.py
from __future__ import annotations

import asyncio
from datetime import datetime

from kotorid.clock import BacktestClock, MarketState
from kotorid.handlers import Handler


class Engine:
    def __init__(self, clock: BacktestClock, handlers: list[Handler]):
        self.clock = clock
        self.handlers = handlers

    def run(self) -> dict:
        return asyncio.run(self._run_async())

    async def _run_async(self) -> dict:
        context: dict = {}
        for timestamp, state in self.clock.tick():
            for handler in self.handlers:
                if handler.should_run(timestamp, state, None):
                    await handler.handle(timestamp, state, context)
        return context
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_handlers.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add kotorid/handlers.py kotorid/engine.py tests/test_handlers.py
git commit -m "add handler framework and engine loop"
```

---

### Task 4: Data Ingest

Download the philippdubach options dataset and FRED VIX data to local Parquet files. This is a one-time CLI operation, not part of the engine loop.

**Files:**
- Create: `kotorid/data/ingest.py`
- Create: `tests/test_ingest.py`

- [ ] **Step 1: Write test for FRED VIX download**

The philippdubach download is too large to test in CI. Test the FRED ingest which is small and free.

```python
# tests/test_ingest.py
import polars as pl
import pytest
from pathlib import Path

from kotorid.data.ingest import fetch_fred_series, ingest_fred_signals


def test_fetch_fred_series_returns_dataframe():
    df = fetch_fred_series("VIXCLS", start="2024-01-01", end="2024-01-31")
    assert isinstance(df, pl.DataFrame)
    assert "date" in df.columns
    assert "value" in df.columns
    assert len(df) > 0


def test_ingest_fred_signals_writes_parquet(tmp_path):
    out_dir = tmp_path / "daily" / "signals"
    ingest_fred_signals(
        out_dir=out_dir,
        start="2024-01-01",
        end="2024-01-31",
        series=["VIXCLS"],
    )
    vix_path = out_dir / "VIXCLS.parquet"
    assert vix_path.exists()
    df = pl.read_parquet(vix_path)
    assert len(df) > 0
    assert "date" in df.columns
    assert "value" in df.columns
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run python -m pytest tests/test_ingest.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement ingest.py**

```python
# kotorid/data/ingest.py
from __future__ import annotations

import logging
from pathlib import Path

import httpx
import polars as pl

log = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_API_KEY = "DEMO_KEY"  # public demo key, rate-limited but functional


def fetch_fred_series(
    series_id: str,
    start: str = "2020-01-01",
    end: str = "2025-12-31",
    api_key: str = FRED_API_KEY,
) -> pl.DataFrame:
    resp = httpx.get(
        FRED_BASE,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start,
            "observation_end": end,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    observations = resp.json()["observations"]
    rows = []
    for obs in observations:
        val = obs["value"]
        if val == ".":
            continue
        rows.append({"date": obs["date"], "value": float(val)})
    return pl.DataFrame(rows).with_columns(pl.col("date").str.to_date())


def ingest_fred_signals(
    out_dir: Path,
    start: str = "2020-01-01",
    end: str = "2025-12-31",
    series: list[str] | None = None,
) -> None:
    series = series or ["VIXCLS", "BAMLH0A0HYM2", "T10Y2Y"]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for s in series:
        log.info("Fetching FRED series %s", s)
        df = fetch_fred_series(s, start=start, end=end)
        path = out_dir / f"{s}.parquet"
        df.write_parquet(path)
        log.info("Wrote %d rows to %s", len(df), path)


def ingest_philippdubach(out_dir: Path, symbols: list[str] | None = None) -> None:
    """Download philippdubach options-data Parquet files.

    The dataset is hosted at https://static.philippdubach.com/data/options/
    partitioned by year. This function downloads and writes per-symbol
    Parquet files to out_dir/options/.
    """
    symbols = symbols or ["SPY", "QQQ", "IWM", "AAPL", "NVDA"]
    base_url = "https://static.philippdubach.com/data/options"
    options_dir = Path(out_dir) / "options"
    options_dir.mkdir(parents=True, exist_ok=True)

    for symbol in symbols:
        log.info("Downloading %s options data...", symbol)
        frames = []
        for year in range(2020, 2026):
            url = f"{base_url}/{symbol}/{year}.parquet"
            try:
                resp = httpx.get(url, timeout=120.0, follow_redirects=True)
                resp.raise_for_status()
                path = options_dir / f"{symbol}_{year}.parquet"
                path.write_bytes(resp.content)
                frames.append(pl.read_parquet(path))
                path.unlink()
                log.info("  %s/%d: %d rows", symbol, year, len(frames[-1]))
            except httpx.HTTPError:
                log.warning("  %s/%d: not available, skipping", symbol, year)
        if frames:
            combined = pl.concat(frames)
            out_path = options_dir / f"{symbol}.parquet"
            combined.write_parquet(out_path)
            log.info("Wrote %s: %d total rows", out_path, len(combined))
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_ingest.py -v`
Expected: 2 passed (requires network access to FRED)

- [ ] **Step 5: Commit**

```bash
git add kotorid/data/ingest.py tests/test_ingest.py
git commit -m "add data ingest for FRED signals and philippdubach options"
```

---

### Task 5: DataProvider + ParquetProvider

The ABC for data access and the Parquet implementation that reads daily options data.

**Files:**
- Create: `kotorid/data/provider.py`
- Create: `kotorid/data/parquet_provider.py`
- Create: `tests/test_parquet_provider.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_parquet_provider.py
import polars as pl
import pytest
from datetime import date
from pathlib import Path

from kotorid.data.parquet_provider import ParquetProvider


@pytest.fixture
def sample_data(tmp_path):
    """Create a minimal options Parquet file for testing."""
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
    vix_df = pl.DataFrame({
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "value": [13.20, 14.10],
    })
    vix_df.write_parquet(signals_dir / "VIXCLS.parquet")

    return tmp_path


def test_get_chain(sample_data):
    provider = ParquetProvider(sample_data / "daily")
    chain = provider.get_chain("SPY", date(2024, 1, 2), min_dte=5, max_dte=10)
    assert len(chain) == 8
    assert "strike" in chain.columns
    assert "delta" in chain.columns
    assert "bid" in chain.columns


def test_get_chain_filters_by_dte(sample_data):
    provider = ParquetProvider(sample_data / "daily")
    # Expiry is Jan 8, quote date Jan 2 → DTE = 6. Should match 5-10.
    chain = provider.get_chain("SPY", date(2024, 1, 2), min_dte=5, max_dte=10)
    assert len(chain) == 8
    # DTE 1-3 should return nothing
    chain2 = provider.get_chain("SPY", date(2024, 1, 2), min_dte=1, max_dte=3)
    assert len(chain2) == 0


def test_get_quotes(sample_data):
    provider = ParquetProvider(sample_data / "daily")
    quotes = provider.get_quotes(["SPY"], date(2024, 1, 2))
    assert "SPY" in quotes
    assert "bid" in quotes["SPY"]
    assert "ask" in quotes["SPY"]


def test_get_signal_data(sample_data):
    provider = ParquetProvider(sample_data / "daily")
    vix = provider.get_signal_data("VIXCLS", date(2024, 1, 2))
    assert vix == pytest.approx(13.20)


def test_get_signal_data_missing_date(sample_data):
    provider = ParquetProvider(sample_data / "daily")
    vix = provider.get_signal_data("VIXCLS", date(2024, 6, 1))
    assert vix is None
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run python -m pytest tests/test_parquet_provider.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement provider.py (ABC)**

```python
# kotorid/data/provider.py
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import polars as pl


class DataProvider(ABC):
    @abstractmethod
    def get_chain(
        self, underlying: str, as_of: date, min_dte: int, max_dte: int,
    ) -> pl.DataFrame: ...

    @abstractmethod
    def get_quotes(
        self, symbols: list[str], as_of: date,
    ) -> dict[str, dict]: ...

    @abstractmethod
    def get_signal_data(
        self, signal_name: str, as_of: date,
    ) -> float | None: ...
```

- [ ] **Step 4: Implement parquet_provider.py**

```python
# kotorid/data/parquet_provider.py
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

    def get_chain(
        self, underlying: str, as_of: date, min_dte: int, max_dte: int,
    ) -> pl.DataFrame:
        df = self._load_options(underlying)
        return df.filter(
            (pl.col("date") == as_of)
            & ((pl.col("expiration") - pl.col("date")).dt.total_days().is_between(min_dte, max_dte))
        )

    def get_quotes(
        self, symbols: list[str], as_of: date,
    ) -> dict[str, dict]:
        result = {}
        for sym in symbols:
            try:
                df = self._load_options(sym)
            except FileNotFoundError:
                continue
            row = df.filter(pl.col("date") == as_of).head(1)
            if len(row) == 0:
                continue
            r = row.to_dicts()[0]
            result[sym] = {"bid": r.get("bid", 0), "ask": r.get("ask", 0)}
        return result

    def get_signal_data(
        self, signal_name: str, as_of: date,
    ) -> float | None:
        try:
            df = self._load_signal(signal_name)
        except FileNotFoundError:
            return None
        row = df.filter(pl.col("date") == as_of)
        if len(row) == 0:
            return None
        return row["value"][0]
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_parquet_provider.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add kotorid/data/provider.py kotorid/data/parquet_provider.py tests/test_parquet_provider.py
git commit -m "add DataProvider ABC and ParquetProvider"
```

---

### Task 6: Portfolio

Track positions, cash, equity curve, and trade log. The portfolio is shared state passed through the handler pipeline.

**Files:**
- Create: `kotorid/portfolio/portfolio.py`
- Create: `tests/test_portfolio.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_portfolio.py
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from kotorid.portfolio.portfolio import Portfolio, Position, TradeRecord

ET = ZoneInfo("America/New_York")


def test_initial_state():
    p = Portfolio(initial_cash=100_000.0)
    assert p.cash == 100_000.0
    assert p.positions == {}
    assert p.trade_log == []
    assert p.equity_curve == []


def test_open_position():
    p = Portfolio(initial_cash=100_000.0)
    ts = datetime(2024, 1, 2, 10, 0, tzinfo=ET)
    p.open_position(
        symbol="SPY_IC_20240108",
        entry_credit=1.00,
        max_loss=400.0,
        contracts=1,
        legs={"short_call": 480, "long_call": 485, "short_put": 465, "long_put": 460},
        timestamp=ts,
    )
    assert "SPY_IC_20240108" in p.positions
    pos = p.positions["SPY_IC_20240108"]
    assert pos.entry_credit == 1.00
    assert pos.contracts == 1
    # Cash increases by credit received (credit × 100 × contracts)
    assert p.cash == 100_100.0


def test_close_position():
    p = Portfolio(initial_cash=100_000.0)
    ts1 = datetime(2024, 1, 2, 10, 0, tzinfo=ET)
    p.open_position("IC1", 1.00, 400.0, 1, {}, ts1)
    ts2 = datetime(2024, 1, 5, 14, 0, tzinfo=ET)
    p.close_position("IC1", exit_debit=0.50, reason="profit_target", timestamp=ts2)
    assert "IC1" not in p.positions
    # Cash: started 100_000, +100 credit, -50 debit = 100_050
    assert p.cash == 100_050.0
    assert len(p.trade_log) == 1
    assert p.trade_log[0].realized_pnl == 50.0


def test_record_equity():
    p = Portfolio(initial_cash=100_000.0)
    ts = datetime(2024, 1, 2, 16, 0, tzinfo=ET)
    p.record_equity(ts)
    assert len(p.equity_curve) == 1
    assert p.equity_curve[0] == (ts, 100_000.0)


def test_max_drawdown_no_trades():
    p = Portfolio(initial_cash=100_000.0)
    assert p.max_drawdown() == 0.0
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run python -m pytest tests/test_portfolio.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement portfolio.py**

```python
# kotorid/portfolio/portfolio.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    symbol: str
    entry_credit: float
    max_loss: float
    contracts: int
    legs: dict
    opened_at: datetime
    current_debit: float | None = None


@dataclass
class TradeRecord:
    symbol: str
    entry_credit: float
    exit_debit: float
    contracts: int
    realized_pnl: float
    reason: str
    opened_at: datetime
    closed_at: datetime


class Portfolio:
    def __init__(self, initial_cash: float = 100_000.0):
        self.cash: float = initial_cash
        self.initial_cash: float = initial_cash
        self.positions: dict[str, Position] = {}
        self.trade_log: list[TradeRecord] = []
        self.equity_curve: list[tuple[datetime, float]] = []
        self._peak: float = initial_cash

    def open_position(
        self,
        symbol: str,
        entry_credit: float,
        max_loss: float,
        contracts: int,
        legs: dict,
        timestamp: datetime,
    ) -> None:
        self.positions[symbol] = Position(
            symbol=symbol,
            entry_credit=entry_credit,
            max_loss=max_loss,
            contracts=contracts,
            legs=legs,
            opened_at=timestamp,
        )
        self.cash += entry_credit * 100 * contracts

    def close_position(
        self, symbol: str, exit_debit: float, reason: str, timestamp: datetime,
    ) -> TradeRecord:
        pos = self.positions.pop(symbol)
        cost = exit_debit * 100 * pos.contracts
        self.cash -= cost
        pnl = (pos.entry_credit - exit_debit) * 100 * pos.contracts
        record = TradeRecord(
            symbol=pos.symbol,
            entry_credit=pos.entry_credit,
            exit_debit=exit_debit,
            contracts=pos.contracts,
            realized_pnl=pnl,
            reason=reason,
            opened_at=pos.opened_at,
            closed_at=timestamp,
        )
        self.trade_log.append(record)
        return record

    def record_equity(self, timestamp: datetime) -> None:
        equity = self.cash
        self.equity_curve.append((timestamp, equity))
        if equity > self._peak:
            self._peak = equity

    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0][1]
        max_dd = 0.0
        for _, equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return max_dd
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_portfolio.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add kotorid/portfolio/portfolio.py tests/test_portfolio.py
git commit -m "add Portfolio with position tracking and equity curve"
```

---

### Task 7: Simulated Executor

Fill orders at bid/ask with transaction costs.

**Files:**
- Create: `kotorid/execution/executor.py`
- Create: `kotorid/execution/cost.py`
- Create: `kotorid/execution/simulated.py`
- Create: `tests/test_simulated_executor.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_simulated_executor.py
import pytest
from kotorid.execution.executor import Order, OrderSide
from kotorid.execution.cost import CostConfig
from kotorid.execution.simulated import SimulatedExecutor


def test_sell_fills_at_bid():
    executor = SimulatedExecutor(CostConfig(commission_per_contract=0))
    order = Order(
        symbol="SPY_IC",
        side=OrderSide.SELL,
        contracts=1,
        legs=[
            {"type": "sell", "bid": 1.20, "ask": 1.40},
            {"type": "sell", "bid": 0.80, "ask": 0.95},
            {"type": "buy", "bid": 0.10, "ask": 0.20},
            {"type": "buy", "bid": 0.05, "ask": 0.15},
        ],
    )
    fill = executor.execute(order)
    # Sell legs fill at bid, buy legs fill at ask
    # Net credit = (1.20 + 0.80) - (0.20 + 0.15) = 1.65
    assert fill.net_credit == pytest.approx(1.65)
    assert fill.filled is True


def test_commission_deducted():
    executor = SimulatedExecutor(CostConfig(commission_per_contract=0.65))
    order = Order(
        symbol="SPY_IC",
        side=OrderSide.SELL,
        contracts=1,
        legs=[
            {"type": "sell", "bid": 1.00, "ask": 1.10},
            {"type": "sell", "bid": 0.50, "ask": 0.60},
            {"type": "buy", "bid": 0.05, "ask": 0.10},
            {"type": "buy", "bid": 0.03, "ask": 0.08},
        ],
    )
    fill = executor.execute(order)
    # 4 legs × $0.65 = $2.60 commission
    assert fill.commission == pytest.approx(2.60)


def test_buy_to_close_fills_at_ask():
    executor = SimulatedExecutor(CostConfig(commission_per_contract=0))
    order = Order(
        symbol="SPY_IC",
        side=OrderSide.BUY,
        contracts=1,
        legs=[
            {"type": "buy", "bid": 0.50, "ask": 0.60},
            {"type": "buy", "bid": 0.30, "ask": 0.40},
            {"type": "sell", "bid": 0.02, "ask": 0.05},
            {"type": "sell", "bid": 0.01, "ask": 0.04},
        ],
    )
    fill = executor.execute(order)
    # Buy legs at ask, sell legs at bid
    # Net debit = (0.60 + 0.40) - (0.02 + 0.01) = 0.97
    assert fill.net_debit == pytest.approx(0.97)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run python -m pytest tests/test_simulated_executor.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement executor.py (Order, Fill, ABC)**

```python
# kotorid/execution/executor.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
```

- [ ] **Step 4: Implement cost.py**

```python
# kotorid/execution/cost.py
from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    commission_per_contract: float = 0.65
    slippage_pct: float = 0.0
```

- [ ] **Step 5: Implement simulated.py**

```python
# kotorid/execution/simulated.py
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
            bid = leg["bid"]
            ask = leg["ask"]
            if leg["type"] == "sell":
                credit += bid
            else:
                debit += ask

        commission = self.cost.commission_per_contract * len(order.legs) * order.contracts

        if order.side == OrderSide.SELL:
            net = credit - debit
            return Fill(
                symbol=order.symbol,
                filled=True,
                net_credit=net,
                commission=commission,
            )
        else:
            net = debit - credit
            return Fill(
                symbol=order.symbol,
                filled=True,
                net_debit=net,
                commission=commission,
            )
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_simulated_executor.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add kotorid/execution/ tests/test_simulated_executor.py
git commit -m "add SimulatedExecutor with bid/ask fills and transaction costs"
```

---

### Task 8: IC Strategy Handler

The core strategy logic: select IC candidates from option chains and manage open positions (exit triggers). Reads from ICConfig, DataProvider, and Portfolio.

**Files:**
- Create: `kotorid/strategy/ic_strategy.py`
- Create: `tests/test_ic_strategy.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_ic_strategy.py
import pytest
import polars as pl
from datetime import date

from kotorid.strategy.config import ICConfig
from kotorid.strategy.ic_strategy import select_ic_candidate, check_exit


def _make_chain() -> pl.DataFrame:
    """Realistic 8-strike chain for SPY around 475."""
    return pl.DataFrame({
        "type": ["call"] * 4 + ["put"] * 4,
        "strike": [480.0, 485.0, 490.0, 495.0, 460.0, 465.0, 470.0, 475.0],
        "bid": [4.20, 1.80, 0.60, 0.15, 0.12, 0.45, 1.50, 3.80],
        "ask": [4.40, 2.00, 0.75, 0.25, 0.20, 0.55, 1.70, 4.00],
        "delta": [0.55, 0.30, 0.14, 0.05, -0.05, -0.13, -0.30, -0.55],
    })


def test_select_ic_candidate_picks_nearest_delta():
    cfg = ICConfig(target_delta=0.16, wing_width=5.0, min_credit_ratio=0.0)
    chain = _make_chain()
    candidate = select_ic_candidate(chain, cfg)
    assert candidate is not None
    # Should pick 490 call (delta 0.14, nearest to 0.16) and 465 put (delta -0.13)
    assert candidate["short_call"] == 490.0
    assert candidate["short_put"] == 465.0
    assert candidate["long_call"] == 495.0
    assert candidate["long_put"] == 460.0


def test_select_ic_candidate_returns_none_when_no_wings():
    cfg = ICConfig(target_delta=0.16, wing_width=50.0)  # absurd wing width
    chain = _make_chain()
    candidate = select_ic_candidate(chain, cfg)
    assert candidate is None


def test_select_ic_candidate_credit_ratio_filter():
    cfg = ICConfig(target_delta=0.16, wing_width=5.0, min_credit_ratio=0.90)
    chain = _make_chain()
    candidate = select_ic_candidate(chain, cfg)
    # Credit ratio will be low (~0.14), should be rejected
    assert candidate is None


def test_check_exit_profit_target():
    result = check_exit(entry_credit=1.00, current_debit=0.40, cfg=ICConfig())
    assert result == "profit_target"


def test_check_exit_stop_loss():
    result = check_exit(entry_credit=1.00, current_debit=2.10, cfg=ICConfig())
    assert result == "stop_loss"


def test_check_exit_no_trigger():
    result = check_exit(entry_credit=1.00, current_debit=0.80, cfg=ICConfig())
    assert result is None
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run python -m pytest tests/test_ic_strategy.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement ic_strategy.py**

```python
# kotorid/strategy/ic_strategy.py
from __future__ import annotations

import polars as pl

from kotorid.strategy.config import ICConfig


def select_ic_candidate(
    chain: pl.DataFrame,
    cfg: ICConfig,
) -> dict | None:
    calls = chain.filter(pl.col("type") == "call").sort("strike")
    puts = chain.filter(pl.col("type") == "put").sort("strike", descending=True)

    if len(calls) == 0 or len(puts) == 0:
        return None

    # Find short strikes nearest to target delta
    short_call_row = calls.filter(pl.col("delta") > 0).sort(
        (pl.col("delta") - cfg.target_delta).abs()
    ).head(1)
    short_put_row = puts.filter(pl.col("delta") < 0).sort(
        (pl.col("delta").abs() - cfg.target_delta).abs()
    ).head(1)

    if len(short_call_row) == 0 or len(short_put_row) == 0:
        return None

    sc_strike = short_call_row["strike"][0]
    sp_strike = short_put_row["strike"][0]
    lc_strike = sc_strike + cfg.wing_width
    lp_strike = sp_strike - cfg.wing_width

    # Find long strikes in the chain
    lc_row = calls.filter(pl.col("strike") == lc_strike)
    lp_row = puts.filter(pl.col("strike") == lp_strike)

    if len(lc_row) == 0 or len(lp_row) == 0:
        return None

    # Compute credit (sell shorts at bid, buy longs at ask)
    sc_bid = short_call_row["bid"][0]
    sp_bid = short_put_row["bid"][0]
    lc_ask = lc_row["ask"][0]
    lp_ask = lp_row["ask"][0]
    credit = (sc_bid + sp_bid) - (lc_ask + lp_ask)

    if credit <= 0:
        return None

    credit_ratio = credit / cfg.wing_width
    if credit_ratio < cfg.min_credit_ratio:
        return None

    return {
        "short_call": sc_strike,
        "long_call": lc_strike,
        "short_put": sp_strike,
        "long_put": lp_strike,
        "credit": round(credit, 2),
        "max_loss": round((cfg.wing_width - credit) * 100, 2),
        "credit_ratio": round(credit_ratio, 4),
    }


def check_exit(
    entry_credit: float,
    current_debit: float,
    cfg: ICConfig,
) -> str | None:
    if current_debit <= entry_credit * cfg.profit_target:
        return "profit_target"
    if current_debit >= entry_credit * cfg.stop_loss:
        return "stop_loss"
    return None
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_ic_strategy.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add kotorid/strategy/ic_strategy.py tests/test_ic_strategy.py
git commit -m "add IC strategy candidate selection and exit triggers"
```

---

### Task 9: Signal Mesh

The mesh holds per-signal state with exponential decay and regime-conditional weights. The allocator reads a composite score.

**Files:**
- Create: `kotorid/signals/mesh.py`
- Create: `kotorid/signals/regime.py`
- Create: `kotorid/strategy/allocator.py`
- Create: `tests/test_signal_mesh.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_signal_mesh.py
import pytest
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from kotorid.signals.mesh import SignalMesh

ET = ZoneInfo("America/New_York")
T0 = datetime(2024, 1, 2, 10, 0, tzinfo=ET)


def test_update_and_read():
    mesh = SignalMesh()
    mesh.update("vix_regime", value=1.0, half_life_days=5.0, timestamp=T0)
    score = mesh.composite_score(T0, regime="normal")
    assert score > 0


def test_decay():
    mesh = SignalMesh()
    mesh.update("vix_regime", value=1.0, half_life_days=5.0, timestamp=T0)
    score_now = mesh.composite_score(T0, regime="normal")
    score_later = mesh.composite_score(T0 + timedelta(days=5), regime="normal")
    # After one half-life, contribution should be ~half
    assert score_later == pytest.approx(score_now * 0.5, rel=0.01)


def test_multiple_signals_sum():
    mesh = SignalMesh()
    mesh.update("vix_regime", value=1.0, half_life_days=5.0, timestamp=T0)
    mesh.update("iv_rank", value=1.0, half_life_days=10.0, timestamp=T0)
    single = SignalMesh()
    single.update("vix_regime", value=1.0, half_life_days=5.0, timestamp=T0)
    assert mesh.composite_score(T0, regime="normal") > single.composite_score(T0, regime="normal")


def test_rejects_nan():
    mesh = SignalMesh()
    with pytest.raises(ValueError):
        mesh.update("bad", value=float("nan"), half_life_days=5.0, timestamp=T0)


def test_rejects_inf():
    mesh = SignalMesh()
    with pytest.raises(ValueError):
        mesh.update("bad", value=float("inf"), half_life_days=5.0, timestamp=T0)


def test_regime_conditional_weights():
    mesh = SignalMesh(
        weights={
            "normal": {"vix_regime": 0.5, "iv_rank": 0.5},
            "crisis": {"vix_regime": 0.9, "iv_rank": 0.1},
        }
    )
    mesh.update("vix_regime", 1.0, 5.0, T0)
    mesh.update("iv_rank", 1.0, 10.0, T0)
    score_normal = mesh.composite_score(T0, regime="normal")
    score_crisis = mesh.composite_score(T0, regime="crisis")
    # In crisis, vix_regime (weight 0.9) dominates over iv_rank (0.1)
    # Both have value=1.0, but iv_rank contributes less in crisis
    assert score_crisis != score_normal


def test_empty_mesh_returns_zero():
    mesh = SignalMesh()
    assert mesh.composite_score(T0, regime="normal") == 0.0
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run python -m pytest tests/test_signal_mesh.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement mesh.py**

```python
# kotorid/signals/mesh.py
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "normal": {"vix_regime": 0.30, "iv_rank": 0.25},
    "caution": {"vix_regime": 0.50, "iv_rank": 0.15},
    "crisis": {"vix_regime": 0.70, "iv_rank": 0.05},
}


@dataclass
class SignalEntry:
    value: float
    half_life_days: float
    fired_at: datetime


class SignalMesh:
    def __init__(self, weights: dict[str, dict[str, float]] | None = None):
        self.weights = weights or DEFAULT_WEIGHTS
        self._signals: dict[str, SignalEntry] = {}

    def update(
        self,
        name: str,
        value: float,
        half_life_days: float,
        timestamp: datetime,
    ) -> None:
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"Signal '{name}' value must be finite, got {value}")
        if half_life_days <= 0:
            raise ValueError(f"half_life_days must be positive, got {half_life_days}")
        self._signals[name] = SignalEntry(value, half_life_days, timestamp)

    def composite_score(self, as_of: datetime, regime: str = "normal") -> float:
        regime_weights = self.weights.get(regime, {})
        total = 0.0
        for name, entry in self._signals.items():
            weight = regime_weights.get(name, 0.0)
            if weight == 0:
                continue
            elapsed_days = (as_of - entry.fired_at).total_seconds() / 86400
            decay = math.exp(-elapsed_days * math.log(2) / entry.half_life_days)
            total += entry.value * weight * decay
        return total

    def active_signals(self) -> dict[str, SignalEntry]:
        return dict(self._signals)
```

- [ ] **Step 4: Implement regime.py and allocator.py**

```python
# kotorid/signals/regime.py
from kotorid.signals.mesh import SignalMesh


def update_vix_regime(mesh: SignalMesh, vix: float, timestamp) -> str:
    """Classify VIX level and update mesh. Returns the regime string."""
    if vix >= 45.0:
        regime = "crisis"
        value = -1.0  # strong no-trade signal
    elif vix >= 35.0:
        regime = "caution"
        value = -0.5
    elif vix >= 20.0:
        regime = "normal"
        value = 0.5
    else:
        regime = "normal"
        value = 1.0  # low VIX = favorable for selling premium
    mesh.update("vix_regime", value, half_life_days=5.0, timestamp=timestamp)
    return regime
```

```python
# kotorid/strategy/allocator.py
from __future__ import annotations

from datetime import datetime
from kotorid.signals.mesh import SignalMesh


class StrategyAllocator:
    def __init__(self, entry_threshold: float = 0.0):
        self.entry_threshold = entry_threshold

    def should_trade(self, mesh: SignalMesh, timestamp: datetime, regime: str) -> bool:
        score = mesh.composite_score(timestamp, regime)
        return score > self.entry_threshold

    def position_scale(self, mesh: SignalMesh, timestamp: datetime, regime: str) -> float:
        score = mesh.composite_score(timestamp, regime)
        return max(0.0, min(1.0, score))
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_signal_mesh.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add kotorid/signals/mesh.py kotorid/signals/regime.py kotorid/strategy/allocator.py tests/test_signal_mesh.py
git commit -m "add SignalMesh with decay, VIX regime signal, and allocator"
```

---

### Task 10: Analytics

Compute Sharpe ratio, max drawdown, win rate, and A/B comparison between backtest runs.

**Files:**
- Create: `kotorid/analytics/stats.py`
- Create: `kotorid/analytics/compare.py`
- Create: `tests/test_analytics.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_analytics.py
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from kotorid.analytics.stats import compute_stats
from kotorid.analytics.compare import compare_runs
from kotorid.portfolio.portfolio import Portfolio, TradeRecord

ET = ZoneInfo("America/New_York")
T0 = datetime(2024, 1, 2, 10, 0, tzinfo=ET)


def _make_portfolio(trades: list[tuple[float, str]]) -> Portfolio:
    """Create a portfolio with the given (pnl, reason) trades."""
    p = Portfolio(initial_cash=100_000.0)
    for i, (pnl, reason) in enumerate(trades):
        credit = 1.00
        debit = credit - (pnl / 100)
        ts_open = T0 + timedelta(days=i * 7)
        ts_close = ts_open + timedelta(days=5)
        p.trade_log.append(TradeRecord(
            symbol=f"IC_{i}",
            entry_credit=credit,
            exit_debit=debit,
            contracts=1,
            realized_pnl=pnl,
            reason=reason,
            opened_at=ts_open,
            closed_at=ts_close,
        ))
        p.cash += pnl
        p.record_equity(ts_close)
    return p


def test_compute_stats_basic():
    p = _make_portfolio([
        (50.0, "profit_target"),
        (80.0, "profit_target"),
        (-100.0, "stop_loss"),
        (30.0, "profit_target"),
    ])
    stats = compute_stats(p)
    assert stats["total_trades"] == 4
    assert stats["wins"] == 3
    assert stats["losses"] == 1
    assert stats["win_rate"] == pytest.approx(0.75)
    assert stats["total_pnl"] == pytest.approx(60.0)
    assert stats["avg_win"] == pytest.approx(160.0 / 3)
    assert stats["avg_loss"] == pytest.approx(-100.0)


def test_compute_stats_empty():
    p = Portfolio(initial_cash=100_000.0)
    stats = compute_stats(p)
    assert stats["total_trades"] == 0
    assert stats["win_rate"] is None


def test_compare_runs():
    baseline = _make_portfolio([(50.0, "profit_target"), (-100.0, "stop_loss")])
    overlay = _make_portfolio([(50.0, "profit_target"), (30.0, "profit_target")])
    result = compare_runs(baseline, overlay, labels=("baseline", "vix_filter"))
    assert result["baseline"]["total_pnl"] == pytest.approx(-50.0)
    assert result["vix_filter"]["total_pnl"] == pytest.approx(80.0)
    assert result["improvement_pnl"] == pytest.approx(130.0)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run python -m pytest tests/test_analytics.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement stats.py**

```python
# kotorid/analytics/stats.py
from __future__ import annotations

import math

from kotorid.portfolio.portfolio import Portfolio


def compute_stats(portfolio: Portfolio) -> dict:
    trades = portfolio.trade_log
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": None, "total_pnl": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": None, "max_drawdown": 0.0,
            "sharpe": None,
        }

    wins = [t for t in trades if t.realized_pnl > 0]
    losses = [t for t in trades if t.realized_pnl <= 0]
    total_pnl = sum(t.realized_pnl for t in trades)
    gross_profit = sum(t.realized_pnl for t in wins) if wins else 0.0
    gross_loss = abs(sum(t.realized_pnl for t in losses)) if losses else 0.0

    pnls = [t.realized_pnl for t in trades]
    mean_pnl = total_pnl / len(trades)
    std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)) if len(pnls) > 1 else 0.0
    sharpe = (mean_pnl / std_pnl) if std_pnl > 0 else None

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades),
        "total_pnl": total_pnl,
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": -(gross_loss / len(losses)) if losses else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "max_drawdown": portfolio.max_drawdown(),
        "sharpe": sharpe,
    }
```

- [ ] **Step 4: Implement compare.py**

```python
# kotorid/analytics/compare.py
from __future__ import annotations

from kotorid.analytics.stats import compute_stats
from kotorid.portfolio.portfolio import Portfolio


def compare_runs(
    baseline: Portfolio,
    overlay: Portfolio,
    labels: tuple[str, str] = ("baseline", "overlay"),
) -> dict:
    baseline_stats = compute_stats(baseline)
    overlay_stats = compute_stats(overlay)
    return {
        labels[0]: baseline_stats,
        labels[1]: overlay_stats,
        "improvement_pnl": overlay_stats["total_pnl"] - baseline_stats["total_pnl"],
        "improvement_win_rate": (
            (overlay_stats["win_rate"] or 0) - (baseline_stats["win_rate"] or 0)
        ),
    }
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_analytics.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add kotorid/analytics/ tests/test_analytics.py
git commit -m "add analytics: stats computation and A/B comparison"
```

---

### Task 11: Baseline Backtest CLI

Wire everything together: engine + clock + handlers + data + executor + portfolio. Run the IC strategy over historical data with no signals and output stats.

**Files:**
- Create: `scripts/run_backtest.py`
- Create: `kotorid/portfolio/risk.py`

- [ ] **Step 1: Implement risk gates**

```python
# kotorid/portfolio/risk.py
from __future__ import annotations

from kotorid.portfolio.portfolio import Portfolio


class MaxPositionCount:
    def __init__(self, limit: int = 5):
        self.limit = limit

    def allows(self, portfolio: Portfolio) -> bool:
        return len(portfolio.positions) < self.limit


class MaxDrawdown:
    def __init__(self, max_dd: float = 0.10):
        self.max_dd = max_dd

    def allows(self, portfolio: Portfolio) -> bool:
        return portfolio.max_drawdown() < self.max_dd
```

- [ ] **Step 2: Implement run_backtest.py**

```python
# scripts/run_backtest.py
"""Run a baseline IC backtest over daily options data.

Usage:
    uv run python scripts/run_backtest.py --data-dir data/daily --start 2023-01-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kotorid.clock import BacktestClock, MarketState
from kotorid.data.parquet_provider import ParquetProvider
from kotorid.execution.simulated import SimulatedExecutor
from kotorid.execution.cost import CostConfig
from kotorid.execution.executor import Order, OrderSide
from kotorid.portfolio.portfolio import Portfolio
from kotorid.portfolio.risk import MaxPositionCount
from kotorid.strategy.config import ICConfig
from kotorid.strategy.ic_strategy import select_ic_candidate, check_exit
from kotorid.signals.mesh import SignalMesh
from kotorid.signals.regime import update_vix_regime
from kotorid.strategy.allocator import StrategyAllocator
from kotorid.analytics.stats import compute_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def run_backtest(
    data_dir: Path,
    start: date,
    end: date,
    ic_config: ICConfig | None = None,
    use_signals: bool = False,
    symbols: list[str] | None = None,
) -> dict:
    cfg = ic_config or ICConfig()
    provider = ParquetProvider(data_dir)
    executor = SimulatedExecutor(CostConfig())
    portfolio = Portfolio(initial_cash=100_000.0)
    risk = MaxPositionCount(limit=5)
    mesh = SignalMesh() if use_signals else None
    allocator = StrategyAllocator() if use_signals else None
    symbols = symbols or ["SPY"]

    clock = BacktestClock(start=start, end=end)
    last_scan_date: date | None = None
    current_regime = "normal"

    for timestamp, state in clock.tick():
        today = timestamp.date()

        # Daily signal update at market open
        if state == MarketState.GAP_OPEN:
            if mesh is not None:
                vix = provider.get_signal_data("VIXCLS", today)
                if vix is not None:
                    current_regime = update_vix_regime(mesh, vix, timestamp)

        # Check exits every tick
        closed = []
        for sym, pos in list(portfolio.positions.items()):
            chain = provider.get_chain(
                sym.split("_")[0], today, min_dte=0, max_dte=60,
            )
            if len(chain) == 0:
                continue
            legs = pos.legs
            sc = chain.filter(
                (chain["strike"] == legs["short_call"]) & (chain["type"] == "call")
            )
            sp = chain.filter(
                (chain["strike"] == legs["short_put"]) & (chain["type"] == "put")
            )
            lc = chain.filter(
                (chain["strike"] == legs["long_call"]) & (chain["type"] == "call")
            )
            lp = chain.filter(
                (chain["strike"] == legs["long_put"]) & (chain["type"] == "put")
            )
            if any(len(x) == 0 for x in [sc, sp, lc, lp]):
                continue
            debit = (sc["bid"][0] + sp["bid"][0]) - (lc["ask"][0] + lp["ask"][0])
            debit = max(0.0, debit)
            pos.current_debit = debit
            trigger = check_exit(pos.entry_credit, debit, cfg)
            if trigger:
                order = Order(sym, OrderSide.BUY, pos.contracts, [
                    {"type": "buy", "bid": sc["bid"][0], "ask": sc["ask"][0]},
                    {"type": "buy", "bid": sp["bid"][0], "ask": sp["ask"][0]},
                    {"type": "sell", "bid": lc["bid"][0], "ask": lc["ask"][0]},
                    {"type": "sell", "bid": lp["bid"][0], "ask": lp["ask"][0]},
                ])
                fill = executor.execute(order)
                portfolio.close_position(sym, fill.net_debit, trigger, timestamp)
                portfolio.cash -= fill.commission
                closed.append(sym)

        # Scan for new candidates once per day at gap open
        if state == MarketState.GAP_OPEN and today != last_scan_date:
            last_scan_date = today
            if not risk.allows(portfolio):
                continue
            if allocator and mesh and not allocator.should_trade(mesh, timestamp, current_regime):
                continue
            for symbol in symbols:
                chain = provider.get_chain(symbol, today, cfg.min_dte, cfg.max_dte)
                if len(chain) == 0:
                    continue
                candidate = select_ic_candidate(chain, cfg)
                if candidate is None:
                    continue
                pos_key = f"{symbol}_IC_{today.isoformat()}"
                if pos_key in portfolio.positions:
                    continue
                order = Order(pos_key, OrderSide.SELL, 1, [
                    {"type": "sell", "bid": candidate["credit"], "ask": candidate["credit"]},
                    {"type": "sell", "bid": 0, "ask": 0},
                    {"type": "buy", "bid": 0, "ask": 0},
                    {"type": "buy", "bid": 0, "ask": 0},
                ])
                portfolio.open_position(
                    pos_key, candidate["credit"], candidate["max_loss"], 1,
                    candidate, timestamp,
                )
                portfolio.cash -= CostConfig().commission_per_contract * 4
                log.info(
                    "%s: opened %s credit=$%.2f",
                    today, pos_key, candidate["credit"],
                )

        # Record equity at close
        if state == MarketState.MARKET_OPEN and timestamp.hour == 15 and timestamp.minute == 45:
            portfolio.record_equity(timestamp)

    stats = compute_stats(portfolio)
    return stats


def main():
    parser = argparse.ArgumentParser(description="Run IC backtest")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, default="2023-01-01")
    parser.add_argument("--end", type=date.fromisoformat, default="2024-12-31")
    parser.add_argument("--symbols", nargs="+", default=["SPY"])
    parser.add_argument("--use-signals", action="store_true")
    args = parser.parse_args()

    stats = run_backtest(
        data_dir=args.data_dir,
        start=args.start,
        end=args.end,
        symbols=args.symbols,
        use_signals=args.use_signals,
    )
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the full test suite**

Run: `uv run python -m pytest tests/test_strategy_config.py tests/test_clock.py tests/test_handlers.py tests/test_portfolio.py tests/test_simulated_executor.py tests/test_ic_strategy.py tests/test_signal_mesh.py tests/test_analytics.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add scripts/run_backtest.py kotorid/portfolio/risk.py
git commit -m "add baseline backtest CLI runner"
```

- [ ] **Step 5: Download data and run first backtest**

```bash
# Download FRED signals
uv run python -c "
from kotorid.data.ingest import ingest_fred_signals
from pathlib import Path
ingest_fred_signals(Path('data/daily/signals'), start='2023-01-01', end='2024-12-31')
"

# Download philippdubach SPY data
uv run python -c "
from kotorid.data.ingest import ingest_philippdubach
from pathlib import Path
ingest_philippdubach(Path('data/daily'), symbols=['SPY'])
"

# Run baseline backtest (no signals)
uv run python scripts/run_backtest.py --data-dir data/daily --start 2023-01-01 --end 2024-12-31

# Run with VIX signal overlay
uv run python scripts/run_backtest.py --data-dir data/daily --start 2023-01-01 --end 2024-12-31 --use-signals
```

- [ ] **Step 6: Add data/ to .gitignore**

```bash
echo "data/" >> .gitignore
git add .gitignore
git commit -m "ignore data directory"
```

---

## Post-Completion

After all 11 tasks are done, you have:
- A working backtest engine that replays IC strategy over daily options data
- Baseline stats (Sharpe, drawdown, win rate) with no signals
- VIX regime signal overlay comparison
- All components tested independently
- CLI to run backtests with different configs

**Next steps (v2.1):** Polygon intraday integration, Black-Scholes Greeks, intraday exit triggers.
