# Portfolio Tracker — Design Spec
_Date: 2026-05-19_

---

## Overview

A Python-based portfolio management system for an active options trader running an iron condor (IC) earnings strategy alongside directional stock/options positions. The system consists of a persistent background daemon (`kotorid`) that monitors positions, scans IC candidates, and generates AI-powered briefings, paired with a Textual TUI for display and interaction.

All times are **Central Time (CT)**. Market close = 3:00 PM CT.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  kotorid  (background daemon, runs continuously)      │
│  ┌──────────────┐ ┌────────────┐ ┌──────────────────┐   │
│  │ tradier_     │ │ iv_engine  │ │ regime_engine    │   │
│  │ client.py    │ │ .py        │ │ .py              │   │
│  └──────────────┘ └────────────┘ └──────────────────┘   │
│  ┌──────────────┐ ┌────────────┐ ┌──────────────────┐   │
│  │ scanner.py   │ │ monitor.py │ │ briefing.py      │   │
│  │ + pipeline   │ │            │ │                  │   │
│  └──────────────┘ └────────────┘ └──────────────────┘   │
│  ┌──────────────┐ ┌────────────┐                        │
│  │ order_       │ │ alert_     │                        │
│  │ executor.py  │ │ engine.py  │                        │
│  └──────────────┘ └────────────┘                        │
│                        │                                 │
│                   SQLite (shared state)                  │
└────────────────────────┼─────────────────────────────────┘
                         │  reads
            ┌────────────▼────────────┐
            │   Textual TUI           │
            │   Dashboard Grid        │
            └─────────────────────────┘
```

- Daemon starts once: `python -m kotorid`
- TUI starts separately: `python -m kotori_tui`
- TUI polls SQLite every 3 seconds; Textual's reactive system handles re-renders
- No sockets or REST layer — SQLite is the shared state bus
- TUI writes thesis/notes directly to SQLite (no daemon involvement)

---

## Data Sources

| Source | Used For | Cost |
|---|---|---|
| Tradier (live/sandbox) | Real-time quotes (SSE), positions, orders, live options chains | Free with brokerage account |
| Polygon (free tier) | Historical IV snapshots (nightly batch, 52-week history) | Free, 5 req/min |

Switching environments: `TRADIER_ENV=sandbox` → all orders go to `sandbox.tradier.com` with paper money. TUI displays a persistent `[SANDBOX]` banner. Switching to live requires changing this env var explicitly.

---

## Daemon Schedule (all times CT)

```
05:30 AM  IV Ingest (morning)
          Polygon batch: fetch prior day ATM IV for each watched symbol
          Compute IV rank + IV percentile → write iv_history
          Update regime_snapshots (IV regime per symbol)

08:00 AM  Pre-market Gap Monitor
          Fetch pre-market price for each symbol with open ICs
          If underlying within 1 expected move of short strike → fire alert
          Gives trader time to react before 8:30 AM open

02:15 PM  IV Ingest (pre-close)
          Re-fetch ATM IV via Tradier live options chain (same-day data)
          Update iv_history with intraday IV
          Recompute IV rank + IV percentile
          Must run before scan so IV percentile thresholds use same-day data

