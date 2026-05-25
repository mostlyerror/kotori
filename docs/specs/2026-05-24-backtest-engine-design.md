# Backtest Engine Design

**Date:** 2026-05-24
**Purpose:** Validate signal mesh overlays against the known IC edge
**Scope:** v5 skeleton, v2 implemented first

## Goal

The IC edge (IV overstatement at earnings) is documented. The backtest engine exists to measure whether each signal overlay improves it. The baseline is "IC with no signals." We test "IC + VIX regime filter," "IC + VIX + IV rank," etc. against that baseline.

## Architecture: Simulation Clock + Handlers

One unified tick loop. `BacktestClock` ticks through historical timestamps. `LiveClock` wraps real time. Registered handlers run at each tick based on frequency declarations. Same handler code runs in both modes — only the clock and data/execution backends change.

### Clock

```
BacktestClock(start, end, step=15min, skip_non_market=True)
LiveClock(poll_interval=30s)

clock.tick() → yields (timestamp, market_state)
market_state: pre_market | market_open | market_close | closed
```

The clock skips weekends, holidays, and overnight hours. Before resuming each market day, it injects a synthetic **gap tick** at market open that checks whether any position was breached overnight (addresses the overnight gap risk identified in the architecture review).

### Handler Pipeline

Handlers run in priority order at each tick. Each declares its frequency.

| Priority | Handler | Frequency | Responsibility |
|----------|---------|-----------|----------------|
| 1 | Data Feed | every tick | Read quotes/chains from Parquet or Tradier |
| 2 | Signal Evaluators | varies | Update signal mesh state |
| 3 | Strategy Allocator | daily at scan time | Read mesh → output trade decisions |
| 4 | Position Manager | every tick | Monitor exits: profit, stop, DTE, delta |
| 5 | Risk Gate | before any order | MaxDelta, MaxVega, MaxDrawdown, MaxPositionCount |
| 6 | Order Executor | after risk gate | Simulated fill or real Tradier order |

Handlers declare frequency as: `every_tick`, `daily_at(time)`, `on(event)`. The clock checks declarations and skips handlers that aren't due. This maps directly to the existing APScheduler jobs.

### Shared State

Three mutable state objects passed through the handler pipeline:

**Portfolio** — positions, cash, equity curve, trade log, order queue

**Signal Mesh** — per-signal state with regime-conditional decay:
- Each signal writes: `value`, `fired_at`, `half_life`
- Contribution decays: `value × weight × exp(-t/half_life)`
- Weights indexed by regime: different weight table per regime state
- Strategy Allocator reads composite mesh score, never queries individual signals
- Validated on update: reject NaN/Inf, log stale signals

**Market Snapshot** — quotes by symbol, option chains, Greeks, underlying prices, VIX

### Strategy Config

Dataclasses extracted from hardcoded constants:

```python
@dataclass
class ICConfig:
    target_delta: float = 0.16
    wing_width: float = 5.0
    min_dte: int = 5
    max_dte: int = 9
    min_credit_ratio: float = 0.20
    profit_target: float = 0.50
    stop_loss: float = 2.00

# v3+
@dataclass
class DirectionalConfig: ...

# v4+
@dataclass
class VolBuyConfig: ...
```

Same config feeds both the live daemon and the backtest engine.

## Data Pipeline

### Sources

| Source | Granularity | Content | Cost |
|--------|------------|---------|------|
| philippdubach/options-data | Daily EOD | 104 symbols, 2008-2025, bid/ask + all Greeks + IV | Free |
| FRED API | Daily | VIX, HY spreads, yield curve, LEI, DXY | Free |
| Earnings calendar | Event | Earnings dates per symbol | Free/scraped |
| Polygon API | Intraday 15-min | Option OHLCV + bid/ask (no Greeks) | $79/mo (Developer) |

### Storage

```
data/
  daily/
    options/SPY.parquet, QQQ.parquet, ...    # philippdubach
    signals/vix.parquet, hy_spread.parquet   # FRED
    events/earnings.parquet, fomc.parquet    # calendars
  intraday/
    SPY/2024-01-15.parquet, ...             # Polygon cache
```

### Hybrid Strategy

Daily data from philippdubach for signal computation and long lookback. Intraday data from Polygon only for dates with open positions. Fetched inline during backtest, cached to local Parquet. First run hits the API (slow); subsequent runs are fully offline.

Estimated intraday data volume: ~200-400 files for a 3-year backtest (only position-days need intraday resolution).

### Greeks

Polygon does not provide historical intraday Greeks. We compute delta/gamma/vega/theta via Black-Scholes from option prices + underlying prices at each 15-min tick. Daily Greeks from philippdubach serve as calibration — we compare computed vs dataset Greeks at EOD to flag systematic drift.

## Broker Abstraction

Split into two interfaces:

**DataProvider** — `get_quotes(symbols, timestamp)`, `get_chain(underlying, timestamp, min_dte, max_dte)`, `get_signal_data(signal_name, timestamp)`
- `ParquetProvider`: reads from local Parquet files
- `TradierProvider`: wraps existing tradier_client.py
- `PolygonCacheProvider`: fetch-on-demand + cache to Parquet

**OrderExecutor** — `execute_order(order) → Fill`
- `SimulatedExecutor`: fill at bid/ask from data, apply transaction costs, model slippage
- `TradierExecutor`: wraps existing order_placement.py

### Transaction Costs

`SimulatedExecutor` deducts per-contract commission (default $0.65/contract/leg = $2.60 per IC round-trip) and models slippage as a fraction of bid-ask spread. Configurable via `CostConfig`.

### Execution Realism

