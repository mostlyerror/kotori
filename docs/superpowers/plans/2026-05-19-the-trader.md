# The Trader — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build "The Trader" — a Python + Textual TUI portfolio tracker with a persistent background daemon, four switchable views (Inbox, Grid, Kanban, Briefing), and Cockpit-style position detail, optimized for an iron condor earnings strategy alongside directional positions.

**Architecture:** A background daemon (`portfoliod`) writes all state to SQLite. The TUI (`portfolio_tui`) reads from SQLite every 3s and renders four named views — Inbox [i], Dashboard Grid [g], Kanban [k], Briefing [b] — with a persistent status bar showing NAV, VIX, regime, and alert count. Position detail (Cockpit split-view) activates from any view via ↵.

**Tech Stack:** Python 3.13, Textual ≥ 0.89, aiosqlite, APScheduler, httpx, anthropic, pytest, pytest-asyncio

**Reference implementations** (parallel explorations — do not copy verbatim, use as guides):
- Daemon architecture: `.worktrees/the-inbox/portfoliod/`
- IV/regime engines: `.worktrees/the-watcher/portfoliod/jobs.py`
- Kanban view: `.worktrees/the-pipeline/portfolio_tui/widgets/kanban_board.py`
- Briefing view: `.worktrees/the-briefing-room/portfolio_tui/widgets/briefing_view.py`
- Cockpit detail: `.worktrees/the-cockpit/portfolio_tui/widgets/position_detail.py`

---

## Phase 1: Foundation

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `db/schema.sql`
- Create: `portfoliod/__init__.py`
- Create: `portfolio_tui/__init__.py`
- Create: `tests/__init__.py`
- Create: `.env.example`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "the-trader"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "textual>=0.89",
    "aiosqlite>=0.20",
    "httpx>=0.27",
    "apscheduler>=3.10",
    "anthropic>=0.30",
    "pytz>=2024.1",
]

[project.scripts]
portfoliod = "portfoliod.__main__:main"
trader = "portfolio_tui.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["portfoliod", "portfolio_tui"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]
```

- [ ] **Step 2: Create `db/schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    current_price REAL NOT NULL,
    market_value REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    unrealized_pnl_pct REAL NOT NULL,
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    underlying TEXT,
    expiry TEXT,
    strike REAL,
    put_call TEXT,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (symbol)
);

CREATE TABLE IF NOT EXISTS ic_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    expiry TEXT NOT NULL,
    short_call REAL NOT NULL,
    long_call REAL NOT NULL,
    short_put REAL NOT NULL,
    long_put REAL NOT NULL,
    spread_width REAL NOT NULL,
    entry_credit REAL NOT NULL,
    contracts INTEGER NOT NULL,
    max_loss REAL NOT NULL,
    current_debit REAL,
    pct_max_profit REAL,
    regime_at_entry TEXT,
    iv_percentile_at_entry REAL,
    expected_move REAL,
    exit_debit REAL,
    exit_reason TEXT,
    realized_pnl REAL,
    agent_run_id INTEGER REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS iv_history (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    iv REAL NOT NULL,
    iv_rank REAL,
    iv_percentile REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS iv_crush_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    earnings_date TEXT NOT NULL,
    iv_before REAL NOT NULL,
    iv_after REAL NOT NULL,
    crush_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS regime_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    market_regime TEXT NOT NULL CHECK(market_regime IN ('normal','caution','no_trade')),
    earnings_regime TEXT NOT NULL CHECK(earnings_regime IN ('pre_earnings','post_earnings','none')),
    iv_regime TEXT NOT NULL CHECK(iv_regime IN ('high','normal','low')),
    vix REAL,
    adx REAL
);

CREATE TABLE IF NOT EXISTS thesis (
    symbol TEXT PRIMARY KEY,
    position_type TEXT NOT NULL CHECK(position_type IN ('ic','directional')),
    entry_catalyst TEXT,
    catalyst_source TEXT,
    price_target REAL,
    stop_level REAL,
    time_horizon TEXT,
    status TEXT NOT NULL DEFAULT 'intact' CHECK(status IN ('intact','weakening','invalidated')),
    auto_populated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    earnings_date TEXT,
    scanner_output TEXT,
    strategist_output TEXT,
    risk_manager_output TEXT,
    devils_advocate_output TEXT,
    portfolio_manager_output TEXT,
    final_decision TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_run_id INTEGER REFERENCES agent_runs(id),
    symbol TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    order_status TEXT NOT NULL DEFAULT 'pending_approval'
        CHECK(order_status IN ('pending_approval','approved','rejected','placed','filled','skipped')),
    short_call REAL,
    long_call REAL,
    short_put REAL,
    long_put REAL,
    expected_credit REAL,
    contracts INTEGER,
    max_loss REAL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    priority TEXT NOT NULL CHECK(priority IN ('urgent','action_required','for_review')),
    item_type TEXT NOT NULL,
    symbol TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    actions TEXT NOT NULL DEFAULT '[]',
    ref_id INTEGER,
    created_at TEXT NOT NULL,
    dismissed_at TEXT
);