02:30 PM  IC Scan
          Fetch next-session AH earnings candidates from earnings calendar
          For each candidate: scan_candidate() → 4-agent pipeline
          (Strategist → Risk Manager → Devil's Advocate → Portfolio Manager)
          Uses IV data from 02:15 PM ingest, not the 5:30 AM snapshot
          Write results to candidates table
          Fire alert for pipeline decision = "trade"

02:50 PM  Order Executor
          Review candidates where pipeline decision = "trade" and
          order_status = "pending_approval"
          TUI shows confirmation prompt; user approves/rejects
          On approval: place 4-legged IC spread order via Tradier
          Update candidates.order_status

Continuous Position Monitor
          SSE stream from Tradier: live quotes on IC legs
          Per open IC: compute current exit_debit from mid prices
          50% profit hit  → place closing order + alert
          200% stop hit   → place closing order + alert
          Expiry date     → force-close all legs at market open

07:00 AM  Briefings
          Daily (every day): prior session trades + alerts + agent
          pipeline outputs → Claude synthesis → write to briefings
          Weekly (Monday): week P&L, win rate, IV crush accuracy
          Monthly (1st): full portfolio review, strategy assessment
```

---

## Data Model

### `positions`
Synced from Tradier. Covers both stock and options positions.
```
symbol            TEXT
quantity          REAL
avg_cost          REAL
current_price     REAL
market_value      REAL
unrealized_pnl    REAL
unrealized_pnl_pct REAL
instrument_type   TEXT   -- "stock" | "option"
-- options fields (null for stocks):
underlying        TEXT
expiry            DATE
strike            REAL
put_call          TEXT   -- "call" | "put"
last_updated      TIMESTAMP
```

### `ic_positions`
One row per open iron condor (4 legs). Separate from `positions` for clarity.
```
id                INTEGER PRIMARY KEY
symbol            TEXT
entry_date        DATE
expiry            DATE
short_call        REAL
long_call         REAL
short_put         REAL
long_put          REAL
spread_width      REAL
entry_credit      REAL   -- per share
contracts         INTEGER
max_loss          REAL   -- total dollars at risk
current_debit     REAL   -- updated by monitor loop
pct_max_profit    REAL   -- (entry_credit - current_debit) / entry_credit
regime_at_entry   TEXT   -- normal | caution
iv_percentile_at_entry REAL
expected_move     REAL
exit_debit        REAL
exit_reason       TEXT   -- profit_target | stop_loss | force_close | manual
realized_pnl      REAL
agent_run_id      INTEGER REFERENCES agent_runs(id)
```

### `iv_history`
One row per symbol per date. Reused across all options on that underlying.
```
symbol            TEXT
date              DATE
iv                REAL
iv_rank           REAL
iv_percentile     REAL
UNIQUE(symbol, date)
```

### `iv_crush_history`
Historical IV crush per symbol per earnings event. Used by scanner to qualify candidates.
```
id                INTEGER PRIMARY KEY
symbol            TEXT
earnings_date     DATE
iv_before         REAL
iv_after          REAL
crush_pct         REAL   -- (iv_before - iv_after) / iv_before
```

### `regime_snapshots`
```
symbol            TEXT
timestamp         TIMESTAMP
market_regime     TEXT   -- normal | caution | no_trade  (VIX-based: <35 / 35-45 / >45)
earnings_regime   TEXT   -- pre_earnings | post_earnings | none
iv_regime         TEXT   -- high | normal | low  (IVR-based: >50 / 25-50 / <25)
vix               REAL
adx               REAL
```

### `thesis`
One row per symbol. IC trades auto-populated from pipeline; directional trades manual.
```
symbol            TEXT PRIMARY KEY
position_type     TEXT   -- "ic" | "directional"
entry_catalyst    TEXT   -- e.g. "earnings IV crush" | "insider cluster buy"
catalyst_source   TEXT   -- e.g. "4-agent pipeline" | "Unusual Whales / SEC Form 4"
price_target      REAL
stop_level        REAL
time_horizon      TEXT
status            TEXT   -- intact | weakening | invalidated
auto_populated    BOOLEAN  -- true for IC trades
created_at        TIMESTAMP
updated_at        TIMESTAMP
```

### `notes`
Append-only log per symbol. Preserves full history of thinking as a trade evolves.
```
id                INTEGER PRIMARY KEY
symbol            TEXT
body              TEXT
created_at        TIMESTAMP
```

### `agent_runs`
One row per pipeline run (IC scan candidate). Stores full agent reasoning for display in TUI and Claude briefings.
```
id                INTEGER PRIMARY KEY
symbol            TEXT
earnings_date     DATE
scanner_output    JSON
strategist_output JSON
risk_manager_output JSON
devils_advocate_output JSON
portfolio_manager_output JSON
final_decision    TEXT   -- trade | pass | pipeline_error
created_at        TIMESTAMP
```

### `candidates`
IC scan results with order lifecycle status.
```
id                INTEGER PRIMARY KEY
agent_run_id      INTEGER REFERENCES agent_runs(id)
symbol            TEXT
scan_date         DATE
order_status      TEXT   -- pending_approval | approved | rejected | placed | filled | skipped
short_call        REAL
long_call         REAL
short_put         REAL
long_put          REAL
expected_credit   REAL
contracts         INTEGER
max_loss          REAL
```

### `alerts`
```
id                INTEGER PRIMARY KEY
symbol            TEXT
alert_type        TEXT   -- iv_rank_low | profit_target | stop_loss | gap_risk |
                         --   regime_change | pre_earnings | force_close | ic_candidate
message           TEXT
triggered_at      TIMESTAMP
acknowledged      BOOLEAN
```

### `trailing_stops`
For directional (non-IC) positions only.
```
symbol            TEXT PRIMARY KEY
trail_type        TEXT   -- percent | dollar
trail_value       REAL
high_water_mark   REAL
stop_price        REAL
active            BOOLEAN
created_at        TIMESTAMP
```

### `briefings`
```
id                INTEGER PRIMARY KEY
period            TEXT   -- daily | weekly | monthly
content           TEXT   -- Claude-generated markdown
generated_at      TIMESTAMP
```

---

## TUI — Dashboard Grid Layout

```
┌─ kotorid ● running ──────────────── 14:22:11 CT ─── [SANDBOX] ─┐
│  NAV $84,230  │  Today +$1,027  │  VIX 18.4  │  Regime normal  │  Alerts 2  │
├───────────────────────────────────────────────────────────────────┤
│ POSITIONS (40%)        │ REGIME (30%)         │ ALERTS (30%)      │
│                        │                      │                   │
│ Symbol   P&L%  Thesis  │ Market   normal      │ ⚡ TSLA IVR→28    │
│ NVDA    +3.2%  Intact  │ VIX      18.4        │ ⚡ SPY thesis X   │
│ TSLA IC +68%   Watch   │ TSLA     PreEarnings │                   │
│ META    -1.8%  Intact  │ NVDA IVR 34          │                   │
│ SPY P   -12%   Invalid │ SPY IVP  52%         │                   │
│ AMZN    +1.1%  Intact  │                      │                   │
├───────────────────────────────────────────────────────────────────┤
│  ↑↓ navigate  ↵ detail  n note  t thesis  s scanner  b briefing  a alerts  q quit  │
└───────────────────────────────────────────────────────────────────┘
```

**Position detail screen (↵ on IC position):**
- IC legs: short call / long call / short put / long put + current mid prices
- Entry credit, current debit, % of max profit captured
- Cushion: distance from underlying to each short strike
- Greeks: Δ, Γ, Θ, Ν for the full position
- IV regime at entry vs current
- Agent pipeline reasoning (strategist / risk manager / devils advocate / PM summaries)
- Thesis (auto-populated from pipeline for ICs)
- Notes log (chronological, append-only)

**Position detail screen (↵ on directional position):**
- Price, avg cost, unrealized P&L
- IV rank, IV percentile, trend regime
- Trailing stop status (if configured)
- Manual thesis (catalyst, target, stop, status)
- Notes log

**IC Scanner screen (`s`):**
- Today's candidates with pipeline decision
- Per candidate: symbol, IV percentile, expected move, proposed strikes, expected credit, contracts, max loss
- Pending approvals highlighted — press `↵` to approve/reject before 2:50 PM CT order execution window

---

## Position Types

| Attribute | IC / Earnings | Directional / Stock |
|---|---|---|
| Entry | Daemon scanner + order executor | Manually placed via Tradier |
| Thesis | Auto-populated from 4-agent pipeline | Manual entry in TUI |
| Exit trigger | 50% profit / 200% stop / expiry | Trailing stop or manual |
| Pre-market monitor | Yes (gap risk vs short strikes) | No |
| Detail view | IC legs + Greeks + cushion + pipeline reasoning | Price + P&L + trend + trailing stop |

---

## IC Strategy Rules (from earnings-vol)

Lifted directly and adapted for live trading:

- **Timing filter**: only AH earnings reporters
- **IV percentile threshold**: >70% (normal regime) / >80% (caution regime)
- **VIX regime**: normal <35 / caution 35–45 / no_trade >45
- **Strike selection**: short strikes at ±1 expected move; long strikes 1 spread width out
- **Liquidity filter**: bid-ask ≤ $0.15, open interest ≥ 500
- **Credit threshold**: ≥30% of spread width
- **Position sizing**: 2% max portfolio loss per trade (1% in caution regime)
- **Sector exposure cap**: ≤40% of total open risk per sector
- **Earnings history**: ≥8 prior earnings events, avg IV crush ≥20%
- **Profit target**: exit when debit ≤ 50% of entry credit
- **Stop loss**: exit when debit ≥ 200% of entry credit
- **Force close**: all legs closed at expiry date market open

---

## Project Structure

```
portfolio/
├── kotorid/
│   ├── __main__.py          -- daemon entry point
│   ├── tradier_client.py    -- REST + SSE wrapper
│   ├── polygon_client.py    -- historical IV fetches (lifted from earnings-vol)
│   ├── iv_engine.py         -- IV rank/percentile (lifted from earnings-vol)
│   ├── regime_engine.py     -- VIX + earnings + IV regime classification
│   ├── scanner.py           -- IC candidate scan + 4-agent pipeline
│   ├── order_executor.py    -- places Tradier multi-leg spread orders
│   ├── monitor.py           -- profit/stop/force-close per open IC
│   ├── alert_engine.py      -- rule evaluation → alerts table
│   ├── briefing.py          -- Claude daily/weekly/monthly briefings
│   └── agents/              -- Strategist, RiskManager, DevilsAdvocate, PortfolioManager
├── kotori_tui/
│   ├── __main__.py          -- TUI entry point
│   ├── app.py               -- Textual App, Dashboard Grid layout
│   ├── screens/
│   │   ├── dashboard.py     -- main grid (positions + regime + alerts)
│   │   ├── ic_detail.py     -- IC position detail screen
│   │   ├── position_detail.py -- directional position detail screen
│   │   ├── scanner.py       -- IC candidates + approval flow
│   │   ├── briefings.py     -- daily/weekly/monthly briefings viewer
│   │   └── alerts.py        -- alerts list with acknowledge
│   └── db.py                -- SQLite read helpers
├── db/
│   └── schema.sql           -- single source of truth for all tables
├── docs/
│   └── superpowers/specs/   -- this file
├── pyproject.toml
└── .env.example             -- TRADIER_ENV, TRADIER_API_KEY, POLYGON_API_KEY, ANTHROPIC_API_KEY
```

---

## Key Dependencies

```toml
textual          # TUI framework
httpx            # async HTTP for Tradier + Polygon REST
anthropic        # Claude API for briefings
apscheduler      # daemon job scheduling
aiosqlite        # async SQLite
```

---

## Out of Scope (v1)

- Mobile or web interface
- Backtesting (earnings-vol already handles this separately)
- Multi-account support
- Push notifications (alerts are TUI-only in v1)
- Historical P&L charting (TUI displays current positions only)