- Fill at bid (sell) or ask (buy) — worst-case realistic
- `VolumeAwareFill` slides price toward unfavorable side for low-volume contracts
- No partial fills (assume full fills for v2)
- No assignment risk modeling (v2)

## Signal Mesh Protocol

### Adding a Signal

A new signal is a handler that:
1. Declares its frequency (daily, every tick, on event)
2. Reads from Market Snapshot and/or external data
3. Writes to the Signal Mesh: `mesh.update(signal_name, value, half_life)`

The Strategy Allocator reads the mesh as a composite score. New signals are added by registering a handler — the allocator never changes.

### Regime-Conditional Weights

Weight table indexed by regime:

```python
weights = {
    "normal":          {"vix_regime": 0.20, "iv_rank": 0.15, "pead": 0.15, ...},
    "high_correlation": {"vix_regime": 0.30, "iv_rank": 0.10, "pead": 0.02, ...},
    "crisis":          {"vix_regime": 0.40, "iv_rank": 0.05, "pead": 0.00, ...},
}
```

The regime layer (macro + vol signals) determines which column to use. For v2, there's effectively one regime ("normal") since we'll only have VIX + IV rank.

### Decay

Each signal's contribution decays exponentially: `value × weight × exp(-t/half_life)`. Half-lives from the edges catalog:

| Signal | Half-Life |
|--------|-----------|
| GEX sign | ~2 days |
| VIX term structure | ~5 days |
| HY spread acceleration | ~10 days |
| PEAD | ~15 days |
| Insider cluster buy | ~45 days |

### Composite Score

The allocator computes: `score = Σ (signal_value × regime_weight × decay_factor)` across all active signals. The score maps to a trade decision:
- `score > threshold` → enter IC (v2)
- `score` also modulates position size (v2)
- `score` allocates across strategy families (v5)

## Walk-Forward Validation

Rolling walk-forward for signal validation:
- Train on 2 years of data
- Test on next 6 months
- Slide forward, repeat across full dataset
- Multiple out-of-sample periods — harder to overfit

Strict temporal boundaries: train data ends before test data begins. DataProvider enforces a `freeze_at` timestamp that prevents reads past the boundary.

Built into the engine but not the first thing that runs — baseline backtest (IC with no signals) comes first.

## Module Structure

```
kotorid/
  clock.py              # BacktestClock, LiveClock (ABC: Clock)
  handlers.py           # Handler ABC + frequency declarations
  engine.py             # Main loop: clock.tick() → run handlers

  data/
    provider.py         # DataProvider ABC
    parquet_provider.py  # Reads daily + intraday Parquet
    tradier_provider.py  # Wraps current tradier_client.py
    polygon_cache.py     # Fetch-on-demand + cache to Parquet
    ingest.py            # Download philippdubach, FRED, earnings calendar

  execution/
    executor.py          # OrderExecutor ABC
    simulated.py         # Fill at bid/ask, apply costs, slippage
    tradier_executor.py  # Wraps current order_placement.py
    cost.py              # CostConfig, transaction cost models

  signals/
    mesh.py              # SignalMesh shared state + decay + validation
    regime.py            # Refactored regime_engine.py
    iv_rank.py           # Refactored iv_engine.py
    black_scholes.py     # Greeks computation from option prices

  strategy/
    config.py            # ICConfig, DirectionalConfig, VolBuyConfig
    allocator.py         # StrategyAllocator (reads mesh → trade decisions)
    ic_strategy.py       # IC-specific logic (candidate scan, position mgmt)

  portfolio/
    portfolio.py         # Positions, cash, equity curve, trade log
    risk.py              # Risk gates (MaxDelta, MaxDrawdown, etc.)

  analytics/
    stats.py             # Sharpe, drawdown, win rate, signal attribution
    compare.py           # A/B: baseline vs with-signal overlay
```

Existing files (`candidate_scan.py`, `position_monitor.py`, `ic_sync.py`, `jobs.py`) get decomposed into handlers and strategy logic.

## Risk Mitigations

From the architecture review — issues that could produce incorrect results:

| Risk | Mitigation |
|------|-----------|
| Missing strikes in option chain data | Pre-validate chains for complete strike ladders; fail loudly |
| Overnight gap risk invisible to clock | Inject synthetic gap tick at market open |
| IV rank divergence backtest vs live | Extract into parameterized function with configurable window |
| Signal mesh NaN/Inf from bad inputs | Validate on every update; reject and log |
| Walk-forward data leakage | `freeze_at` timestamp in DataProvider |
| No transaction costs | CostConfig with realistic Tradier rates |
| Handler ordering dependencies | Strictly sequential pipeline; invariant checks after each handler |
| Stale Polygon cache | Schema version in Parquet files; reject mismatched versions |

## v2 Implementation Scope

What gets built first:

1. **Clock + Handler framework** — BacktestClock, Handler ABC, Engine loop
2. **Data ingest** — Download philippdubach dataset, FRED signals
3. **ParquetProvider** — Read daily options data
4. **SimulatedExecutor** — Fill at bid/ask with transaction costs
5. **Portfolio** — Track positions, cash, equity curve, trade log
6. **IC Strategy handler** — Replay candidate scan + position management from ICConfig
7. **Baseline backtest** — Run IC strategy with no signals, measure Sharpe/drawdown
8. **Signal mesh skeleton** — Mesh state, VIX regime signal, allocator reads mesh
9. **A/B comparison** — Baseline vs IC + VIX regime filter
10. **Analytics** — Stats, signal attribution, comparison report

**Deferred to v2.1 (after baseline proves out):** Polygon intraday integration, Black-Scholes Greeks computation, intraday position management (stop-loss, delta adjustments at 15-min resolution).

**Deferred to v3+:** Directional strategies, vol-buying, multi-family allocation, walk-forward harness.