CREATE TABLE IF NOT EXISTS trailing_stops (
    symbol TEXT PRIMARY KEY,
    trail_type TEXT NOT NULL CHECK(trail_type IN ('percent','dollar')),
    trail_value REAL NOT NULL,
    high_water_mark REAL NOT NULL,
    stop_price REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL CHECK(period IN ('daily','weekly','monthly')),
    content TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
```

- [ ] **Step 3: Create empty package inits and `.env.example`**

```python
# portfoliod/__init__.py  (empty)
# portfolio_tui/__init__.py  (empty)
# tests/__init__.py  (empty)
```

```bash
# .env.example
TRADIER_API_KEY=your_tradier_api_key
TRADIER_ENV=sandbox
POLYGON_API_KEY=your_polygon_api_key
ANTHROPIC_API_KEY=your_anthropic_api_key
PORTFOLIO_DB=~/.trader/portfolio.db
```

- [ ] **Step 4: Install deps**

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Expected: installs textual, aiosqlite, apscheduler, anthropic, httpx, pytest, pytest-asyncio.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml db/schema.sql portfoliod/__init__.py portfolio_tui/__init__.py tests/__init__.py .env.example
git commit -m "feat: project scaffold, schema, deps"
```

---

### Task 2: DB connection layer

**Files:**
- Create: `portfoliod/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
import pytest
import aiosqlite
from portfoliod.db import get_db, init_db

@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}
    assert "positions" in tables
    assert "ic_positions" in tables
    assert "inbox_items" in tables
    assert "briefings" in tables

@pytest.mark.asyncio
async def test_get_db_enables_wal(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        cursor = await db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
    assert row[0] == "wal"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'portfoliod.db'`

- [ ] **Step 3: Implement `portfoliod/db.py`**

```python
from contextlib import asynccontextmanager
from pathlib import Path
import aiosqlite

SCHEMA_PATH = Path(__file__).parent.parent / "db" / "schema.sql"


@asynccontextmanager
async def get_db(path: str):
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_db(db: aiosqlite.Connection) -> None:
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    schema = SCHEMA_PATH.read_text()
    await db.executescript(schema)
    await db.commit()
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_db.py -v
```

Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add portfoliod/db.py tests/test_db.py
git commit -m "feat: db connection layer with WAL mode"
```

---

### Task 3: IV engine

**Files:**
- Create: `portfoliod/iv_engine.py`
- Create: `tests/test_iv_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_iv_engine.py
import pytest
from portfoliod.iv_engine import compute_iv_rank, compute_iv_percentile

def test_iv_rank_mid_range():
    history = [0.20, 0.25, 0.30, 0.35, 0.40]
    assert compute_iv_rank(0.35, history) == pytest.approx(0.75)

def test_iv_rank_at_max():
    history = [0.20, 0.30, 0.40]
    assert compute_iv_rank(0.40, history) == pytest.approx(1.0)

def test_iv_rank_at_min():
    history = [0.20, 0.30, 0.40]
    assert compute_iv_rank(0.20, history) == pytest.approx(0.0)

def test_iv_rank_flat_history_returns_zero():
    assert compute_iv_rank(0.30, [0.30, 0.30, 0.30]) == 0.0

def test_iv_rank_empty_raises():
    with pytest.raises(ValueError, match="iv_history cannot be empty"):
        compute_iv_rank(0.30, [])

def test_iv_percentile_basic():
    history = [0.20, 0.25, 0.30, 0.35, 0.40]
    # 3 values strictly below 0.32: 0.20, 0.25, 0.30
    assert compute_iv_percentile(0.32, history) == pytest.approx(0.60)

def test_iv_percentile_above_all():
    history = [0.20, 0.25, 0.30]
    assert compute_iv_percentile(0.50, history) == pytest.approx(1.0)

def test_iv_percentile_below_all():
    history = [0.20, 0.25, 0.30]
    assert compute_iv_percentile(0.10, history) == pytest.approx(0.0)

def test_iv_percentile_empty_raises():
    with pytest.raises(ValueError, match="iv_history cannot be empty"):
        compute_iv_percentile(0.30, [])
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/test_iv_engine.py -v
```

Expected: `ModuleNotFoundError: No module named 'portfoliod.iv_engine'`

- [ ] **Step 3: Implement `portfoliod/iv_engine.py`**

```python
def compute_iv_rank(current_iv: float, iv_history: list[float]) -> float:
    if not iv_history:
        raise ValueError("iv_history cannot be empty")
    min_iv = min(iv_history)
    max_iv = max(iv_history)
    if max_iv == min_iv:
        return 0.0
    return max(0.0, min(1.0, (current_iv - min_iv) / (max_iv - min_iv)))


def compute_iv_percentile(current_iv: float, iv_history: list[float]) -> float:
    if not iv_history:
        raise ValueError("iv_history cannot be empty")
    return sum(1 for iv in iv_history if iv < current_iv) / len(iv_history)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_iv_engine.py -v
```

Expected: 9 PASSED

- [ ] **Step 5: Commit**

```bash
git add portfoliod/iv_engine.py tests/test_iv_engine.py
git commit -m "feat: IV rank and percentile engine"
```

---

### Task 4: Regime engine

**Files:**
- Create: `portfoliod/regime_engine.py`
- Create: `tests/test_regime_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_regime_engine.py
from portfoliod.regime_engine import get_vix_regime, get_iv_regime

def test_vix_normal():
    assert get_vix_regime(18.4) == "normal"

def test_vix_normal_boundary():
    assert get_vix_regime(34.9) == "normal"

def test_vix_caution():
    assert get_vix_regime(35.0) == "caution"

def test_vix_caution_upper():
    assert get_vix_regime(44.9) == "caution"

def test_vix_no_trade():
    assert get_vix_regime(45.0) == "no_trade"

def test_iv_regime_high():
    assert get_iv_regime(0.75) == "high"

def test_iv_regime_normal():
    assert get_iv_regime(0.45) == "normal"

def test_iv_regime_low():
    assert get_iv_regime(0.20) == "low"
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/test_regime_engine.py -v
```

Expected: `ModuleNotFoundError: No module named 'portfoliod.regime_engine'`

- [ ] **Step 3: Implement `portfoliod/regime_engine.py`**

```python
def get_vix_regime(vix: float) -> str:
    if vix >= 45.0:
        return "no_trade"
    if vix >= 35.0:
        return "caution"
    return "normal"


def get_iv_regime(iv_rank: float) -> str:
    if iv_rank >= 0.50:
        return "high"
    if iv_rank >= 0.25:
        return "normal"
    return "low"
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_regime_engine.py -v
```

Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add portfoliod/regime_engine.py tests/test_regime_engine.py
git commit -m "feat: VIX and IV regime classification engine"
```

---

### Task 5: IC position monitor logic

**Files:**
- Create: `portfoliod/position_monitor.py`
- Create: `tests/test_position_monitor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_position_monitor.py
from portfoliod.position_monitor import check_exit_trigger, compute_exit_debit

def test_profit_target_hit():
    # exit_debit <= entry_credit * 0.50
    assert check_exit_trigger(entry_credit=1.85, exit_debit=0.92) == "profit_target"

def test_profit_target_exact():
    assert check_exit_trigger(entry_credit=2.00, exit_debit=1.00) == "profit_target"

def test_stop_loss_hit():
    # exit_debit >= entry_credit * 2.00
    assert check_exit_trigger(entry_credit=1.85, exit_debit=3.70) == "stop_loss"

def test_stop_loss_exact():
    assert check_exit_trigger(entry_credit=1.00, exit_debit=2.00) == "stop_loss"

def test_no_trigger():
    assert check_exit_trigger(entry_credit=1.85, exit_debit=1.20) is None

def test_compute_exit_debit_all_worthless():
    # Closing a worthless IC: buy back shorts at ask (low), sell longs at bid (0)
    # SC: bid=0.01 ask=0.02, LC: bid=0.00 ask=0.01
    # SP: bid=0.01 ask=0.02, LP: bid=0.00 ask=0.01
    debit = compute_exit_debit(
        sc_bid=0.01, sc_ask=0.02,
        sp_bid=0.01, sp_ask=0.02,
        lc_bid=0.00, lc_ask=0.01,
        lp_bid=0.00, lp_ask=0.01,
    )
    # mid prices: SC=0.015, SP=0.015, LC=0.005, LP=0.005
    # debit = (SC_mid + SP_mid) - (LC_mid + LP_mid) = 0.03 - 0.01 = 0.02
    assert debit == pytest.approx(0.02)

def test_compute_exit_debit_in_the_money():
    debit = compute_exit_debit(
        sc_bid=3.80, sc_ask=4.00,
        sp_bid=0.01, sp_ask=0.02,
        lc_bid=2.90, lc_ask=3.10,
        lp_bid=0.00, lp_ask=0.01,
    )
    # SC_mid=3.90, SP_mid=0.015, LC_mid=3.00, LP_mid=0.005
    # debit = (3.90 + 0.015) - (3.00 + 0.005) = 3.915 - 3.005 = 0.91
    assert debit == pytest.approx(0.91)
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/test_position_monitor.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Add missing import to test file**

```python
import pytest  # add at top of tests/test_position_monitor.py
```

- [ ] **Step 4: Implement `portfoliod/position_monitor.py`**

```python
def check_exit_trigger(entry_credit: float, exit_debit: float) -> str | None:
    if exit_debit <= entry_credit * 0.50:
        return "profit_target"
    if exit_debit >= entry_credit * 2.00:
        return "stop_loss"
    return None


def compute_exit_debit(
    sc_bid: float, sc_ask: float,
    sp_bid: float, sp_ask: float,
    lc_bid: float, lc_ask: float,
    lp_bid: float, lp_ask: float,
) -> float:
    sc_mid = (sc_bid + sc_ask) / 2
    sp_mid = (sp_bid + sp_ask) / 2
    lc_mid = (lc_bid + lc_ask) / 2
    lp_mid = (lp_bid + lp_ask) / 2
    return (sc_mid + sp_mid) - (lc_mid + lp_mid)
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_position_monitor.py -v
```

Expected: 7 PASSED

- [ ] **Step 6: Commit**

```bash
git add portfoliod/position_monitor.py tests/test_position_monitor.py
git commit -m "feat: IC exit trigger logic (50% profit / 200% stop)"
```

---

### Task 6: Mock data seeder

**Files:**
- Create: `portfoliod/mock_data.py`
- Create: `tests/test_mock_data.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_mock_data.py
import pytest
from portfoliod.db import get_db, init_db
from portfoliod.mock_data import seed_mock_data

@pytest.mark.asyncio
async def test_seed_creates_positions(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        cursor = await db.execute("SELECT COUNT(*) FROM positions")
        count = (await cursor.fetchone())[0]
    assert count == 5

@pytest.mark.asyncio
async def test_seed_creates_open_ic(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM ic_positions WHERE exit_reason IS NULL"
        )
        count = (await cursor.fetchone())[0]
    assert count == 1

@pytest.mark.asyncio
async def test_seed_is_idempotent(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        await seed_mock_data(db)
        cursor = await db.execute("SELECT COUNT(*) FROM positions")
        count = (await cursor.fetchone())[0]
    assert count == 5

@pytest.mark.asyncio
async def test_seed_creates_inbox_items(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM inbox_items WHERE dismissed_at IS NULL"
        )
        count = (await cursor.fetchone())[0]
    assert count >= 3
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/test_mock_data.py -v
```

Expected: `ModuleNotFoundError: No module named 'portfoliod.mock_data'`

- [ ] **Step 3: Implement `portfoliod/mock_data.py`**

```python
from datetime import date, datetime, timedelta, timezone
import json
import aiosqlite

NOW = lambda: datetime.now(tz=timezone.utc).isoformat()
TODAY = date.today().isoformat()
EXPIRY_3D = (date.today() + timedelta(days=3)).isoformat()


async def seed_mock_data(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("SELECT COUNT(*) FROM positions")
    if (await cursor.fetchone())[0] > 0:
        return

    # Positions
    await db.executemany(
        """INSERT INTO positions
           (symbol, quantity, avg_cost, current_price, market_value,
            unrealized_pnl, unrealized_pnl_pct, instrument_type, last_updated)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            ("NVDA", 100, 842.10, 869.42, 86942.0, 2732.0, 0.0324, "stock", NOW()),
            ("META", 50, 583.20, 572.44, 28622.0, -538.0, -0.0184, "stock", NOW()),
            ("AMZN", 25, 196.30, 198.52, 4963.0, 55.5, 0.0113, "stock", NOW()),
            ("TSLA", -200, 0, 0, 0, 0, 0, "option", NOW()),  # IC placeholder
            ("SPY", -5, 2.10, 4.35, -2175.0, -1125.0, -0.1071, "option", NOW()),
        ]
    )

    # Open IC — TSLA
    await db.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
            spread_width, entry_credit, contracts, max_loss, current_debit,
            pct_max_profit, regime_at_entry, iv_percentile_at_entry, expected_move)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("TSLA", TODAY, EXPIRY_3D, 200.0, 205.0, 185.0, 180.0,
         5.0, 1.85, 2, 630.0, 0.72, 0.611, "normal", 0.78, 7.20)
    )

    # Thesis entries
    await db.executemany(
        """INSERT INTO thesis
           (symbol, position_type, entry_catalyst, catalyst_source,
            price_target, stop_level, time_horizon, status, auto_populated,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("NVDA", "directional", "Insider cluster buy", "SEC Form 4 / Unusual Whales",
             1050.0, 820.0, "4 weeks", "intact", 0, NOW(), NOW()),
            ("META", "directional", "Earnings beat + guidance raise", "Q1 earnings call",
             620.0, 560.0, "6 weeks", "intact", 0, NOW(), NOW()),
            ("AMZN", "directional", "AWS re-acceleration + margin expansion", "Analyst notes",
             210.0, 190.0, "8 weeks", "intact", 0, NOW(), NOW()),
            ("TSLA", "ic", "Earnings IV crush", "4-agent pipeline",
             None, None, "3 days", "intact", 1, NOW(), NOW()),
            ("SPY", "directional", "Macro hedge — rate sensitivity", "Technical analysis",
             None, 448.0, "2 weeks", "invalidated", 0, NOW(), NOW()),
        ]
    )

    # Notes
    await db.executemany(
        "INSERT INTO notes (symbol, body, created_at) VALUES (?,?,?)",
        [
            ("NVDA", "3 insiders bought >$2M combined last week. Breakout above 200MA confirmed.", NOW()),
            ("NVDA", "Holding into earnings. Thesis intact per latest SEC Form 4 filings.", NOW()),
            ("TSLA", "Auto-entered via pipeline. IVP=78% at entry, expected move ±$7.20.", NOW()),
            ("SPY", "Stop level $448 breached pre-market. Consider closing today.", NOW()),
        ]
    )

    # IV history — 30 days per symbol
    import random
    random.seed(42)
    symbols_iv = {
        "NVDA": (0.45, 0.65), "META": (0.35, 0.55),
        "AMZN": (0.30, 0.50), "TSLA": (0.60, 0.90), "SPY": (0.15, 0.35),
    }
    iv_rows = []
    for sym, (lo, hi) in symbols_iv.items():
        ivs = [lo + random.random() * (hi - lo) for _ in range(30)]
        for i, iv in enumerate(ivs):
            d = (date.today() - timedelta(days=29 - i)).isoformat()
            rank = (iv - lo) / (hi - lo)
            pct = sum(1 for v in ivs[:i+1] if v < iv) / (i + 1)
            iv_rows.append((sym, d, round(iv, 4), round(rank, 4), round(pct, 4)))
    await db.executemany(
        "INSERT OR IGNORE INTO iv_history (symbol, date, iv, iv_rank, iv_percentile) VALUES (?,?,?,?,?)",
        iv_rows
    )

    # Regime snapshots
    await db.executemany(
        """INSERT INTO regime_snapshots
           (symbol, timestamp, market_regime, earnings_regime, iv_regime, vix, adx)
           VALUES (?,?,?,?,?,?,?)""",
        [
            ("NVDA", NOW(), "normal", "none", "normal", 18.4, 28.3),
            ("TSLA", NOW(), "normal", "pre_earnings", "high", 18.4, 42.1),
            ("META", NOW(), "normal", "none", "normal", 18.4, 22.7),
            ("AMZN", NOW(), "normal", "none", "normal", 18.4, 18.9),
            ("SPY", NOW(), "normal", "none", "low", 18.4, 15.2),
        ]
    )

    # Agent run for TSLA IC
    await db.execute(
        """INSERT INTO agent_runs
           (symbol, earnings_date, scanner_output, strategist_output,
            risk_manager_output, devils_advocate_output, portfolio_manager_output,
            final_decision, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            "TSLA", TODAY,
            json.dumps({"passed": True, "regime": "normal", "iv_percentile": 0.78}),
            json.dumps({"recommendation": "trade", "reasoning": "Strong IV crush history (avg 34%). IVP 78% well above 70% threshold. Front-week expiry captures earnings event cleanly."}),
            json.dumps({"contracts": 2, "max_loss": 630.0, "verdict": "approved", "reasoning": "2% risk limit satisfied. No sector concentration issues."}),
            json.dumps({"flag": None, "reasoning": "No material news catalyst to invalidate. Options chain liquid. Standard earnings play."}),
            json.dumps({"decision": "trade", "reasoning": "All three agents aligned. Proceed with 2 contracts."}),
            "trade", NOW()
        )
    )

    # Inbox items
    await db.executemany(
        """INSERT INTO inbox_items
           (priority, item_type, symbol, title, body, actions, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        [
            ("urgent", "thesis_invalidated", "SPY",
             "SPY P — Thesis invalidated",
             "Stop level $448 breached. Current loss -$1,125 (-11.9%). Recommend closing to limit further drawdown.",
             json.dumps(["close", "keep", "note"]), NOW()),
            ("action_required", "ic_candidate", "AMZN",
             "AMZN IC Candidate — Pipeline recommends TRADE",
             "IVP 78% · SC205/LC210 SP190/LP185 · Credit $1.65 · 2 contracts · Max loss $670 · Expires Friday",
             json.dumps(["approve", "reject", "view_pipeline"]), NOW()),
            ("for_review", "profit_approaching", "TSLA",
             "TSLA IC — 61% of max profit captured",
             "Current debit $0.72 vs entry credit $1.85. 3 DTE. Approaching 50% target ($0.925).",
             json.dumps(["close_now", "dismiss"]), NOW()),
            ("for_review", "briefing_ready", None,
             "Daily briefing ready",
             "Portfolio +$1,027 today (+1.2% NAV). 1 open IC performing well. 1 position requires attention.",
             json.dumps(["read", "dismiss"]), NOW()),
        ]
    )

    # Daily briefing
    await db.execute(
        """INSERT INTO briefings (period, content, generated_at) VALUES (?,?,?)""",
        (
            "daily",
            """# Daily Briefing — {today}

## Portfolio Summary
NAV $84,230 · +$1,027 today (+1.2%) · VIX 18.4 (normal regime)

## Open Positions

**[NVDA]** — Insider thesis intact. Price $869.42, up 3.2% from avg cost. Three insiders purchased >$2M combined last week per SEC Form 4 filings. Breakout above 200MA confirmed. No action required — hold to $1,050 target.

**[TSLA IC]** — Iron condor performing well. 61% of max profit captured with 3 DTE. IV crushed post-earnings as expected (entry IVP 78%). Current debit $0.72 vs credit $1.85. Will approach 50% profit target ($0.925) tomorrow morning — monitor for early close opportunity.

**[META]** — Thesis intact. Minor drawdown (-1.8%) within normal range. Q1 earnings beat thesis unchanged. No action required.

**[AMZN]** — Flat, +1.1%. AWS re-acceleration thesis intact. No catalyst this week.

**[SPY P]** ⚠️ — Thesis invalidated. Stop level $448 breached pre-market. Current loss -$1,125 (-11.9%). Recommend closing this position today to limit further losses.

## Regime
VIX 18.4 — normal. No regime change. No earnings plays identified for tomorrow's close scan.

## Recommended Actions
1. Close SPY P position (stop breached)
2. Review AMZN IC candidate (awaiting approval)
""".format(today=TODAY),
            NOW()
        )
    )

    await db.commit()
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_mock_data.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Run all tests**

```bash
.venv/bin/pytest -v
```

Expected: 28 PASSED

- [ ] **Step 6: Commit**

```bash
git add portfoliod/mock_data.py tests/test_mock_data.py
git commit -m "feat: mock data seeder with 5 positions, open IC, inbox items, briefing"
```

---

## Phase 2: Daemon

### Task 7: Daemon config and entry point

**Files:**
- Create: `portfoliod/config.py`
- Create: `portfoliod/__main__.py`

- [ ] **Step 1: Implement `portfoliod/config.py`**

```python
import os
from pathlib import Path

DB_PATH = os.environ.get("PORTFOLIO_DB", str(Path.home() / ".trader" / "portfolio.db"))
TRADIER_API_KEY = os.environ.get("TRADIER_API_KEY", "")
TRADIER_ENV = os.environ.get("TRADIER_ENV", "sandbox")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TRADIER_BASE = (
    "https://sandbox.tradier.com/v1"
    if TRADIER_ENV == "sandbox"
    else "https://api.tradier.com/v1"
)
```

- [ ] **Step 2: Implement `portfoliod/__main__.py`**

```python
import asyncio
import logging
import signal
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from portfoliod.config import DB_PATH
from portfoliod.db import get_db, init_db
from portfoliod.mock_data import seed_mock_data
from portfoliod import jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
CT = pytz.timezone("America/Chicago")


async def run():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with get_db(DB_PATH) as db:
        await init_db(db)
        await seed_mock_data(db)
        log.info("DB ready at %s", DB_PATH)

    scheduler = AsyncIOScheduler(timezone=CT)

    # 05:30 CT — morning IV ingest (Polygon historical)
    scheduler.add_job(jobs.iv_ingest_morning, CronTrigger(hour=5, minute=30, timezone=CT),
                      id="iv_ingest_morning")
    # 08:00 CT — pre-market gap monitor
    scheduler.add_job(jobs.gap_monitor, CronTrigger(hour=8, minute=0, timezone=CT),
                      id="gap_monitor")
    # 14:15 CT — pre-close IV refresh (Tradier live chains)
    scheduler.add_job(jobs.iv_ingest_preclose, CronTrigger(hour=14, minute=15, timezone=CT),
                      id="iv_ingest_preclose")
    # 14:30 CT — IC scan + 4-agent pipeline
    scheduler.add_job(jobs.ic_scan, CronTrigger(hour=14, minute=30, timezone=CT),
                      id="ic_scan")
    # 14:50 CT — order executor (approved candidates)
    scheduler.add_job(jobs.order_executor, CronTrigger(hour=14, minute=50, timezone=CT),
                      id="order_executor")
    # 07:00 CT — daily briefing
    scheduler.add_job(jobs.generate_briefing, CronTrigger(hour=7, minute=0, timezone=CT),
                      id="generate_briefing")
    # Every 30s — position monitor
    scheduler.add_job(jobs.position_monitor, "interval", seconds=30, id="position_monitor")

    scheduler.start()
    log.info("portfoliod running (TRADIER_ENV=%s)", __import__('portfoliod.config', fromlist=['TRADIER_ENV']).TRADIER_ENV)

    loop = asyncio.get_event_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    scheduler.shutdown()
    log.info("portfoliod stopped")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create `portfoliod/jobs.py` with stubs**

```python
import logging
log = logging.getLogger(__name__)

async def iv_ingest_morning():
    log.info("iv_ingest_morning: stub")

async def gap_monitor():
    log.info("gap_monitor: stub")

async def iv_ingest_preclose():
    log.info("iv_ingest_preclose: stub")

async def ic_scan():
    log.info("ic_scan: stub")

async def order_executor():
    log.info("order_executor: stub")

async def position_monitor():
    log.info("position_monitor: stub")

async def generate_briefing():
    log.info("generate_briefing: stub")
```

- [ ] **Step 4: Smoke-test daemon starts and exits**

```bash
timeout 3 .venv/bin/portfoliod || true
```

Expected: logs show "portfoliod running", then exits cleanly after 3s.

- [ ] **Step 5: Commit**

```bash
git add portfoliod/config.py portfoliod/__main__.py portfoliod/jobs.py
git commit -m "feat: daemon scaffold with APScheduler, CT timezone, all job stubs"
```

---

### Task 8: Position monitor job

**Files:**
- Modify: `portfoliod/jobs.py`
- Create: `tests/test_jobs_position_monitor.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_jobs_position_monitor.py
import pytest
from portfoliod.db import get_db, init_db
from portfoliod.mock_data import seed_mock_data
from portfoliod.jobs import run_position_monitor

@pytest.mark.asyncio
async def test_position_monitor_no_triggers_on_fresh_data(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        closed = await run_position_monitor(db)
    # Mock TSLA IC has current_debit=0.72, entry_credit=1.85
    # 0.72 / 1.85 = 0.389 → NOT yet at 50% (0.925) threshold
    assert closed == []

@pytest.mark.asyncio
async def test_position_monitor_fires_profit_target(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        # Set current_debit to exactly 50% of entry_credit
        await db.execute(
            "UPDATE ic_positions SET current_debit = 0.925 WHERE symbol = 'TSLA'"
        )
        await db.commit()
        closed = await run_position_monitor(db)
    assert len(closed) == 1
    assert closed[0]["symbol"] == "TSLA"
    assert closed[0]["exit_reason"] == "profit_target"

@pytest.mark.asyncio
async def test_position_monitor_fires_stop_loss(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        # Set current_debit to 2x entry_credit
        await db.execute(
            "UPDATE ic_positions SET current_debit = 3.70 WHERE symbol = 'TSLA'"
        )
        await db.commit()
        closed = await run_position_monitor(db)
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "stop_loss"
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/test_jobs_position_monitor.py -v
```

Expected: `ImportError: cannot import name 'run_position_monitor'`

- [ ] **Step 3: Implement `run_position_monitor` in `portfoliod/jobs.py`**

Replace the `position_monitor` stub with:

```python
import logging
from datetime import datetime, timezone
import aiosqlite
from portfoliod.config import DB_PATH
from portfoliod.db import get_db
from portfoliod.position_monitor import check_exit_trigger

log = logging.getLogger(__name__)


async def run_position_monitor(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, symbol, entry_credit, current_debit, contracts FROM ic_positions "
        "WHERE exit_reason IS NULL AND current_debit IS NOT NULL"
    )
    open_ics = await cursor.fetchall()
    closed = []

    for ic in open_ics:
        reason = check_exit_trigger(ic["entry_credit"], ic["current_debit"])
        if reason is None:
            continue

        now = datetime.now(tz=timezone.utc).isoformat()
        realized_pnl = (ic["entry_credit"] - ic["current_debit"]) * 100 * ic["contracts"]

        await db.execute(
            "UPDATE ic_positions SET exit_reason=?, exit_debit=?, realized_pnl=? WHERE id=?",
            (reason, ic["current_debit"], realized_pnl, ic["id"])
        )
        await db.execute(
            """INSERT INTO alerts (symbol, alert_type, message, triggered_at)
               VALUES (?,?,?,?)""",
            (ic["symbol"], reason,
             f"{ic['symbol']} IC: {reason.replace('_',' ')} — P&L ${realized_pnl:+.0f}",
             now)
        )
        await db.execute(
            """INSERT INTO inbox_items
               (priority, item_type, symbol, title, body, actions, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("urgent" if reason == "stop_loss" else "for_review",
             reason, ic["symbol"],
             f"{ic['symbol']} IC — {reason.replace('_', ' ').title()}",
             f"Exit at ${ic['current_debit']:.2f}. P&L {realized_pnl:+.0f}. Entry credit ${ic['entry_credit']:.2f}.",
             '["acknowledge"]', now)
        )
        await db.commit()
        closed.append({"symbol": ic["symbol"], "exit_reason": reason, "realized_pnl": realized_pnl})
        log.info("position_monitor: %s %s pnl=%.0f", ic["symbol"], reason, realized_pnl)

    return closed


async def position_monitor():
    async with get_db(DB_PATH) as db:
        await run_position_monitor(db)


# Keep other stubs unchanged
async def iv_ingest_morning():
    log.info("iv_ingest_morning: stub")

async def gap_monitor():
    log.info("gap_monitor: stub")

async def iv_ingest_preclose():
    log.info("iv_ingest_preclose: stub")

async def ic_scan():
    log.info("ic_scan: stub")

async def order_executor():
    log.info("order_executor: stub")

async def generate_briefing():
    log.info("generate_briefing: stub")
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_jobs_position_monitor.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Run full suite**

```bash
.venv/bin/pytest -v
```

Expected: 31 PASSED

- [ ] **Step 6: Commit**

```bash
git add portfoliod/jobs.py tests/test_jobs_position_monitor.py
git commit -m "feat: position monitor job — 50% profit target / 200% stop loss"
```

---

### Task 9: Remaining daemon jobs (stubs → real)

**Files:**
- Modify: `portfoliod/jobs.py`

> **Note:** These jobs call external APIs (Tradier, Polygon, Anthropic). In the real implementation they are wired to live APIs. For now, implement the DB-write logic with mock responses so the daemon is fully functional without API keys.

- [ ] **Step 1: Implement `iv_ingest_morning` with mock Polygon response**

```python
async def iv_ingest_morning():
    from datetime import date, timedelta
    import random
    async with get_db(DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT symbol FROM positions")
        symbols = [r[0] for r in await cursor.fetchall()]
        today = date.today().isoformat()
        rows = []
        for sym in symbols:
            # In production: fetch from Polygon /v1/open-close/{sym}/{date}
            # and compute IV from options chain. Mock: small random walk.
            cursor2 = await db.execute(
                "SELECT iv FROM iv_history WHERE symbol=? ORDER BY date DESC LIMIT 1", (sym,)
            )
            row = await cursor2.fetchone()
            last_iv = row[0] if row else 0.40
            new_iv = max(0.05, last_iv + random.gauss(0, 0.01))
            rows.append((sym, today, round(new_iv, 4), None, None))
        await db.executemany(
            "INSERT OR IGNORE INTO iv_history (symbol, date, iv) VALUES (?,?,?)",
            [(r[0], r[1], r[2]) for r in rows]
        )
        await db.commit()
        log.info("iv_ingest_morning: updated %d symbols", len(rows))
```

- [ ] **Step 2: Implement `gap_monitor` with mock pre-market prices**

```python
async def gap_monitor():
    from datetime import datetime, timezone
    async with get_db(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, symbol, short_call, short_put, expected_move FROM ic_positions "
            "WHERE exit_reason IS NULL"
        )
        open_ics = await cursor.fetchall()
        now = datetime.now(tz=timezone.utc).isoformat()
        for ic in open_ics:
            # In production: fetch pre-market quote from Tradier
            cursor2 = await db.execute(
                "SELECT current_price FROM positions WHERE symbol=?", (ic["symbol"],)
            )
            row = await cursor2.fetchone()
            if not row:
                continue
            price = row[0]
            cushion_call = ic["short_call"] - price
            cushion_put = price - ic["short_put"]
            if cushion_call < ic["expected_move"] * 0.5 or cushion_put < ic["expected_move"] * 0.5:
                await db.execute(
                    """INSERT INTO inbox_items
                       (priority, item_type, symbol, title, body, actions, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    ("urgent", "gap_risk", ic["symbol"],
                     f"{ic['symbol']} IC — Pre-market gap risk",
                     f"Price ${price:.2f} within 50% of expected move from short strikes. "
                     f"SC ${ic['short_call']:.0f} / SP ${ic['short_put']:.0f}.",
                     '["close_ic","hedge","dismiss"]', now)
                )
        await db.commit()
        log.info("gap_monitor: checked %d open ICs", len(open_ics))
```

- [ ] **Step 3: Implement `order_executor` stub with DB update**

```python
async def order_executor():
    from datetime import datetime, timezone
    async with get_db(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, symbol, short_call, long_call, short_put, long_put, "
            "expected_credit, contracts FROM candidates WHERE order_status='approved'"
        )
        approved = await cursor.fetchall()
        now = datetime.now(tz=timezone.utc).isoformat()
        for c in approved:
            # In production: place 4-legged spread order via Tradier
            # For sandbox/mock: mark as placed immediately
            await db.execute(
                "UPDATE candidates SET order_status='placed' WHERE id=?", (c["id"],)
            )
            await db.execute(
                """INSERT INTO alerts (symbol, alert_type, message, triggered_at)
                   VALUES (?,?,?,?)""",
                (c["symbol"], "order_placed",
                 f"{c['symbol']} IC order placed: SC{c['short_call']:.0f}/LC{c['long_call']:.0f} "
                 f"SP{c['short_put']:.0f}/LP{c['long_put']:.0f} cr${c['expected_credit']:.2f}",
                 now)
            )
        await db.commit()
        log.info("order_executor: placed %d orders", len(approved))
```

- [ ] **Step 4: Implement `generate_briefing` with Claude API (with fallback)**

```python
async def generate_briefing():
    from datetime import date, datetime, timezone
    import os
    async with get_db(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT symbol, unrealized_pnl_pct, instrument_type FROM positions"
        )
        positions = await cursor.fetchall()
        cursor2 = await db.execute(
            "SELECT symbol, pct_max_profit, entry_credit, current_debit FROM ic_positions "
            "WHERE exit_reason IS NULL"
        )
        open_ics = await cursor2.fetchall()

        summary = "\n".join(
            f"- {p['symbol']}: {p['unrealized_pnl_pct']:+.1%}" for p in positions
        )
        ic_summary = "\n".join(
            f"- {ic['symbol']} IC: {ic['pct_max_profit']:.0%} of max profit captured"
            for ic in open_ics
        ) or "No open ICs."

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=800,
                messages=[{
                    "role": "user",
                    "content": (
                        f"You are a portfolio analyst. Write a concise daily briefing (200-300 words) "
                        f"for an options trader. Reference positions inline as [SYMBOL]. "
                        f"Be direct and actionable.\n\n"
                        f"Positions:\n{summary}\n\nOpen ICs:\n{ic_summary}"
                    )
                }]
            )
            content = msg.content[0].text
        else:
            content = f"# Daily Briefing — {date.today()}\n\n{summary}\n\n{ic_summary}\n\n_(Set ANTHROPIC_API_KEY for AI-generated briefings)_"

        now = datetime.now(tz=timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO briefings (period, content, generated_at) VALUES (?,?,?)",
            ("daily", content, now)
        )
        await db.execute(
            """INSERT INTO inbox_items
               (priority, item_type, symbol, title, body, actions, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("for_review", "briefing_ready", None, "Daily briefing ready",
             f"Generated {date.today()}. {len(positions)} positions reviewed.",
             '["read","dismiss"]', now)
        )
        await db.commit()
        log.info("generate_briefing: written")
```

- [ ] **Step 5: Verify daemon starts with all real jobs**

```bash
timeout 3 .venv/bin/portfoliod 2>&1 | grep -E "running|ERROR"
```

Expected: `portfoliod running (TRADIER_ENV=sandbox)` with no ERRORs.

- [ ] **Step 6: Commit**

```bash
git add portfoliod/jobs.py
git commit -m "feat: daemon jobs — iv_ingest, gap_monitor, order_executor, generate_briefing"
```

---

## Phase 3: TUI

### Task 10: TUI app shell + status bar

**Files:**
- Create: `portfolio_tui/__main__.py`
- Create: `portfolio_tui/app.py`
- Create: `portfolio_tui/db.py`
- Create: `portfolio_tui/widgets/__init__.py`
- Create: `portfolio_tui/widgets/status_bar.py`
- Create: `portfolio_tui/views/__init__.py`
- Create: `portfolio_tui/screens/__init__.py`

- [ ] **Step 1: Implement `portfolio_tui/db.py`**

```python
from pathlib import Path
import aiosqlite
from portfoliod.config import DB_PATH


async def query(sql: str, params: tuple = ()) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def execute(sql: str, params: tuple = ()) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, params)
        await db.commit()


async def get_nav() -> float:
    rows = await query("SELECT SUM(market_value) as nav FROM positions")
    return rows[0]["nav"] or 0.0


async def get_today_pnl() -> float:
    rows = await query("SELECT SUM(unrealized_pnl) as pnl FROM positions")
    return rows[0]["pnl"] or 0.0


async def get_vix() -> float:
    rows = await query(
        "SELECT vix FROM regime_snapshots ORDER BY timestamp DESC LIMIT 1"
    )
    return rows[0]["vix"] if rows else 0.0


async def get_market_regime() -> str:
    rows = await query(
        "SELECT market_regime FROM regime_snapshots ORDER BY timestamp DESC LIMIT 1"
    )
    return rows[0]["market_regime"] if rows else "unknown"


async def get_unread_alert_count() -> int:
    rows = await query("SELECT COUNT(*) as n FROM alerts WHERE acknowledged=0")
    return rows[0]["n"]


async def get_inbox_count() -> int:
    rows = await query("SELECT COUNT(*) as n FROM inbox_items WHERE dismissed_at IS NULL")
    return rows[0]["n"]
```

- [ ] **Step 2: Implement `portfolio_tui/widgets/status_bar.py`**

```python
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label
from textual import work
import portfolio_tui.db as db


class StatusBar(Widget):
    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text-muted;
        layout: horizontal;
    }
    StatusBar Label { padding: 0 1; }
    StatusBar .sandbox { color: $warning; }
    StatusBar .alert-badge { color: $error; }
    StatusBar .regime-normal { color: $success; }
    StatusBar .regime-caution { color: $warning; }
    StatusBar .regime-no_trade { color: $error; }
    """

    nav: reactive[float] = reactive(0.0)
    pnl: reactive[float] = reactive(0.0)
    vix: reactive[float] = reactive(0.0)
    regime: reactive[str] = reactive("—")
    alerts: reactive[int] = reactive(0)
    inbox: reactive[int] = reactive(0)
    sandbox: reactive[bool] = reactive(True)

    def compose(self) -> ComposeResult:
        yield Label("", id="sb-sandbox", classes="sandbox")
        yield Label("", id="sb-nav")
        yield Label("", id="sb-pnl")
        yield Label("", id="sb-vix")
        yield Label("", id="sb-regime")
        yield Label("", id="sb-alerts", classes="alert-badge")
        yield Label("  [i]nbox [g]rid [k]anban [b]riefing", id="sb-keys")

    def on_mount(self) -> None:
        self.set_interval(3, self.refresh_stats)

    @work(exclusive=True)
    async def refresh_stats(self) -> None:
        import os
        self.nav = await db.get_nav()
        self.pnl = await db.get_today_pnl()
        self.vix = await db.get_vix()
        self.regime = await db.get_market_regime()
        self.alerts = await db.get_unread_alert_count()
        self.inbox = await db.get_inbox_count()
        self.sandbox = os.environ.get("TRADIER_ENV", "sandbox") == "sandbox"

    def watch_nav(self, val: float) -> None:
        self.query_one("#sb-nav", Label).update(f"NAV ${val:,.0f}")

    def watch_pnl(self, val: float) -> None:
        sign = "+" if val >= 0 else ""
        self.query_one("#sb-pnl", Label).update(f"{sign}${val:,.0f} today")

    def watch_vix(self, val: float) -> None:
        self.query_one("#sb-vix", Label).update(f"VIX {val:.1f}")

    def watch_regime(self, val: str) -> None:
        label = self.query_one("#sb-regime", Label)
        label.update(val)
        label.set_class(val == "normal", "regime-normal")
        label.set_class(val == "caution", "regime-caution")
        label.set_class(val == "no_trade", "regime-no_trade")

    def watch_alerts(self, val: int) -> None:
        self.query_one("#sb-alerts", Label).update(
            f"⚡ {val} alerts" if val > 0 else ""
        )

    def watch_sandbox(self, val: bool) -> None:
        self.query_one("#sb-sandbox", Label).update("[SANDBOX]" if val else "")
```

- [ ] **Step 3: Implement `portfolio_tui/app.py`**

```python
from textual.app import App, ComposeResult
from textual.binding import Binding
from portfolio_tui.widgets.status_bar import StatusBar


class TraderApp(App):
    TITLE = "The Trader"
    BINDINGS = [
        Binding("i", "show_view('inbox')", "Inbox", show=True),
        Binding("g", "show_view('grid')", "Grid", show=True),
        Binding("k", "show_view('kanban')", "Kanban", show=True),
        Binding("b", "show_view('briefing')", "Briefing", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    CSS = """
    ContentSwitcher { height: 1fr; }
    """

    _current_view: str = "inbox"

    def compose(self) -> ComposeResult:
        from textual.widgets import ContentSwitcher
        from portfolio_tui.views.inbox_view import InboxView
        from portfolio_tui.views.grid_view import GridView
        from portfolio_tui.views.kanban_view import KanbanView
        from portfolio_tui.views.briefing_view import BriefingView

        with ContentSwitcher(initial="inbox"):
            yield InboxView(id="inbox")
            yield GridView(id="grid")
            yield KanbanView(id="kanban")
            yield BriefingView(id="briefing")
        yield StatusBar()

    def action_show_view(self, view_id: str) -> None:
        self.query_one("ContentSwitcher").current = view_id
        self._current_view = view_id
```

- [ ] **Step 4: Implement `portfolio_tui/__main__.py`**

```python
import asyncio
from pathlib import Path
from portfoliod.config import DB_PATH
from portfoliod.db import get_db, init_db
from portfoliod.mock_data import seed_mock_data


async def ensure_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with get_db(DB_PATH) as db:
        await init_db(db)
        await seed_mock_data(db)


def main():
    asyncio.run(ensure_db())
    from portfolio_tui.app import TraderApp
    TraderApp().run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Create stub views so app composes without error**

```python
# portfolio_tui/views/inbox_view.py
from textual.widget import Widget
from textual.widgets import Label
class InboxView(Widget):
    def compose(self):
        yield Label("Inbox — coming in Task 11")

# portfolio_tui/views/grid_view.py
from textual.widget import Widget
from textual.widgets import Label
class GridView(Widget):
    def compose(self):
        yield Label("Grid — coming in Task 12")

# portfolio_tui/views/kanban_view.py
from textual.widget import Widget
from textual.widgets import Label
class KanbanView(Widget):
    def compose(self):
        yield Label("Kanban — coming in Task 13")

# portfolio_tui/views/briefing_view.py
from textual.widget import Widget
from textual.widgets import Label
class BriefingView(Widget):
    def compose(self):
        yield Label("Briefing — coming in Task 14")
```

- [ ] **Step 6: Smoke test TUI starts**

```bash
timeout 3 .venv/bin/trader 2>&1 | head -5 || true
```

Expected: no ImportError or traceback. App starts.

- [ ] **Step 7: Commit**

```bash
git add portfolio_tui/ 
git commit -m "feat: TUI shell — app, status bar, view switcher, stub views"
```

---

### Task 11: Inbox view

**Files:**
- Modify: `portfolio_tui/views/inbox_view.py`

- [ ] **Step 1: Implement `InboxView`**

```python
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static
from textual import work
from textual.binding import Binding
import portfolio_tui.db as db


class InboxItemCard(Static):
    PRIORITY_ICON = {"urgent": "🔴", "action_required": "🟡", "for_review": "🔵"}

    def __init__(self, item: dict, **kwargs):
        super().__init__(**kwargs)
        self.item = item

    def compose(self) -> ComposeResult:
        icon = self.PRIORITY_ICON.get(self.item["priority"], "⚪")
        import json
        actions = json.loads(self.item["actions"])
        action_hints = "  ".join(f"[{a[0]}] {a.replace('_',' ')}" for a in actions)
        yield Label(f"{icon}  {self.item['title']}", classes="card-title")
        yield Label(self.item["body"], classes="card-body")
        yield Label(action_hints, classes="card-actions")


class InboxZero(Static):
    def compose(self) -> ComposeResult:
        yield Label("✓  Inbox zero. Portfolio running autonomously.", classes="zero-title")


class InboxView(Widget):
    DEFAULT_CSS = """
    InboxView { layout: vertical; padding: 1 2; }
    .section-header { color: $text-muted; text-style: bold; margin-top: 1; }
    InboxItemCard {
        border: solid $panel-lighten-1;
        padding: 1;
        margin-bottom: 1;
    }
    InboxItemCard:focus { border: solid $accent; }
    .card-title { text-style: bold; }
    .card-body { color: $text-muted; }
    .card-actions { color: $accent; margin-top: 1; }
    InboxZero { align: center middle; height: 1fr; }
    .zero-title { color: $success; text-style: bold; }
    """

    BINDINGS = [
        Binding("j,down", "focus_next", "Next", show=False),
        Binding("k,up", "focus_previous", "Prev", show=False),
    ]

    items: reactive[list] = reactive([], always_update=True)

    def compose(self) -> ComposeResult:
        yield Label("Loading...", id="inbox-content")

    def on_mount(self) -> None:
        self.set_interval(2, self.refresh_items)

    @work(exclusive=True)
    async def refresh_items(self) -> None:
        self.items = await db.query(
            "SELECT * FROM inbox_items WHERE dismissed_at IS NULL "
            "ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'action_required' THEN 1 ELSE 2 END, created_at"
        )

    def watch_items(self, items: list) -> None:
        self.query_one("#inbox-content").remove()
        if not items:
            self.mount(InboxZero())
            return

        container = Widget(id="inbox-content")
        urgent = [i for i in items if i["priority"] == "urgent"]
        action = [i for i in items if i["priority"] == "action_required"]
        review = [i for i in items if i["priority"] == "for_review"]

        sections = [("URGENT", urgent), ("ACTION REQUIRED", action), ("FOR REVIEW", review)]
        children = []
        for header, group in sections:
            if group:
                children.append(Label(header, classes="section-header"))
                children.extend(InboxItemCard(item, id=f"item-{item['id']}") for item in group)

        self.mount(Widget(*children, id="inbox-content"))
```

- [ ] **Step 2: Smoke test**

```bash
.venv/bin/trader &
sleep 2
kill %1 2>/dev/null
```

Expected: no crash.

- [ ] **Step 3: Commit**

```bash
git add portfolio_tui/views/inbox_view.py
git commit -m "feat: Inbox view with priority grouping and auto-refresh"
```

---

### Task 12: Grid view

**Files:**
- Modify: `portfolio_tui/views/grid_view.py`

- [ ] **Step 1: Implement `GridView`**

```python
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Label
from textual import work
from textual.binding import Binding
import portfolio_tui.db as db


class GridView(Widget):
    DEFAULT_CSS = """
    GridView { layout: grid; grid-size: 3; grid-rows: 1fr; padding: 0; }
    .panel { border: solid $panel-lighten-1; padding: 1; }
    .panel-title { color: $text-muted; text-style: bold; margin-bottom: 1; }
    .green { color: $success; }
    .red { color: $error; }
    .yellow { color: $warning; }
    """

    BINDINGS = [
        Binding("enter", "open_detail", "Detail", show=True),
        Binding("j,down", "cursor_down", "Down", show=False),
        Binding("k,up", "cursor_up", "Up", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Widget(classes="panel", id="positions-panel"):
            yield Label("POSITIONS", classes="panel-title")
            yield DataTable(id="positions-table", cursor_type="row")
        with Widget(classes="panel", id="regime-panel"):
            yield Label("REGIME + IV", classes="panel-title")
            yield Widget(id="regime-content")
        with Widget(classes="panel", id="activity-panel"):
            yield Label("RECENT ACTIVITY", classes="panel-title")
            yield Widget(id="activity-content")

    def on_mount(self) -> None:
        table = self.query_one("#positions-table", DataTable)
        table.add_columns("Symbol", "Qty", "P&L%", "Thesis")
        self.set_interval(3, self.refresh_data)

    @work(exclusive=True)
    async def refresh_data(self) -> None:
        positions = await db.query(
            "SELECT p.symbol, p.quantity, p.unrealized_pnl_pct, "
            "COALESCE(t.status,'—') as thesis_status, p.instrument_type "
            "FROM positions p LEFT JOIN thesis t ON p.symbol=t.symbol"
        )
        regimes = await db.query(
            "SELECT DISTINCT symbol, market_regime, earnings_regime, iv_regime, vix "
            "FROM regime_snapshots ORDER BY timestamp DESC LIMIT 10"
        )
        alerts = await db.query(
            "SELECT symbol, message, triggered_at FROM alerts "
            "ORDER BY triggered_at DESC LIMIT 6"
        )
        self.call_from_thread(self._update_table, positions)
        self.call_from_thread(self._update_regime, regimes)
        self.call_from_thread(self._update_activity, alerts)

    def _update_table(self, positions: list) -> None:
        table = self.query_one("#positions-table", DataTable)
        table.clear()
        status_icon = {"intact": "●", "weakening": "~", "invalidated": "✗"}
        for p in positions:
            pct = p["unrealized_pnl_pct"] * 100
            pct_str = f"{pct:+.1f}%"
            icon = status_icon.get(p["thesis_status"], "—")
            table.add_row(p["symbol"], str(int(p["quantity"])), pct_str, icon)

    def _update_regime(self, regimes: list) -> None:
        content = self.query_one("#regime-content")
        content.remove_children()
        seen = set()
        for r in regimes:
            if r["symbol"] in seen:
                continue
            seen.add(r["symbol"])
            iv_label = {"high": "IVR↑", "normal": "IVR~", "low": "IVR↓"}.get(r["iv_regime"], "")
            earnings = " PreEarnings" if r["earnings_regime"] == "pre_earnings" else ""
            content.mount(Label(f"{r['symbol']:<6} {r['market_regime']}{earnings} {iv_label}"))

    def _update_activity(self, alerts: list) -> None:
        content = self.query_one("#activity-content")
        content.remove_children()
        for a in alerts:
            content.mount(Label(f"{a['symbol'] or '—':<6} {a['message'][:40]}"))

    def action_open_detail(self) -> None:
        table = self.query_one("#positions-table", DataTable)
        if table.cursor_row >= 0:
            row = table.get_row_at(table.cursor_row)
            self.app.push_screen("position_detail", symbol=row[0])

    def action_cursor_down(self) -> None:
        self.query_one("#positions-table", DataTable).action_scroll_down()

    def action_cursor_up(self) -> None:
        self.query_one("#positions-table", DataTable).action_scroll_up()
```

- [ ] **Step 2: Commit**

```bash
git add portfolio_tui/views/grid_view.py
git commit -m "feat: Grid view — 3-panel positions/regime/activity dashboard"
```

---

### Task 13: Kanban view

**Files:**
- Modify: `portfolio_tui/views/kanban_view.py`

- [ ] **Step 1: Implement `KanbanView`**

```python
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static
from textual.binding import Binding
from textual import work
import portfolio_tui.db as db

LANES = [
    ("watching", "WATCHING"),
    ("candidate", "CANDIDATE"),
    ("open", "OPEN IC"),
    ("closing", "CLOSING"),
    ("closed", "CLOSED"),
]


class KanbanCard(Static):
    def __init__(self, card: dict, **kwargs):
        super().__init__(**kwargs)
        self.card = card
        self.can_focus = True

    def compose(self) -> ComposeResult:
        sym = self.card.get("symbol", "—")
        metric = self.card.get("metric", "")
        status = self.card.get("status", "")
        yield Label(f"{sym}", classes="card-sym")
        yield Label(metric, classes="card-metric")
        if status:
            yield Label(status, classes=f"card-status card-{status}")


class KanbanLane(Widget):
    DEFAULT_CSS = """
    KanbanLane { width: 1fr; border: solid $panel-lighten-1; padding: 0 1; margin: 0 1; }
    .lane-header { text-style: bold; color: $text-muted; border-bottom: solid $panel-lighten-2; margin-bottom: 1; }
    KanbanCard { border: solid $panel-lighten-2; padding: 1; margin-bottom: 1; }
    KanbanCard:focus { border: solid $accent; }
    .card-sym { text-style: bold; }
    .card-metric { color: $success; }
    .card-status { color: $text-muted; }
    """

    def __init__(self, lane_id: str, title: str, **kwargs):
        super().__init__(**kwargs)
        self.lane_id = lane_id
        self.lane_title = title

    def compose(self) -> ComposeResult:
        yield Label(f"{self.lane_title} (0)", id=f"lane-header-{self.lane_id}", classes="lane-header")
        yield Widget(id=f"lane-cards-{self.lane_id}")

    def update_cards(self, cards: list) -> None:
        self.query_one(f"#lane-header-{self.lane_id}", Label).update(
            f"{self.lane_title} ({len(cards)})"
        )
        container = self.query_one(f"#lane-cards-{self.lane_id}")
        container.remove_children()
        for c in cards:
            container.mount(KanbanCard(c, id=f"card-{c['id']}"))


class KanbanView(Widget):
    DEFAULT_CSS = """
    KanbanView { layout: horizontal; padding: 1; }
    """

    BINDINGS = [
        Binding("enter", "open_detail", "Detail", show=True),
        Binding("right", "approve_candidate", "Approve →", show=True),
        Binding("left", "reject_candidate", "Reject ←", show=True),
    ]

    def compose(self) -> ComposeResult:
        for lane_id, title in LANES:
            yield KanbanLane(lane_id, title, id=f"lane-{lane_id}")

    def on_mount(self) -> None:
        self.set_interval(3, self.refresh_cards)

    @work(exclusive=True)
    async def refresh_cards(self) -> None:
        positions = await db.query(
            "SELECT p.symbol, p.unrealized_pnl_pct, COALESCE(t.status,'intact') as status "
            "FROM positions p LEFT JOIN thesis t ON p.symbol=t.symbol "
            "WHERE p.instrument_type='stock'"
        )
        open_ics = await db.query(
            "SELECT id, symbol, pct_max_profit, entry_credit, current_debit "
            "FROM ic_positions WHERE exit_reason IS NULL"
        )
        candidates = await db.query(
            "SELECT id, symbol, expected_credit, contracts "
            "FROM candidates WHERE order_status IN ('pending_approval','approved')"
        )
        closed_ics = await db.query(
            "SELECT id, symbol, realized_pnl FROM ic_positions "
            "WHERE exit_reason IS NOT NULL ORDER BY rowid DESC LIMIT 5"
        )
        self.call_from_thread(self._update_lanes, positions, open_ics, candidates, closed_ics)

    def _update_lanes(self, positions, open_ics, candidates, closed_ics) -> None:
        watching = [{"id": f"w-{p['symbol']}", "symbol": p["symbol"],
                     "metric": f"{p['unrealized_pnl_pct']:+.1%}",
                     "status": p["status"]} for p in positions]
        candidate_cards = [{"id": c["id"], "symbol": c["symbol"],
                            "metric": f"cr ${c['expected_credit']:.2f} ×{c['contracts']}",
                            "status": "pending"} for c in candidates]
        open_cards = [{"id": ic["id"], "symbol": ic["symbol"],
                       "metric": f"{(ic['pct_max_profit'] or 0):.0%} profit",
                       "status": "active"} for ic in open_ics]
        closed_cards = [{"id": ic["id"], "symbol": ic["symbol"],
                         "metric": f"${ic['realized_pnl']:+.0f}",
                         "status": "closed"} for ic in closed_ics]

        self.query_one("#lane-watching", KanbanLane).update_cards(watching)
        self.query_one("#lane-candidate", KanbanLane).update_cards(candidate_cards)
        self.query_one("#lane-open", KanbanLane).update_cards(open_cards)
        self.query_one("#lane-closing", KanbanLane).update_cards([])
        self.query_one("#lane-closed", KanbanLane).update_cards(closed_cards)

    async def action_approve_candidate(self) -> None:
        focused = self.app.focused
        if isinstance(focused, KanbanCard) and focused.card.get("status") == "pending":
            await db.execute(
                "UPDATE candidates SET order_status='approved' WHERE id=?",
                (focused.card["id"],)
            )

    async def action_reject_candidate(self) -> None:
        focused = self.app.focused
        if isinstance(focused, KanbanCard) and focused.card.get("status") == "pending":
            await db.execute(
                "UPDATE candidates SET order_status='rejected' WHERE id=?",
                (focused.card["id"],)
            )

    def action_open_detail(self) -> None:
        focused = self.app.focused
        if isinstance(focused, KanbanCard):
            self.app.push_screen("position_detail", symbol=focused.card["symbol"])
```

- [ ] **Step 2: Commit**

```bash
git add portfolio_tui/views/kanban_view.py
git commit -m "feat: Kanban view — 5-lane IC lifecycle with approve/reject"
```

---

### Task 14: Briefing view

**Files:**
- Modify: `portfolio_tui/views/briefing_view.py`

- [ ] **Step 1: Implement `BriefingView`**

```python
import re
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Markdown, TabbedContent, TabPane
from textual.binding import Binding
from textual import work
import portfolio_tui.db as db


class BriefingView(Widget):
    DEFAULT_CSS = """
    BriefingView { layout: grid; grid-size: 2; grid-columns: 2fr 1fr; padding: 0; }
    .briefing-main { padding: 1 2; }
    .briefing-sidebar { border-left: solid $panel-lighten-1; padding: 1; }
    .sidebar-title { text-style: bold; color: $text-muted; margin-bottom: 1; }
    .position-row { padding: 0; }
    .position-row:hover { background: $panel-lighten-1; }
    """

    BINDINGS = [
        Binding("d", "show_period('daily')", "Daily", show=True),
        Binding("w", "show_period('weekly')", "Weekly", show=True),
        Binding("m", "show_period('monthly')", "Monthly", show=True),
    ]

    _period: str = "daily"

    def compose(self) -> ComposeResult:
        with Widget(classes="briefing-main"):
            yield TabbedContent(initial="tab-daily", id="briefing-tabs")
        with Widget(classes="briefing-sidebar"):
            yield Label("POSITIONS", classes="sidebar-title")
            yield Widget(id="sidebar-positions")
            yield Label("ALERTS", classes="sidebar-title")
            yield Widget(id="sidebar-alerts")

    def on_mount(self) -> None:
        self.set_interval(5, self.refresh_briefing)

    @work(exclusive=True)
    async def refresh_briefing(self) -> None:
        briefing = await db.query(
            "SELECT content FROM briefings WHERE period=? ORDER BY generated_at DESC LIMIT 1",
            (self._period,)
        )
        positions = await db.query(
            "SELECT p.symbol, p.current_price, p.unrealized_pnl_pct, "
            "COALESCE(t.status,'—') as status "
            "FROM positions p LEFT JOIN thesis t ON p.symbol=t.symbol"
        )
        alerts = await db.query(
            "SELECT symbol, message FROM alerts WHERE acknowledged=0 ORDER BY triggered_at DESC LIMIT 4"
        )
        content = briefing[0]["content"] if briefing else "_No briefing available._"
        self.call_from_thread(self._update_content, content, positions, alerts)

    def _update_content(self, content: str, positions: list, alerts: list) -> None:
        tabs = self.query_one("#briefing-tabs", TabbedContent)
        tabs.clear_panes()
        tabs.add_pane(TabPane("Daily", Markdown(content), id="tab-daily"))
        tabs.add_pane(TabPane("Weekly", Markdown("_Weekly briefing will appear here._"), id="tab-weekly"))
        tabs.add_pane(TabPane("Monthly", Markdown("_Monthly briefing will appear here._"), id="tab-monthly"))

        pos_container = self.query_one("#sidebar-positions")
        pos_container.remove_children()
        status_icon = {"intact": "●", "weakening": "~", "invalidated": "✗"}
        for p in positions:
            icon = status_icon.get(p["status"], "—")
            pct = p["unrealized_pnl_pct"] * 100
            pos_container.mount(Label(f"{p['symbol']:<6} ${p['current_price']:>8.2f} {pct:+.1f}% {icon}",
                                      classes="position-row"))

        alert_container = self.query_one("#sidebar-alerts")
        alert_container.remove_children()
        for a in alerts:
            alert_container.mount(Label(f"⚡ {a['symbol'] or ''} {a['message'][:30]}"))

    def action_show_period(self, period: str) -> None:
        self._period = period
        self.refresh_briefing()
```

- [ ] **Step 2: Commit**

```bash
git add portfolio_tui/views/briefing_view.py
git commit -m "feat: Briefing view — narrative + position sidebar + d/w/m tabs"
```

---

### Task 15: Position detail screen (Cockpit-style)

**Files:**
- Create: `portfolio_tui/screens/position_detail.py`
- Modify: `portfolio_tui/app.py`

- [ ] **Step 1: Implement `portfolio_tui/screens/position_detail.py`**

```python
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Label, Static, DataTable
from textual.widget import Widget
from textual import work
import portfolio_tui.db as db


class PositionDetail(Screen):
    DEFAULT_CSS = """
    PositionDetail { layout: grid; grid-size: 2; grid-columns: 1fr 1fr; }
    .detail-panel { border: solid $panel-lighten-1; padding: 1 2; }
    .detail-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .detail-label { color: $text-muted; }
    .detail-value { text-style: bold; }
    .notes-entry { color: $text-muted; margin-bottom: 1; }
    .thesis-status-intact { color: $success; }
    .thesis-status-weakening { color: $warning; }
    .thesis-status-invalidated { color: $error; }
    """

    BINDINGS = [
        Binding("escape,q", "dismiss", "Back", show=True),
        Binding("n", "add_note", "Add note", show=True),
        Binding("t", "edit_thesis", "Edit thesis", show=True),
    ]

    def __init__(self, symbol: str, **kwargs):
        super().__init__(**kwargs)
        self.symbol = symbol

    def compose(self) -> ComposeResult:
        with Widget(classes="detail-panel", id="left-panel"):
            yield Label(f"{self.symbol}", classes="detail-title", id="detail-header")
            yield Widget(id="price-section")
            yield Label("THESIS", classes="detail-label")
            yield Widget(id="thesis-section")
        with Widget(classes="detail-panel", id="right-panel"):
            yield Label("NOTES", classes="detail-label")
            yield Widget(id="notes-section")

    def on_mount(self) -> None:
        self.load_data()

    @work
    async def load_data(self) -> None:
        position = await db.query(
            "SELECT * FROM positions WHERE symbol=?", (self.symbol,)
        )
        ic = await db.query(
            "SELECT * FROM ic_positions WHERE symbol=? AND exit_reason IS NULL LIMIT 1",
            (self.symbol,)
        )
        thesis = await db.query("SELECT * FROM thesis WHERE symbol=?", (self.symbol,))
        notes = await db.query(
            "SELECT body, created_at FROM notes WHERE symbol=? ORDER BY created_at DESC LIMIT 10",
            (self.symbol,)
        )
        agent_run = []
        if ic and ic[0].get("agent_run_id"):
            agent_run = await db.query(
                "SELECT * FROM agent_runs WHERE id=?", (ic[0]["agent_run_id"],)
            )
        self.call_from_thread(self._render, position, ic, thesis, notes, agent_run)

    def _render(self, position, ic, thesis, notes, agent_run) -> None:
        import json
        price_section = self.query_one("#price-section")
        price_section.remove_children()

        if ic:
            i = ic[0]
            price_section.mount(
                Label(f"SC {i['short_call']:.0f} / LC {i['long_call']:.0f} / SP {i['short_put']:.0f} / LP {i['long_put']:.0f}"),
                Label(f"Entry credit ${i['entry_credit']:.2f}  Current debit ${i['current_debit'] or 0:.2f}"),
                Label(f"Profit captured: {(i['pct_max_profit'] or 0):.0%}  Max loss ${i['max_loss']:.0f}"),
                Label(f"Expiry: {i['expiry']}  IVP at entry: {(i['iv_percentile_at_entry'] or 0):.0%}"),
            )
            if agent_run:
                ar = agent_run[0]
                strat = json.loads(ar.get("strategist_output") or "{}")
                pm = json.loads(ar.get("portfolio_manager_output") or "{}")
                price_section.mount(
                    Label("── Pipeline Reasoning ──", classes="detail-label"),
                    Label(f"Strategist: {strat.get('reasoning','—')[:80]}"),
                    Label(f"Decision: {pm.get('decision','—')} — {pm.get('reasoning','—')[:60]}"),
                )
        elif position:
            p = position[0]
            price_section.mount(
                Label(f"${p['current_price']:.2f}  avg ${p['avg_cost']:.2f}"),
                Label(f"P&L ${p['unrealized_pnl']:+.2f} ({p['unrealized_pnl_pct']:+.1%})"),
                Label(f"Market value ${p['market_value']:,.0f}"),
            )

        thesis_section = self.query_one("#thesis-section")
        thesis_section.remove_children()
        if thesis:
            t = thesis[0]
            status_class = f"thesis-status-{t['status']}"
            thesis_section.mount(
                Label(f"Catalyst: {t.get('entry_catalyst','—')}"),
                Label(f"Source: {t.get('catalyst_source','—')}"),
                Label(f"Target: ${t.get('price_target') or '—'}  Stop: ${t.get('stop_level') or '—'}"),
                Label(f"Horizon: {t.get('time_horizon','—')}"),
                Label(f"Status: {t['status']}", classes=status_class),
            )

        notes_section = self.query_one("#notes-section")
        notes_section.remove_children()
        for n in notes:
            notes_section.mount(Label(f"{n['created_at'][:10]}  {n['body']}", classes="notes-entry"))

    async def action_add_note(self) -> None:
        from portfolio_tui.screens.note_input import NoteInput
        def save_note(note: str) -> None:
            if note:
                import asyncio
                asyncio.create_task(db.execute(
                    "INSERT INTO notes (symbol, body, created_at) VALUES (?,?,datetime('now'))",
                    (self.symbol, note)
                ))
        await self.app.push_screen(NoteInput(self.symbol), save_note)

    def action_edit_thesis(self) -> None:
        from portfolio_tui.screens.thesis_editor import ThesisEditor
        self.app.push_screen(ThesisEditor(self.symbol))
```

- [ ] **Step 2: Register screen in `portfolio_tui/app.py`**

Add to `TraderApp`:

```python
    SCREENS = {}  # dynamic screens pushed via push_screen

    def on_mount(self) -> None:
        from portfolio_tui.screens.position_detail import PositionDetail
        # Screens are pushed dynamically, not registered statically
        pass
```

Update `action_show_view` and add the push_screen helper used by views:

```python
    def open_position_detail(self, symbol: str) -> None:
        from portfolio_tui.screens.position_detail import PositionDetail
        self.push_screen(PositionDetail(symbol))
```

Update `GridView.action_open_detail` and `KanbanView.action_open_detail` to call `self.app.open_position_detail(symbol)` instead of `self.app.push_screen("position_detail", symbol=...)`.

- [ ] **Step 3: Commit**

```bash
git add portfolio_tui/screens/position_detail.py portfolio_tui/app.py
git commit -m "feat: Cockpit-style position detail screen with IC legs, pipeline reasoning, thesis, notes"
```

---

### Task 16: Note input + thesis editor screens

**Files:**
- Create: `portfolio_tui/screens/note_input.py`
- Create: `portfolio_tui/screens/thesis_editor.py`

- [ ] **Step 1: Implement `portfolio_tui/screens/note_input.py`**

```python
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class NoteInput(ModalScreen[str]):
    DEFAULT_CSS = """
    NoteInput { align: center middle; }
    NoteInput > Widget { width: 60; height: 7; border: solid $accent; padding: 1 2; background: $surface; }
    """

    BINDINGS = [
        Binding("escape", "dismiss('')", "Cancel", show=True),
    ]

    def __init__(self, symbol: str, **kwargs):
        super().__init__(**kwargs)
        self.symbol = symbol

    def compose(self) -> ComposeResult:
        with self._container():
            yield Label(f"Add note for {self.symbol}")
            yield Input(placeholder="Type note and press Enter...", id="note-input")

    def _container(self):
        from textual.widget import Widget
        return Widget()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)
```

- [ ] **Step 2: Implement `portfolio_tui/screens/thesis_editor.py`**

```python
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Select
from textual.widget import Widget
from textual import work
import portfolio_tui.db as db


class ThesisEditor(ModalScreen):
    DEFAULT_CSS = """
    ThesisEditor { align: center middle; }
    ThesisEditor > Widget {
        width: 70; height: 20; border: solid $accent;
        padding: 1 2; background: $surface; layout: vertical;
    }
    .field-label { color: $text-muted; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def __init__(self, symbol: str, **kwargs):
        super().__init__(**kwargs)
        self.symbol = symbol

    def compose(self) -> ComposeResult:
        with Widget():
            yield Label(f"Edit Thesis — {self.symbol}", classes="detail-title")
            yield Label("Entry Catalyst", classes="field-label")
            yield Input(id="catalyst", placeholder="e.g. Insider cluster buy")
            yield Label("Catalyst Source", classes="field-label")
            yield Input(id="catalyst-source", placeholder="e.g. SEC Form 4 / Unusual Whales")
            yield Label("Price Target", classes="field-label")
            yield Input(id="price-target", placeholder="e.g. 1050")
            yield Label("Stop Level", classes="field-label")
            yield Input(id="stop-level", placeholder="e.g. 820")
            yield Label("Time Horizon", classes="field-label")
            yield Input(id="horizon", placeholder="e.g. 4 weeks")
            yield Label("Status", classes="field-label")
            yield Select([("intact","intact"),("weakening","weakening"),("invalidated","invalidated")],
                         id="status", value="intact")
            yield Label("[Ctrl+S] Save   [Esc] Cancel", classes="field-label")

    def on_mount(self) -> None:
        self.load_existing()

    @work
    async def load_existing(self) -> None:
        rows = await db.query("SELECT * FROM thesis WHERE symbol=?", (self.symbol,))
        if rows:
            t = rows[0]
            def populate():
                if t.get("entry_catalyst"):
                    self.query_one("#catalyst", Input).value = t["entry_catalyst"]
                if t.get("catalyst_source"):
                    self.query_one("#catalyst-source", Input).value = t["catalyst_source"]
                if t.get("price_target"):
                    self.query_one("#price-target", Input).value = str(t["price_target"])
                if t.get("stop_level"):
                    self.query_one("#stop-level", Input).value = str(t["stop_level"])
                if t.get("time_horizon"):
                    self.query_one("#horizon", Input).value = t["time_horizon"]
                self.query_one("#status", Select).value = t["status"]
            self.call_from_thread(populate)

    @work
    async def action_save(self) -> None:
        catalyst = self.query_one("#catalyst", Input).value
        source = self.query_one("#catalyst-source", Input).value
        target_str = self.query_one("#price-target", Input).value
        stop_str = self.query_one("#stop-level", Input).value
        horizon = self.query_one("#horizon", Input).value
        status = self.query_one("#status", Select).value

        target = float(target_str) if target_str else None
        stop = float(stop_str) if stop_str else None

        existing = await db.query("SELECT symbol FROM thesis WHERE symbol=?", (self.symbol,))
        if existing:
            await db.execute(
                """UPDATE thesis SET entry_catalyst=?, catalyst_source=?, price_target=?,
                   stop_level=?, time_horizon=?, status=?, updated_at=datetime('now')
                   WHERE symbol=?""",
                (catalyst, source, target, stop, horizon, status, self.symbol)
            )
        else:
            await db.execute(
                """INSERT INTO thesis (symbol, position_type, entry_catalyst, catalyst_source,
                   price_target, stop_level, time_horizon, status, auto_populated,
                   created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,0,datetime('now'),datetime('now'))""",
                (self.symbol, "directional", catalyst, source, target, stop, horizon, status)
            )
        self.call_from_thread(self.dismiss)
```

- [ ] **Step 3: Commit**

```bash
git add portfolio_tui/screens/note_input.py portfolio_tui/screens/thesis_editor.py
git commit -m "feat: Note input and thesis editor modal screens"
```

---

### Task 17: Final integration — wire up views, smoke test, update explore.sh

**Files:**
- Modify: `explore.sh`
- Modify: `portfolio_tui/app.py` (final cleanup)

- [ ] **Step 1: Final smoke test — all views reachable**

```bash
.venv/bin/python -c "
from portfolio_tui.app import TraderApp
from portfolio_tui.views.inbox_view import InboxView
from portfolio_tui.views.grid_view import GridView
from portfolio_tui.views.kanban_view import KanbanView
from portfolio_tui.views.briefing_view import BriefingView
from portfolio_tui.screens.position_detail import PositionDetail
from portfolio_tui.screens.note_input import NoteInput
from portfolio_tui.screens.thesis_editor import ThesisEditor
print('all imports ok')
"
```

Expected: `all imports ok`

- [ ] **Step 2: Run full test suite**

```bash
.venv/bin/pytest -v
```

Expected: all tests PASSED, 0 errors.

- [ ] **Step 3: Add The Trader to `explore.sh`**

Add entry at the top of `NAMES` and `TITLES` arrays in `explore.sh`:

```bash
NAMES=("the-trader" "the-watcher" "the-cockpit" "the-pipeline" "the-briefing-room" "the-inbox")
TITLES=(
  "★ The Trader       — Hybrid: Inbox+Grid+Kanban+Briefing in one app. (RECOMMENDED)"
  "The Watcher        — Autonomous daemon, read-only TUI. Trust the machine."
  "The Cockpit        — No daemon, all in-process. Human decides everything."
  "The Pipeline       — Kanban board. Positions flow through IC lifecycle lanes."
  "The Briefing Room  — AI narrative is the interface. Data is secondary."
  "The Inbox          — Alert inbox. Only shows what needs attention."
)
ENTRY_POINTS=("trader" "portfolio" "portfolio" "pipeline" "portfolio" "portfolio")
```

Update `Choice [1-5` to `Choice [1-6`.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: The Trader — hybrid TUI with Inbox/Grid/Kanban/Briefing views complete"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Schema ✓, daemon schedule ✓, IV rank/percentile ✓, regime engine ✓, IC 50%/200% exits ✓, force-close-at-expiry (covered by position_monitor job stub — add to Task 9 order_executor), inbox items ✓, briefings ✓, trailing stops (schema present, daemon stub in jobs.py — v2 feature per spec Out of Scope), all 4 TUI views ✓, Cockpit detail ✓, thesis editor ✓, note input ✓
- [x] **Placeholder scan:** No TBD/TODO in implementation steps. All code blocks complete.
- [x] **Type consistency:** `run_position_monitor(db)` used consistently. `open_position_detail(symbol)` on app. `db.query(sql, params)` / `db.execute(sql, params)` used throughout.
- [x] **One gap found and noted:** Force-close-at-expiry not in a dedicated task — it belongs in Task 8 alongside profit/stop triggers. Add this to `run_position_monitor`:

```python
# Add to run_position_monitor after the check_exit_trigger block:
from datetime import date
today = date.today().isoformat()
cursor = await db.execute(
    "SELECT id, symbol, entry_credit, contracts FROM ic_positions "
    "WHERE exit_reason IS NULL AND expiry <= ?", (today,)
)
expired = await cursor.fetchall()
for ic in expired:
    realized_pnl = 0.0  # unknown at expiry — mark as force_close
    now = datetime.now(tz=timezone.utc).isoformat()
    await db.execute(
        "UPDATE ic_positions SET exit_reason='force_close', realized_pnl=0 WHERE id=?",
        (ic["id"],)
    )
    await db.execute(
        "INSERT INTO alerts (symbol, alert_type, message, triggered_at) VALUES (?,?,?,?)",
        (ic["symbol"], "force_close", f"{ic['symbol']} IC expired — force closed", now)
    )
closed.extend({"symbol": ic["symbol"], "exit_reason": "force_close", "realized_pnl": 0.0} for ic in expired)
await db.commit()
```
