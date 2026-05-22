# 🐦 kotori (小鳥)

A portfolio companion for options traders running iron condors.

## Run

```bash
pip install -e .
cp .env.example .env  # fill in API keys
kotorid               # background sync daemon
kotori                # TUI
```

Requires Python 3.13+. Data lives at `~/.kotori/kotori.db` (override with `KOTORI_DB`).

## What works live

With `TRADIER_API_KEY` set, the daemon runs these against the real broker:

- **position_sync** (every 60s) — Tradier positions → SQLite, multiplier-aware
- **ic_refresh** (every 60s) — live mid-price quotes → `ic_positions.current_debit` + `pct_max_profit`
- **position_monitor** (every 30s) — fires alerts on profit target (debit ≤ 50% of credit) or stop loss (debit ≥ 2× credit)
- **gap_monitor** (08:00 CT) — pre-market gap risk against open IC short strikes
- **generate_briefing** (07:00 CT) — uses Anthropic if `ANTHROPIC_API_KEY` set, static fallback otherwise

The TUI reads from the same SQLite DB — positions list, IC detail with strikes/credit/debit/profit-captured, thesis editor, notes.

## What's stubbed

Set `KOTORI_SEED_MOCK=1` to enable demo behavior:

- One-shot mock data seed on startup (`mock_data.seed_mock_data`)
- **iv_ingest_morning / iv_ingest_preclose** — fake IV via `random.gauss` (needs real Polygon historical IV)
- **ic_scan** — fake candidates from fake IV (needs real 4-agent pipeline)
- **order_executor** — flags candidates as `placed` without calling Tradier (needs real order submission + post-fill ic_positions row creation)

In live mode (`KOTORI_SEED_MOCK` unset), none of these run — daemon is quiet about anything it can't do honestly.

## Dev

```bash
.venv/bin/python -m pytest                                   # 64 tests
.venv/bin/python scripts/snapshot_tui.py                     # SVG snapshot of the briefing view
.venv/bin/python scripts/snapshot_position_detail.py SYMBOL  # SVG of a position detail screen
.venv/bin/python scripts/snapshot_modal.py thesis|note SYM   # SVG of a modal screen
.venv/bin/python scripts/place_and_sync.py                   # buy 1 share stock, sync, dump positions
.venv/bin/python scripts/place_option_and_sync.py            # buy 1 ATM call, sync, dump positions
.venv/bin/python scripts/place_ic_and_sync.py                # place a 4-leg IC, sync, materialize ic_positions
```

Live-broker scripts hit the Tradier sandbox; fictitious money, real HTTP, real broker state.
