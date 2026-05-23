# Kotori Discord Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single Discord webhook channel into a live window into kotori's behavior: 15-min heartbeats during market hours, 11 enriched event alerts, daily morning briefing + EOD recap.

**Architecture:** Three streams sharing one webhook. Events flow through the existing `alerts` table → `notify_alerts` job → Discord embed; heartbeats and narratives post directly via a new direct-post path (state, not events). Alert content is centralized in a new `kotorid/alerts_lib.py` so trigger sites stay terse and format changes happen in one place.

**Tech Stack:** Python 3.11, aiosqlite, httpx, APScheduler (existing). pytest-asyncio for tests (existing pattern).

**Spec:** `docs/superpowers/specs/2026-05-23-kotori-discord-stream-design.md`

**Commit message style:** Bare imperative subjects, no `feat:`/`chore:` prefixes (this repo's convention).

---

## File Structure

**New files:**
- `kotorid/alerts_lib.py` — centralized `create_alert()` helper accepting structured fields, plus alert-formatting helpers
- `kotorid/heartbeat.py` — heartbeat digest builder + direct Discord post path
- `kotorid/order_status.py` — Tradier order-status polling, emits `order_filled` / `order_failed`
- `tests/test_alerts_lib.py`
- `tests/test_heartbeat.py`
- `tests/test_order_status.py`
- `tests/test_dte_check.py`
- `tests/test_eod_recap.py`

**Modified files:**
- `kotorid/schema.sql` — three new columns on `ic_positions`
- `kotorid/db.py` — three new `_ensure_column` calls
- `kotorid/notify.py` — `format_alert_embed` reads structured fields, falls back to legacy `message`
- `kotorid/candidate_scan.py` — emits `candidate_ready` at end of scan
- `kotorid/jobs.py` — richer messages on existing exit alerts; new `dte_check` + `eod_recap` jobs
- `kotorid/position_monitor.py` — emits `position_warning` on 50% breach
- `kotorid/ic_sync.py` — emits `short_strike_threatened` per refresh
- `kotorid/order_placement.py` — stores `order_id` on `ic_positions`; richer `ic_placed` content
- `kotorid/__main__.py` — registers new APScheduler jobs

---

## Task 1: Schema migration — three new ic_positions columns

**Files:**
- Modify: `kotorid/schema.sql` (add columns)
- Modify: `kotorid/db.py` (add `_ensure_column` calls in `init_db`)
- Test: `tests/test_db.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
import pytest
from kotorid.db import get_db, init_db


@pytest.mark.asyncio
async def test_init_db_adds_discord_stream_columns(tmp_path):
    """ic_positions has order_id, position_warning_at, short_strike_warned_at."""
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        cur = await db.execute("PRAGMA table_info(ic_positions)")
        cols = {row[1] for row in await cur.fetchall()}
    assert "order_id" in cols
    assert "position_warning_at" in cols
    assert "short_strike_warned_at" in cols
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_db.py::test_init_db_adds_discord_stream_columns -v
```

Expected: FAIL with `AssertionError: assert 'order_id' in cols`

- [ ] **Step 3: Implement**

In `kotorid/schema.sql`, add the columns directly to the `ic_positions` table definition (for fresh installs). Find the `CREATE TABLE IF NOT EXISTS ic_positions (...)` block; add three lines at the bottom of the column list (before the closing `);`):

```sql
    order_id TEXT,
    position_warning_at TEXT,
    short_strike_warned_at TEXT
```

In `kotorid/db.py`, inside `init_db()`, after the existing `_ensure_column` line for `alerts.notified_at`, add three more lines for existing databases:

```python
    await _ensure_column(db, "ic_positions", "order_id", "order_id TEXT")
    await _ensure_column(db, "ic_positions", "position_warning_at", "position_warning_at TEXT")
    await _ensure_column(db, "ic_positions", "short_strike_warned_at", "short_strike_warned_at TEXT")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_db.py -v
```

Expected: all tests in file PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/schema.sql kotorid/db.py tests/test_db.py
git commit -m "add ic_positions columns for discord stream"
```

---

## Task 2: alerts_lib.py — centralized create_alert helper

**Files:**
- Create: `kotorid/alerts_lib.py`
- Test: `tests/test_alerts_lib.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_alerts_lib.py`:

```python
import json

import pytest

from kotorid.alerts_lib import create_alert, ALERT_FIELDS_KEY
from kotorid.db import get_db, init_db


@pytest.mark.asyncio
async def test_create_alert_inserts_row_with_structured_fields(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        alert_id = await create_alert(
            db,
            alert_type="stop_loss",
            symbol="SPY",
            headline="Stop Loss — SPY 5/29",
            body_lines=[
                "Closed at debit $1.85 (entry credit $1.00).",
                "Loss: −$400 (100% of max).",
            ],
            fields={"entry_credit": 1.00, "exit_debit": 1.85, "realized_pnl": -400.0},
        )
        await db.commit()
        cur = await db.execute(
            "SELECT alert_type, symbol, message FROM alerts WHERE id=?",
            (alert_id,),
        )
        row = await cur.fetchone()

    assert row is not None
    assert row["alert_type"] == "stop_loss"
    assert row["symbol"] == "SPY"
    # The structured payload is embedded in `message` as a JSON tail after a marker.
    assert ALERT_FIELDS_KEY in row["message"]
    legacy, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    assert "Stop Loss — SPY 5/29" in legacy
    parsed = json.loads(json_tail)
    assert parsed["fields"]["entry_credit"] == 1.00
    assert parsed["body_lines"] == [
        "Closed at debit $1.85 (entry credit $1.00).",
        "Loss: −$400 (100% of max).",
    ]


@pytest.mark.asyncio
async def test_create_alert_without_fields_keeps_plain_message(tmp_path):
    """Legacy compat: callers that only pass a plain string still work."""
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await create_alert(
            db, alert_type="custom", symbol="X", headline="plain text only",
        )
        await db.commit()
        cur = await db.execute("SELECT message FROM alerts WHERE alert_type='custom'")
        row = await cur.fetchone()

    assert ALERT_FIELDS_KEY not in row["message"]
    assert "plain text only" in row["message"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_alerts_lib.py -v
```

Expected: FAIL with `ModuleNotFoundError: kotorid.alerts_lib`.

- [ ] **Step 3: Implement**

Create `kotorid/alerts_lib.py`:

```python
"""Centralized alert creation.

Every alert in kotori flows through ``create_alert``. The function writes
to the ``alerts`` table the existing ``notify_alerts`` job already polls,
but augments the legacy single-string ``message`` column with optional
*structured* fields (a JSON tail appended after a sentinel marker).

``notify.format_alert_embed`` parses the marker and renders rich Discord
embeds. Legacy rows without the marker render the bare ``message`` as
before — so this is fully backward compatible.

Format of message column when structured payload is present::

    <headline>\\n<body line 1>\\n<body line 2>...\\n<ALERT_FIELDS_KEY><json>

The sentinel keeps the payload parseable without a schema change.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

ALERT_FIELDS_KEY = "\n---KOTORI_STRUCTURED---\n"


async def create_alert(
    db: aiosqlite.Connection,
    *,
    alert_type: str,
    symbol: str,
    headline: str,
    body_lines: list[str] | None = None,
    fields: dict[str, Any] | None = None,
    triggered_at: str | None = None,
) -> int:
    """Insert one row into the alerts table; return its id.

    Caller is responsible for ``db.commit()``. We don't commit here so
    multi-alert flows (e.g., a single job that emits several alerts) can
    batch.
    """
    body_lines = body_lines or []
    now = triggered_at or datetime.now(tz=timezone.utc).isoformat()

    if fields or body_lines:
        plain = headline if not body_lines else headline + "\n" + "\n".join(body_lines)
        payload = {"body_lines": body_lines, "fields": fields or {}}
        message = plain + ALERT_FIELDS_KEY + json.dumps(payload, default=str)
    else:
        message = headline

    cursor = await db.execute(
        "INSERT INTO alerts (symbol, alert_type, message, triggered_at) "
        "VALUES (?,?,?,?)",
        (symbol, alert_type, message, now),
    )
    return cursor.lastrowid


def parse_alert_message(message: str) -> tuple[str, dict[str, Any]]:
    """Split a stored message into (plain_text, structured_payload).

    Returns ``(plain, {})`` for legacy rows without the marker.
    """
    if ALERT_FIELDS_KEY not in message:
        return message, {}
    plain, _, json_tail = message.partition(ALERT_FIELDS_KEY)
    try:
        payload = json.loads(json_tail)
    except json.JSONDecodeError:
        return message, {}
    return plain, payload
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_alerts_lib.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/alerts_lib.py tests/test_alerts_lib.py
git commit -m "add alerts_lib centralized alert helper"
```

---

## Task 3: notify.py — render structured alert fields as rich embeds

**Files:**
- Modify: `kotorid/notify.py:39-59` (format_alert_embed)
- Test: `tests/test_notify.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_notify.py`:

```python
from kotorid.alerts_lib import ALERT_FIELDS_KEY
from kotorid.notify import format_alert_embed


def test_format_alert_embed_renders_structured_message():
    """When message contains the structured marker, headline + body_lines render in embed."""
    structured = {
        "body_lines": [
            "Closed at debit $1.85 (entry credit $1.00).",
            "Loss: −$400 (100% of max).",
            "Driver: short put 730 went $0.45 → $1.20 (+167%).",
        ],
        "fields": {"realized_pnl": -400.0},
    }
    import json
    message = "Stop Loss — SPY 5/29" + ALERT_FIELDS_KEY + json.dumps(structured)
    alert = {
        "alert_type": "stop_loss",
        "symbol": "SPY",
        "message": message,
        "triggered_at": "2026-05-23T20:00:00+00:00",
    }
    payload = format_alert_embed(alert)
    embed = payload["embeds"][0]

    # Title still uses the alert_type style + symbol
    assert "Stop Loss" in embed["title"]
    assert "SPY" in embed["title"]
    # Description should contain each body line
    for line in structured["body_lines"]:
        assert line in embed["description"]


def test_format_alert_embed_legacy_message_unchanged():
    """Plain string messages render exactly as before."""
    alert = {
        "alert_type": "stop_loss",
        "symbol": "SPY",
        "message": "SPY IC: stop loss — P&L $-400",
        "triggered_at": "2026-05-23T20:00:00+00:00",
    }
    payload = format_alert_embed(alert)
    assert payload["embeds"][0]["description"] == "SPY IC: stop loss — P&L $-400"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_notify.py::test_format_alert_embed_renders_structured_message -v
```

Expected: FAIL — current `format_alert_embed` puts the raw message (including marker + JSON tail) into description.

- [ ] **Step 3: Implement**

In `kotorid/notify.py`, update `format_alert_embed` (currently lines 39–59):

```python
from kotorid.alerts_lib import parse_alert_message


def format_alert_embed(alert: dict) -> dict:
    """Build the Discord webhook payload for an alert row.

    Returns the full payload dict (with an ``embeds`` array of one), not
    just the embed — so the caller can httpx.post(..., json=payload)
    directly without further wrapping.

    Structured alerts (created via ``alerts_lib.create_alert`` with
    ``body_lines`` / ``fields``) render the headline as the embed title
    and the body lines joined by newlines as the description. Legacy
    rows (plain string in ``message``) keep the previous behavior.
    """
    alert_type = alert.get("alert_type") or "unknown"
    title, color = _ALERT_STYLE.get(alert_type, (f"⚪ {alert_type}", _COLOR_GRAY))
    symbol = alert.get("symbol") or "—"
    raw_message = alert.get("message") or ""
    triggered_at = alert.get("triggered_at") or datetime.now(tz=timezone.utc).isoformat()

    plain, payload = parse_alert_message(raw_message)
    if payload:
        # Structured: first line of `plain` is the headline; body_lines follow.
        body_lines = payload.get("body_lines") or []
        description = "\n".join(body_lines) if body_lines else plain
    else:
        description = plain

    return {
        "embeds": [{
            "title": f"{title} — {symbol}",
            "description": description,
            "color": color,
            "timestamp": triggered_at,
            "footer": {"text": "kotori"},
        }],
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_notify.py -v
```

Expected: all tests in file PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add kotorid/notify.py tests/test_notify.py
git commit -m "render structured alerts as multi-line discord embeds"
```

---

## Task 4: Enrich stop_loss / profit_target content via alerts_lib

**Files:**
- Modify: `kotorid/jobs.py:31-37` (the exit-trigger alert insert)
- Test: `tests/test_jobs_position_monitor.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_jobs_position_monitor.py`:

```python
import json
from datetime import date, timedelta

import pytest

from kotorid.alerts_lib import ALERT_FIELDS_KEY
from kotorid.db import get_db, init_db
from kotorid.jobs import run_position_monitor


@pytest.mark.asyncio
async def test_stop_loss_alert_has_structured_content(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        # IC entered at $1.00 credit, current debit $2.10 = stop_loss territory
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,2.10)"""
        )
        await db.commit()

        await run_position_monitor(db)

        cur = await db.execute(
            "SELECT message FROM alerts WHERE alert_type='stop_loss'"
        )
        row = await cur.fetchone()

    assert row is not None
    assert ALERT_FIELDS_KEY in row["message"]
    plain, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    payload = json.loads(json_tail)
    fields = payload["fields"]
    assert fields["entry_credit"] == 1.00
    assert fields["exit_debit"] == 2.10
    assert fields["realized_pnl"] == pytest.approx(-110.0)  # (1.00-2.10)*100*1
    # Body lines must explain *why*
    body = "\n".join(payload["body_lines"])
    assert "$1.00" in body  # entry credit
    assert "$2.10" in body  # exit debit
    assert "-$110" in body or "−$110" in body  # realized loss
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_jobs_position_monitor.py::test_stop_loss_alert_has_structured_content -v
```

Expected: FAIL — current code writes a plain string, no `ALERT_FIELDS_KEY`.

- [ ] **Step 3: Implement**

In `kotorid/jobs.py`, replace the existing `INSERT INTO alerts` block in `run_position_monitor` (the one inside the exit-trigger loop, currently lines 31–37) with a call to `create_alert`:

```python
        # Replace:
        # await db.execute(
        #     """INSERT INTO alerts (symbol, alert_type, message, triggered_at)
        #        VALUES (?,?,?,?)""",
        #     (ic["symbol"], reason, f"{ic['symbol']} IC: {reason.replace('_',' ')} — P&L ${realized_pnl:+.0f}", now)
        # )
        from kotorid.alerts_lib import create_alert
        reason_label = "Stop Loss" if reason == "stop_loss" else "Profit Target"
        await create_alert(
            db,
            alert_type=reason,
            symbol=ic["symbol"],
            headline=f"{reason_label} — {ic['symbol']}",
            body_lines=[
                f"Closed at debit ${ic['current_debit']:.2f} (entry credit ${ic['entry_credit']:.2f}).",
                f"Realized P/L: ${realized_pnl:+.0f}.",
                f"{'Loss' if realized_pnl < 0 else 'Gain'} captured at "
                f"{abs(realized_pnl/100/ic['entry_credit']):.0%} of entry credit.",
            ],
            fields={
                "entry_credit": float(ic["entry_credit"]),
                "exit_debit": float(ic["current_debit"]),
                "realized_pnl": realized_pnl,
                "contracts": ic["contracts"],
            },
            triggered_at=now,
        )
```

Hoist the `from kotorid.alerts_lib import create_alert` to the top of `kotorid/jobs.py` once (and remove the inline import) after the test passes locally.

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_jobs_position_monitor.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/jobs.py tests/test_jobs_position_monitor.py
git commit -m "enrich stop_loss and profit_target alert content"
```

---

## Task 5: Enrich force_close + ic_placed + gap_risk content

**Files:**
- Modify: `kotorid/jobs.py:90-94` (force_close INSERT)
- Modify: `kotorid/order_placement.py:160-168` (ic_placed INSERT)
- Modify: `kotorid/jobs.py` — find existing gap_monitor INSERT (search for `alert_type='gap_risk'` or `"gap_risk"`)
- Test: extend existing test files for each

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_jobs_position_monitor.py`:

```python
@pytest.mark.asyncio
async def test_force_close_alert_is_structured(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-19',?,760,765,735,730,5,1.00,1,400,0.30)""",
            (yesterday,),
        )
        await db.commit()

        await run_position_monitor(db)

        cur = await db.execute(
            "SELECT message FROM alerts WHERE alert_type='force_close'"
        )
        row = await cur.fetchone()

    assert row is not None
    assert ALERT_FIELDS_KEY in row["message"]
    _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    payload = json.loads(json_tail)
    assert payload["fields"]["realized_pnl"] == pytest.approx(70.0)  # (1.00-0.30)*100
```

Append to `tests/test_order_placement.py`:

```python
import json as _json
from kotorid.alerts_lib import ALERT_FIELDS_KEY

# After whatever existing place_approved_candidates test, add:

@pytest.mark.asyncio
async def test_ic_placed_alert_is_structured(tmp_path, monkeypatch):
    """Replace this with whatever stub the existing place_approved tests use."""
    # The pattern depends on existing test infra. The key assertion:
    # after place_approved_candidates, an alert exists where alert_type='ic_placed'
    # and ALERT_FIELDS_KEY is in its message column, and parsed fields includes
    # `order_id`, `expiry`, `expected_credit`, `max_loss`.
    # Pull from existing tests' mocking patterns (Tradier client fake, candidate fixture).
    ...  # See existing test_order_placement.py for the mocking shape
```

(If the existing `test_order_placement.py` uses a specific fixture for httpx-mock or stub responses, mirror that pattern instead of writing from scratch.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_jobs_position_monitor.py::test_force_close_alert_is_structured tests/test_order_placement.py -v -k structured
```

Expected: FAIL on the structured assertions.

- [ ] **Step 3: Implement**

In `kotorid/jobs.py`, replace the force_close INSERT (currently lines 90–94) with:

```python
        from kotorid.alerts_lib import create_alert  # remove if already imported at top
        if realized_pnl is not None:
            body_lines = [
                f"IC expired {ic['symbol']} {ic.get('expiry', '?')}.",
                f"Final debit ${debit:.2f} (entry credit ${entry_credit:.2f}).",
                f"Realized P/L: ${realized_pnl:+.0f}.",
            ]
        else:
            body_lines = [
                f"IC expired {ic['symbol']} {ic.get('expiry', '?')}.",
                "Final debit unknown — last refresh missing; review manually.",
            ]
        await create_alert(
            db,
            alert_type="force_close",
            symbol=ic["symbol"],
            headline=f"IC Closed (Expiry) — {ic['symbol']}",
            body_lines=body_lines,
            fields={
                "entry_credit": entry_credit,
                "exit_debit": debit,
                "realized_pnl": realized_pnl,
            },
            triggered_at=now_ts,
        )
```

In `kotorid/order_placement.py`, replace the `ic_placed` INSERT (lines 160–168) with:

```python
        from kotorid.alerts_lib import create_alert  # hoist to top after first task
        await create_alert(
            db,
            alert_type="ic_placed",
            symbol=cand["symbol"],
            headline=f"Order Placed — {cand['symbol']}",
            body_lines=[
                f"4-leg IC submitted to Tradier for {expiry}.",
                f"Strikes: SC{int(float(cand['short_call']))}/LC{int(float(cand['long_call']))} "
                f"SP{int(float(cand['short_put']))}/LP{int(float(cand['long_put']))}.",
                f"Estimated credit ${float(cand['expected_credit']):.2f}, "
                f"max loss ${float(cand['max_loss']):.0f}.",
                f"Tradier order id: {order_resp.get('order', {}).get('id', '?')}.",
            ],
            fields={
                "order_id": str(order_resp.get("order", {}).get("id", "")),
                "expiry": expiry,
                "expected_credit": float(cand["expected_credit"]),
                "max_loss": float(cand["max_loss"]),
                "contracts": cand["contracts"] or 1,
            },
            triggered_at=now_iso,
        )
```

For gap_risk: locate the existing INSERT in `kotorid/jobs.py` (search `gap_risk`). Replace with a `create_alert` call passing the existing message text as `headline` plus whatever computed fields it has (e.g., gap %, underlying close). Test pattern mirrors the others.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/ -v
```

Expected: all tests PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add kotorid/jobs.py kotorid/order_placement.py tests/test_jobs_position_monitor.py tests/test_order_placement.py
git commit -m "enrich force_close, ic_placed, gap_risk alert content"
```

---

## Task 6: New alert — candidate_ready

**Files:**
- Modify: `kotorid/candidate_scan.py` (emit at end of `scan_candidates`)
- Test: `tests/test_candidate_scan.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_candidate_scan.py`:

```python
import json as _json
import pytest

from kotorid.alerts_lib import ALERT_FIELDS_KEY


@pytest.mark.asyncio
async def test_scan_candidates_emits_candidate_ready_alert(tmp_path, monkeypatch):
    """When ≥1 candidate is written, a candidate_ready alert is created."""
    # The existing test file mocks the httpx client. Re-use that pattern:
    # build a fake chain that yields exactly one candidate (e.g., for SPY).
    # Look in test_candidate_scan.py for the existing httpx stub helper
    # and reuse it, calling scan_candidates with symbols=["SPY"].

    # After the scan runs and inserts at least one candidate row,
    # assert the alerts table contains a row with alert_type='candidate_ready':
    from kotorid.db import get_db, init_db
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        # ... (mock client setup mirroring the existing test file)
        # written = await scan_candidates(db, mock_client, symbols=["SPY"])
        # assert len(written) >= 1
        cur = await db.execute(
            "SELECT message FROM alerts WHERE alert_type='candidate_ready'"
        )
        row = await cur.fetchone()
    assert row is not None
    assert ALERT_FIELDS_KEY in row["message"]
    _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    payload = _json.loads(json_tail)
    assert payload["fields"]["count"] >= 1
    assert "credit" in payload["fields"]
```

(Read the existing `tests/test_candidate_scan.py` first to see how `scan_candidates` is invoked there and mirror the mock pattern. Don't reinvent the stub.)

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_candidate_scan.py::test_scan_candidates_emits_candidate_ready_alert -v
```

Expected: FAIL — `candidate_ready` row missing.

- [ ] **Step 3: Implement**

In `kotorid/candidate_scan.py`, in `scan_candidates()` (lines 254–275), after the `for sym in syms` loop and before `log.info(...)`, add:

```python
    if written:
        from kotorid.alerts_lib import create_alert
        top = max(written, key=lambda c: c["credit"] / (c["max_loss"] / 100))
        body_lines = [
            f"{len(written)} candidate(s) ready for approval.",
            f"Top pick: {top['symbol']} {top['expiry']} — "
            f"credit ${top['credit']:.2f}, max loss ${top['max_loss']:.0f}.",
            f"Strikes: SC{int(top['short_call'])}/LC{int(top['long_call'])} "
            f"SP{int(top['short_put'])}/LP{int(top['long_put'])}.",
            "Approve in TUI or wait — auto-place fallback at 14:50 CT.",
        ]
        await create_alert(
            db,
            alert_type="candidate_ready",
            symbol=top["symbol"],
            headline="Candidates Ready",
            body_lines=body_lines,
            fields={
                "count": len(written),
                "symbols": [c["symbol"] for c in written],
                "credit": top["credit"],
                "max_loss": top["max_loss"],
            },
        )
        await db.commit()
```

In `kotorid/notify.py`, add `"candidate_ready"` to `_ALERT_STYLE`:

```python
    "candidate_ready": ("⚠️ Candidates Ready", _COLOR_ORANGE),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_candidate_scan.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/candidate_scan.py kotorid/notify.py tests/test_candidate_scan.py
git commit -m "add candidate_ready discord alert"
```

---

## Task 7: New alert — dte_warning + new dte_check job

**Files:**
- Modify: `kotorid/jobs.py` (add `dte_check` async function)
- Modify: `kotorid/notify.py` (style entry)
- Create: `tests/test_dte_check.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dte_check.py`:

```python
import json
from datetime import date, timedelta

import pytest

from kotorid.alerts_lib import ALERT_FIELDS_KEY
from kotorid.db import get_db, init_db
from kotorid.jobs import dte_check


@pytest.mark.asyncio
async def test_dte_check_fires_for_ic_expiring_tomorrow(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-22',?,760,765,735,730,5,1.00,1,400,0.42)""",
            (tomorrow,),
        )
        await db.commit()

        await dte_check(db)

        cur = await db.execute("SELECT message FROM alerts WHERE alert_type='dte_warning'")
        rows = await cur.fetchall()

    assert len(rows) == 1
    _, _, json_tail = rows[0]["message"].partition(ALERT_FIELDS_KEY)
    fields = json.loads(json_tail)["fields"]
    assert fields["dte"] == 1
    assert fields["current_debit"] == 0.42


@pytest.mark.asyncio
async def test_dte_check_dedup_same_day(tmp_path):
    """Running twice in one day produces only one alert per IC."""
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-22',?,760,765,735,730,5,1.00,1,400,0.42)""",
            (tomorrow,),
        )
        await db.commit()

        await dte_check(db)
        await dte_check(db)

        cur = await db.execute(
            "SELECT COUNT(*) FROM alerts WHERE alert_type='dte_warning'"
        )
        (count,) = await cur.fetchone()
    assert count == 1


@pytest.mark.asyncio
async def test_dte_check_no_alert_when_not_tomorrow(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        far = (date.today() + timedelta(days=5)).isoformat()
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-22',?,760,765,735,730,5,1.00,1,400,0.42)""",
            (far,),
        )
        await db.commit()
        await dte_check(db)
        cur = await db.execute(
            "SELECT COUNT(*) FROM alerts WHERE alert_type='dte_warning'"
        )
        (count,) = await cur.fetchone()
    assert count == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_dte_check.py -v
```

Expected: FAIL — `ImportError: cannot import name 'dte_check'`.

- [ ] **Step 3: Implement**

In `kotorid/jobs.py`, add (near the top-level functions):

```python
async def dte_check(db: aiosqlite.Connection) -> int:
    """Emit dte_warning for any open IC expiring tomorrow.

    De-duped per (symbol, today) — running multiple times the same day
    creates at most one alert per IC.
    """
    from datetime import date, timedelta
    from kotorid.alerts_lib import create_alert
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    cur = await db.execute(
        "SELECT symbol, expiry, entry_credit, current_debit, contracts "
        "FROM ic_positions WHERE exit_reason IS NULL AND expiry=?",
        (tomorrow,),
    )
    rows = await cur.fetchall()
    fired = 0
    for ic in rows:
        # Dedup: skip if a dte_warning already exists today for this symbol.
        dup_cur = await db.execute(
            "SELECT 1 FROM alerts "
            "WHERE alert_type='dte_warning' AND symbol=? AND date(triggered_at)=?",
            (ic["symbol"], today),
        )
        if await dup_cur.fetchone():
            continue
        debit = ic["current_debit"] or 0.0
        entry = ic["entry_credit"] or 0.0
        unrealized = (entry - debit) * 100 * (ic["contracts"] or 1)
        await create_alert(
            db,
            alert_type="dte_warning",
            symbol=ic["symbol"],
            headline=f"1 Day to Expiry — {ic['symbol']}",
            body_lines=[
                f"Expires {ic['expiry']} (tomorrow).",
                f"Current debit ${debit:.2f}, P/L ${unrealized:+.0f}.",
                "Auto force_close fires day after expiry; close manually if you want a better fill.",
            ],
            fields={
                "dte": 1,
                "expiry": ic["expiry"],
                "current_debit": debit,
                "unrealized_pnl": unrealized,
            },
        )
        fired += 1
    if fired:
        await db.commit()
    return fired


async def dte_check_job():
    """Scheduler wrapper that opens its own DB connection."""
    from kotorid.config import DB_PATH
    from kotorid.db import get_db as _get_db
    async with _get_db(DB_PATH) as db:
        await dte_check(db)
```

In `kotorid/notify.py`, add to `_ALERT_STYLE`:

```python
    "dte_warning": ("⚠️ 1 Day to Expiry", _COLOR_ORANGE),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_dte_check.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/jobs.py kotorid/notify.py tests/test_dte_check.py
git commit -m "add dte_warning alert and dte_check job"
```

---

## Task 8: New alert — position_warning (50% of max_loss breach)

**Files:**
- Modify: `kotorid/jobs.py` (`run_position_monitor`, inside the IC loop)
- Modify: `kotorid/notify.py` (style)
- Test: `tests/test_jobs_position_monitor.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_jobs_position_monitor.py`:

```python
@pytest.mark.asyncio
async def test_position_warning_fires_once_at_50pct_max_loss(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        # max_loss=400, entry_credit=1.00. 50% breach = unrealized loss >= 200.
        # That corresponds to current_debit = 1.00 + 2.00 = 3.00 per share.
        # But stop_loss fires at exit_debit >= entry_credit*2.00 = 2.00.
        # So position_warning fires at 50% of MAX_LOSS, not 50% of entry.
        # max_loss in dollars = $400; 50% = $200 loss.
        # Per share equivalent: unrealized_loss_per_share = (entry_credit - debit)
        # If contracts=1, multiplier=100: unrealized_loss = (entry_credit - debit) * 100.
        # Loss of $200 → debit = entry_credit + 2.00 = 3.00? No: (1.00 - 3.00)*100 = -200. Yes.
        # But stop_loss already fires at debit >= 2.00 (entry_credit*2).
        # Pick a value between profit and stop:
        # Actually 50% of max_loss = $200; stop = full max_loss = $400.
        # debit that produces $200 unrealized loss = 1.00 + 2.00 = 3.00.
        # But stop_loss fires at debit >= 2.00. So our 50%-warn threshold needs
        # to fire *before* stop_loss does. Reframe: warn when
        # current_unrealized_loss / max_loss >= 0.50.
        # At debit = 1.50, unrealized_loss = (1.00 - 1.50) * 100 = -50, that's
        # 50/400 = 12.5% of max_loss. Not enough.
        # At debit = 3.00, that's 200/400 = 50% — but stop_loss already at 2.00.
        # The interval where ONLY position_warning fires (no stop):
        #   need (entry - debit)*100*contracts >= 0.50*max_loss
        #   and  debit < entry_credit * 2.00 (so stop hasn't fired yet)
        # With max_loss=400 and entry=1.00, those conflict.
        # Resolution: position_warning threshold should be tied to
        # max_loss FRACTION, but max_loss is itself defined such that
        # stop_loss (debit = 2*entry) corresponds to ~100% of max_loss only
        # when entry_credit roughly equals spread_width * (some constant).
        # For SPY 5-wide IC at $1 credit: max_loss = 400, stop_loss debit = 2.
        # At debit 2: loss = (1-2)*100 = -100, which is 25% of max_loss.
        # So stop_loss in current code fires at 25% of dollar max_loss.
        # Therefore position_warning at 50% of dollar max_loss would never
        # fire before stop_loss does.
        #
        # The right interpretation: 50% of *stop_loss threshold*, i.e.,
        # debit >= entry_credit * 1.50. That gives ~13% of dollar max_loss,
        # but the *operationally* meaningful "halfway to stop" trigger.
        # Set position_warning when:
        #     debit >= entry_credit * 1.50  AND  debit < entry_credit * 2.00
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,1.60)"""
        )
        await db.commit()

        # First call fires the warning
        await run_position_monitor(db)
        cur = await db.execute("SELECT COUNT(*) FROM alerts WHERE alert_type='position_warning'")
        (count1,) = await cur.fetchone()

        # Second call must NOT fire again (de-dup via position_warning_at column)
        await run_position_monitor(db)
        cur = await db.execute("SELECT COUNT(*) FROM alerts WHERE alert_type='position_warning'")
        (count2,) = await cur.fetchone()

    assert count1 == 1
    assert count2 == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_jobs_position_monitor.py::test_position_warning_fires_once_at_50pct_max_loss -v
```

Expected: FAIL — `count1 == 0`.

- [ ] **Step 3: Implement**

In `kotorid/jobs.py`, inside `run_position_monitor`, **before** the existing exit-trigger check (the `check_exit_trigger` call), add a position_warning branch:

```python
    # ... existing fetch of open_ics ...

    for ic in open_ics:
        # position_warning: halfway-to-stop heads-up. Fires once per IC ever.
        entry = float(ic["entry_credit"] or 0)
        debit = float(ic["current_debit"] or 0)
        warning_already_fired = await db.execute(
            "SELECT position_warning_at FROM ic_positions WHERE id=?", (ic["id"],),
        )
        wrow = await warning_already_fired.fetchone()
        if (
            entry > 0
            and debit >= entry * 1.50
            and debit < entry * 2.00
            and (wrow is None or wrow[0] is None)
        ):
            from kotorid.alerts_lib import create_alert
            unrealized = (entry - debit) * 100 * (ic["contracts"] or 1)
            await create_alert(
                db,
                alert_type="position_warning",
                symbol=ic["symbol"],
                headline=f"Position Warning — {ic['symbol']}",
                body_lines=[
                    f"Debit ${debit:.2f} (entry credit ${entry:.2f}) — halfway to stop.",
                    f"Unrealized P/L: ${unrealized:+.0f}.",
                    "Stop fires at debit "
                    f"${entry*2.00:.2f}. Consider whether to close manually.",
                ],
                fields={
                    "entry_credit": entry,
                    "current_debit": debit,
                    "unrealized_pnl": unrealized,
                },
            )
            now = datetime.now(tz=timezone.utc).isoformat()
            await db.execute(
                "UPDATE ic_positions SET position_warning_at=? WHERE id=?",
                (now, ic["id"]),
            )
            await db.commit()

        # ... existing check_exit_trigger logic continues below ...
```

In `kotorid/notify.py`, add:

```python
    "position_warning": ("⚠️ Position Warning", _COLOR_ORANGE),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_jobs_position_monitor.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/jobs.py kotorid/notify.py tests/test_jobs_position_monitor.py
git commit -m "add position_warning alert at 50pct-to-stop"
```

---

## Task 9: New alert — short_strike_threatened

**Files:**
- Modify: `kotorid/ic_sync.py` (in `refresh_ic_state`, after each IC's debit is updated)
- Modify: `kotorid/notify.py` (style)
- Test: `tests/test_ic_sync.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ic_sync.py`. The exact mock pattern depends on the existing httpx stub in that file — read it first.

```python
import json as _json
from datetime import date

import pytest

from kotorid.alerts_lib import ALERT_FIELDS_KEY


@pytest.mark.asyncio
async def test_refresh_ic_state_fires_short_strike_threatened(tmp_path):
    """When underlying is within 1% of either short strike, emit alert."""
    # Setup: IC with short_put=735, short_call=760. Underlying at 740 = within
    # 1% of short_put (735 * 1.01 = 742.35 ≥ 740 ≥ 735 * 0.99 = 727.65).
    # Wait — "within 1%" means |underlying - short_strike| / short_strike <= 0.01.
    # For short_put=735: 735*0.99=727.65 to 735*1.01=742.35.
    # Underlying 740 is within that band → threatened.
    #
    # Mock the chain so the test doesn't need real Tradier.
    # Reuse the httpx mock pattern from existing test_ic_sync tests.
    ...
    # Assertion shape:
    # cur = await db.execute("SELECT message FROM alerts WHERE alert_type='short_strike_threatened'")
    # row = await cur.fetchone()
    # assert row is not None
    # _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    # fields = _json.loads(json_tail)["fields"]
    # assert fields["short_strike"] == 735
    # assert fields["side"] == "put"


@pytest.mark.asyncio
async def test_refresh_ic_state_dedup_short_strike_same_day(tmp_path):
    """Two refresh cycles same day → one alert."""
    ...
```

(The `...` placeholders mark where the existing `test_ic_sync.py` mock pattern goes — read that file and mirror.)

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_ic_sync.py -v -k short_strike
```

Expected: FAIL on the structured assertions.

- [ ] **Step 3: Implement**

In `kotorid/ic_sync.py`, inside `refresh_ic_state`, after the `UPDATE ic_positions SET current_debit=...` for each IC, add:

```python
        # short_strike_threatened: underlying within 1% of either short strike.
        # Fires once per IC per day (de-duped by short_strike_warned_at).
        # Underlying price comes from quoting the underlying ticker.
        from datetime import date as _date
        from kotorid.alerts_lib import create_alert as _create_alert
        today_iso = _date.today().isoformat()
        warned_cur = await db.execute(
            "SELECT short_strike_warned_at FROM ic_positions WHERE id=?",
            (ic["id"],),
        )
        warned = (await warned_cur.fetchone())[0]
        if warned == today_iso:
            continue  # already warned today

        underlying_quote = quotes.get(ic["symbol"])
        if not underlying_quote:
            continue
        underlying_price = None
        for k in ("last", "bid", "ask"):
            v = underlying_quote.get(k)
            if v is not None:
                try:
                    underlying_price = float(v)
                    break
                except (TypeError, ValueError):
                    continue
        if underlying_price is None:
            continue

        short_put = float(ic["short_put"])
        short_call = float(ic["short_call"])
        threatened_side = None
        if abs(underlying_price - short_put) / short_put <= 0.01:
            threatened_side = "put"
            threatened_strike = short_put
        elif abs(underlying_price - short_call) / short_call <= 0.01:
            threatened_side = "call"
            threatened_strike = short_call

        if threatened_side:
            distance_pct = (underlying_price - threatened_strike) / threatened_strike
            await _create_alert(
                db,
                alert_type="short_strike_threatened",
                symbol=ic["symbol"],
                headline=f"Short Strike Threatened — {ic['symbol']}",
                body_lines=[
                    f"{ic['symbol']} at ${underlying_price:.2f}, "
                    f"{distance_pct:+.2%} from short {threatened_side} {threatened_strike:.0f}.",
                    f"Current debit ${debit:.2f}; if this strike breaches, the IC may stop out.",
                ],
                fields={
                    "underlying_price": underlying_price,
                    "short_strike": threatened_strike,
                    "side": threatened_side,
                    "distance_pct": distance_pct,
                    "current_debit": debit,
                },
            )
            await db.execute(
                "UPDATE ic_positions SET short_strike_warned_at=? WHERE id=?",
                (today_iso, ic["id"]),
            )
```

You'll need to include `ic["symbol"]` in the `all_symbols` set near the top of `refresh_ic_state` so the underlying is quoted. Add after the existing leg-symbol building:

```python
        all_symbols.add(ic["symbol"])  # for short_strike_threatened underlying check
```

In `kotorid/notify.py`:

```python
    "short_strike_threatened": ("🚨 Short Strike Threatened", _COLOR_RED),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_ic_sync.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/ic_sync.py kotorid/notify.py tests/test_ic_sync.py
git commit -m "add short_strike_threatened alert"
```

---

## Task 10: Track order_id on ic_positions during placement

**Files:**
- Modify: `kotorid/order_placement.py:67-88` (`_materialize_ic_position`)
- Test: `tests/test_order_placement.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_order_placement.py`:

```python
@pytest.mark.asyncio
async def test_materialize_ic_position_stores_order_id(tmp_path, ...):
    """When Tradier returns order id 12345, ic_positions.order_id == '12345'."""
    # Use the existing mock pattern in this file.
    # After place_approved_candidates runs successfully:
    cur = await db.execute("SELECT order_id FROM ic_positions WHERE symbol='SPY'")
    row = await cur.fetchone()
    assert row["order_id"] == "12345"  # the mocked Tradier order id
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_order_placement.py::test_materialize_ic_position_stores_order_id -v
```

Expected: FAIL — order_id is NULL.

- [ ] **Step 3: Implement**

Modify `_materialize_ic_position` signature to accept `order_id`, and the caller in `place_approved_candidates` to pass it:

```python
async def _materialize_ic_position(
    db: aiosqlite.Connection,
    candidate: aiosqlite.Row,
    expiry: str,
    order_id: str | None = None,
) -> None:
    spread_width = float(candidate["long_call"]) - float(candidate["short_call"])
    contracts = candidate["contracts"] or 1
    await db.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
            spread_width, entry_credit, contracts, max_loss, regime_at_entry,
            agent_run_id, order_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            candidate["symbol"], date.today().isoformat(), expiry,
            float(candidate["short_call"]), float(candidate["long_call"]),
            float(candidate["short_put"]), float(candidate["long_put"]),
            spread_width, float(candidate["expected_credit"]),
            contracts, float(candidate["max_loss"]), "normal",
            candidate["agent_run_id"], order_id,
        ),
    )
```

And in `place_approved_candidates`, change the call site:

```python
        order_id_str = str(order_resp.get("order", {}).get("id", "")) or None
        await _materialize_ic_position(db, cand, expiry, order_id=order_id_str)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_order_placement.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/order_placement.py tests/test_order_placement.py
git commit -m "track order_id on ic_positions"
```

---

## Task 11: order_status.py — poll Tradier for fills, emit order_filled

**Files:**
- Create: `kotorid/order_status.py`
- Create: `tests/test_order_status.py`
- Modify: `kotorid/notify.py` (style)

- [ ] **Step 1: Write the failing test**

Create `tests/test_order_status.py`:

```python
import json
import pytest
import httpx

from kotorid.alerts_lib import ALERT_FIELDS_KEY
from kotorid.db import get_db, init_db
from kotorid.order_status import check_open_orders


@pytest.mark.asyncio
async def test_check_open_orders_emits_order_filled_on_fill(tmp_path):
    """Fully-filled order produces order_filled alert with slippage."""
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        # IC placed with estimate $1.00, order_id 12345, fill_status NULL
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, order_id)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,'12345')"""
        )
        await db.commit()

        # Mock Tradier: order status = filled, avg_fill_price = 0.98 net credit
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "order": {
                    "id": 12345, "status": "filled", "avg_fill_price": 0.98,
                    "class": "multileg",
                }
            })
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://x/v1") as client:
            await check_open_orders(db, client, account_id="VA1")

        cur = await db.execute("SELECT message FROM alerts WHERE alert_type='order_filled'")
        row = await cur.fetchone()

    assert row is not None
    assert ALERT_FIELDS_KEY in row["message"]
    _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    fields = json.loads(json_tail)["fields"]
    assert fields["order_id"] == "12345"
    assert fields["fill_credit"] == 0.98
    assert fields["estimated_credit"] == 1.00
    # slippage = (fill - estimate) / estimate = -0.02
    assert fields["slippage_pct"] == pytest.approx(-0.02, abs=1e-4)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_order_status.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement**

Create `kotorid/order_status.py`:

```python
"""Poll Tradier order status for placed ICs; emit order_filled/order_failed.

Iterates ``ic_positions`` rows whose ``order_id`` is set but whose
status hasn't been resolved yet (we treat status as resolved once an
order_filled or order_failed alert exists for that order_id, OR the IC
has exit_reason set).

Tradier returns ``avg_fill_price`` for filled multileg orders as the
net credit per share. Slippage is computed against the candidate's
estimated entry_credit.
"""
from __future__ import annotations

import logging

import aiosqlite
import httpx

from kotorid.alerts_lib import create_alert

log = logging.getLogger(__name__)


async def _already_resolved(db: aiosqlite.Connection, order_id: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM alerts "
        "WHERE alert_type IN ('order_filled','order_failed') "
        "  AND symbol IN (SELECT symbol FROM ic_positions WHERE order_id=?) "
        "LIMIT 1",
        (order_id,),
    )
    return (await cur.fetchone()) is not None


async def check_open_orders(
    db: aiosqlite.Connection, client: httpx.AsyncClient, account_id: str,
) -> int:
    """Poll Tradier for each ic_position with an unresolved order_id.

    Returns the number of order alerts created in this pass.
    """
    cur = await db.execute(
        "SELECT id, symbol, order_id, entry_credit FROM ic_positions "
        "WHERE order_id IS NOT NULL AND exit_reason IS NULL"
    )
    rows = await cur.fetchall()
    created = 0

    for ic in rows:
        order_id = ic["order_id"]
        if await _already_resolved(db, order_id):
            continue

        try:
            resp = await client.get(
                f"/accounts/{account_id}/orders/{order_id}",
                params={"includeTags": "true"},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            log.warning("check_open_orders: failed to fetch order %s; will retry", order_id)
            continue

        order = resp.json().get("order", {}) or {}
        status = (order.get("status") or "").lower()

        if status == "filled":
            fill_credit = float(order.get("avg_fill_price") or 0.0)
            estimate = float(ic["entry_credit"] or 0.0)
            slippage = (fill_credit - estimate) / estimate if estimate else 0.0
            await create_alert(
                db,
                alert_type="order_filled",
                symbol=ic["symbol"],
                headline=f"Order Filled — {ic['symbol']}",
                body_lines=[
                    f"Multileg order {order_id} filled at credit ${fill_credit:.2f} "
                    f"(estimated ${estimate:.2f}, slippage {slippage:+.1%}).",
                ],
                fields={
                    "order_id": str(order_id),
                    "fill_credit": fill_credit,
                    "estimated_credit": estimate,
                    "slippage_pct": slippage,
                },
            )
            created += 1
        elif status in ("rejected", "canceled", "expired"):
            reason = order.get("reason_description") or status
            await create_alert(
                db,
                alert_type="order_failed",
                symbol=ic["symbol"],
                headline=f"Order Failed — {ic['symbol']}",
                body_lines=[
                    f"Multileg order {order_id} status: {status}.",
                    f"Reason: {reason}.",
                ],
                fields={
                    "order_id": str(order_id),
                    "status": status,
                    "reason": reason,
                },
            )
            created += 1
        # statuses like 'pending', 'open', 'partially_filled' — keep polling

    if created:
        await db.commit()
    return created
```

In `kotorid/notify.py`:

```python
    "order_filled": ("🟢 Order Filled", _COLOR_GREEN),
    "order_failed": ("⚠️ Order Failed", _COLOR_ORANGE),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_order_status.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/order_status.py kotorid/notify.py tests/test_order_status.py
git commit -m "add order_filled and order_failed alerts via status polling"
```

---

## Task 12: order_failed test coverage

**Files:**
- Modify: `tests/test_order_status.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_order_status.py`:

```python
@pytest.mark.asyncio
async def test_check_open_orders_emits_order_failed_on_reject(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, order_id)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,'99999')"""
        )
        await db.commit()

        def handler(request):
            return httpx.Response(200, json={
                "order": {
                    "id": 99999, "status": "rejected",
                    "reason_description": "Insufficient buying power",
                }
            })
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://x/v1") as client:
            await check_open_orders(db, client, account_id="VA1")

        cur = await db.execute("SELECT message FROM alerts WHERE alert_type='order_failed'")
        row = await cur.fetchone()
    assert row is not None
    _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    fields = json.loads(json_tail)["fields"]
    assert fields["status"] == "rejected"
    assert "Insufficient buying power" in fields["reason"]


@pytest.mark.asyncio
async def test_check_open_orders_dedup(tmp_path):
    """Polling twice on the same filled order produces only one alert."""
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, order_id)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,'77777')"""
        )
        await db.commit()

        def handler(request):
            return httpx.Response(200, json={
                "order": {"id": 77777, "status": "filled", "avg_fill_price": 1.00}
            })
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://x/v1") as client:
            await check_open_orders(db, client, account_id="VA1")
            await check_open_orders(db, client, account_id="VA1")

        cur = await db.execute(
            "SELECT COUNT(*) FROM alerts WHERE alert_type='order_filled'"
        )
        (count,) = await cur.fetchone()
    assert count == 1
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
pytest tests/test_order_status.py -v
```

Expected: 3 PASS (the failing case from Task 11 plus these two).

- [ ] **Step 3: Commit**

```bash
git add tests/test_order_status.py
git commit -m "add order_failed and dedup tests for order_status"
```

---

## Task 13: heartbeat.py — build the digest string

**Files:**
- Create: `kotorid/heartbeat.py`
- Create: `tests/test_heartbeat.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_heartbeat.py`:

```python
import pytest

from kotorid.db import get_db, init_db
from kotorid.heartbeat import build_heartbeat_line


@pytest.mark.asyncio
async def test_heartbeat_line_with_no_positions(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        line = await build_heartbeat_line(db, now_ct_label="14:30 CT")
    assert "14:30 CT" in line
    assert "0 ICs" in line


@pytest.mark.asyncio
async def test_heartbeat_line_with_one_ic_open(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,0.82)"""
        )
        await db.commit()
        line = await build_heartbeat_line(db, now_ct_label="14:30 CT")

    assert "1 IC" in line
    assert "SPY" in line
    assert "5/29" in line or "2026-05-29" in line
    # P/L per share = (1.00 - 0.82) = 0.18; dollars = 18.
    # Format may show "+$18" or "+18" — either acceptable.
    assert "+$18" in line or "+18" in line


@pytest.mark.asyncio
async def test_heartbeat_line_includes_last_scan_outcome(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc).isoformat()
        # Insert a candidates row to represent "today's scan"
        await db.execute(
            """INSERT INTO candidates (symbol, scan_date, order_status, expected_credit,
                                       contracts, max_loss, short_call, long_call,
                                       short_put, long_put)
               VALUES ('SPY', date('now'), 'pending_approval', 1.00, 1, 400,
                       760, 765, 735, 730)"""
        )
        await db.commit()
        line = await build_heartbeat_line(db, now_ct_label="14:30 CT")
    assert "scan:" in line.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_heartbeat.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement**

Create `kotorid/heartbeat.py`:

```python
"""Heartbeat: a one-line state digest posted to Discord every 15 min.

Heartbeats are state snapshots, not events. They bypass the ``alerts``
table and post directly through ``post_heartbeat``. The whole point is
that a quiet period still produces predictable, scannable lines so the
user knows the system is alive.
"""
from __future__ import annotations

import logging
from datetime import date

import aiosqlite
import httpx

log = logging.getLogger(__name__)


def _short_expiry(iso: str) -> str:
    """Render '2026-05-29' as '5/29'."""
    try:
        y, m, d = iso.split("-")
        return f"{int(m)}/{int(d)}"
    except (ValueError, AttributeError):
        return iso


async def build_heartbeat_line(
    db: aiosqlite.Connection, *, now_ct_label: str,
) -> str:
    """Build the one-line heartbeat string from current DB state."""
    cur = await db.execute(
        "SELECT symbol, expiry, entry_credit, current_debit, contracts "
        "FROM ic_positions WHERE exit_reason IS NULL"
    )
    open_ics = await cur.fetchall()

    parts: list[str] = [f"ℹ️  {now_ct_label}"]
    parts.append(f"{len(open_ics)} IC{'s' if len(open_ics) != 1 else ''}")

    for ic in open_ics:
        sym = ic["symbol"]
        exp = _short_expiry(ic["expiry"])
        debit = ic["current_debit"]
        entry = ic["entry_credit"] or 0
        if debit is not None and entry > 0:
            pnl_dollars = (entry - debit) * 100 * (ic["contracts"] or 1)
            pnl_pct = (entry - debit) / entry
            parts.append(
                f"{sym} {exp} debit ${debit:.2f} "
                f"(P/L ${pnl_dollars:+.0f}, {pnl_pct:+.0%})"
            )
        else:
            parts.append(f"{sym} {exp} debit ?")

    # Last scan outcome (today's candidates row count)
    scan_cur = await db.execute(
        "SELECT COUNT(*) FROM candidates WHERE scan_date=?",
        (date.today().isoformat(),),
    )
    (today_candidates,) = await scan_cur.fetchone()
    if today_candidates > 0:
        parts.append(f"scan: {today_candidates} candidate(s) today")
    else:
        parts.append("scan: 0 candidates today")

    return " · ".join(parts)


async def post_heartbeat(
    client: httpx.AsyncClient, webhook_url: str, line: str,
) -> bool:
    """POST the heartbeat as a low-color embed; return True on success."""
    payload = {
        "embeds": [{
            "description": line,
            "color": 9807270,  # gray, distinct from event colors
            "footer": {"text": "kotori heartbeat"},
        }]
    }
    try:
        resp = await client.post(webhook_url, json=payload, timeout=10.0)
        resp.raise_for_status()
        return True
    except httpx.HTTPError:
        log.exception("post_heartbeat: POST failed; will retry next cycle")
        return False
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_heartbeat.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/heartbeat.py tests/test_heartbeat.py
git commit -m "add heartbeat digest builder"
```

---

## Task 14: Schedule heartbeat in __main__.py

**Files:**
- Modify: `kotorid/__main__.py` (add scheduled job)
- Test: smoke test the wiring locally

- [ ] **Step 1: Add the scheduled job wrapper**

In `kotorid/__main__.py`, after `_scheduled_notify_alerts`:

```python
async def _scheduled_heartbeat():
    """Recurring job: post a heartbeat digest to Discord."""
    from kotorid.heartbeat import build_heartbeat_line, post_heartbeat
    from datetime import datetime
    url = webhook_url()
    if not url:
        return
    try:
        async with get_db(DB_PATH) as db:
            async with httpx.AsyncClient() as client:
                now_ct = datetime.now(tz=CT).strftime("%H:%M CT")
                line = await build_heartbeat_line(db, now_ct_label=now_ct)
                await post_heartbeat(client, url, line)
    except Exception:
        log.exception("scheduled heartbeat failed")
```

In the scheduler-registration block, only when webhook_url() is set, add (alongside the existing notify_alerts registration):

```python
        # Every 15 min during market hours — heartbeat digest
        scheduler.add_job(
            _scheduled_heartbeat,
            CronTrigger(
                day_of_week="mon-fri",
                hour="8-15",
                minute="0,15,30,45",
                timezone=CT,
            ),
            id="heartbeat",
        )
        log.info("heartbeat: registered, every 15min Mon-Fri 08:00-15:45 CT")
```

(Note: `hour="8-15"` together with `minute="0,15,30,45"` produces 32 firings/day — 08:00, 08:15, …, 15:45. That matches the spec's "08:00–15:30 CT" intent with one extra at 15:45 which is fine.)

- [ ] **Step 2: Smoke test locally (or stub-test)**

Add to `tests/test_heartbeat.py`:

```python
import httpx
import pytest

from kotorid.heartbeat import post_heartbeat


@pytest.mark.asyncio
async def test_post_heartbeat_returns_true_on_204():
    def handler(request):
        return httpx.Response(204)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await post_heartbeat(client, "https://discord.test/webhook", "ℹ️ test")
    assert ok is True


@pytest.mark.asyncio
async def test_post_heartbeat_returns_false_on_error():
    def handler(request):
        return httpx.Response(500)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await post_heartbeat(client, "https://discord.test/webhook", "ℹ️ test")
    assert ok is False
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_heartbeat.py -v
```

Expected: 5 PASS.

- [ ] **Step 4: Commit**

```bash
git add kotorid/__main__.py tests/test_heartbeat.py
git commit -m "schedule heartbeat every 15min during market hours"
```

---

## Task 15: Post morning briefing to Discord

**Files:**
- Modify: `kotorid/jobs.py` (extend `generate_briefing`)
- Test: extend `tests/test_briefing_view_formatting.py` or a new test file

- [ ] **Step 1: Write the failing test**

Create `tests/test_briefing_discord_post.py`:

```python
import pytest
import httpx

from kotorid.db import get_db, init_db
from kotorid.jobs import post_latest_briefing_to_discord


@pytest.mark.asyncio
async def test_post_latest_briefing_posts_today_briefing(tmp_path, monkeypatch):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await db.execute(
            "INSERT INTO briefings (period, content, generated_at) "
            "VALUES ('daily', 'Today: hold positions, watch SPY 730 short put.', "
            "datetime('now'))"
        )
        await db.commit()

        posted_payloads = []
        def handler(request):
            posted_payloads.append(request.read())
            return httpx.Response(204)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ok = await post_latest_briefing_to_discord(
                db, client, "https://discord.test/webhook"
            )

    assert ok is True
    assert len(posted_payloads) == 1
    assert b"hold positions" in posted_payloads[0]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_briefing_discord_post.py -v
```

Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Implement**

In `kotorid/jobs.py`, add:

```python
async def post_latest_briefing_to_discord(
    db: aiosqlite.Connection, client: httpx.AsyncClient, webhook_url: str,
) -> bool:
    """Post today's most recent daily briefing as a large embed."""
    cur = await db.execute(
        "SELECT content, generated_at FROM briefings "
        "WHERE period='daily' AND date(generated_at)=date('now') "
        "ORDER BY id DESC LIMIT 1"
    )
    row = await cur.fetchone()
    if not row:
        return False
    payload = {
        "embeds": [{
            "title": "📊 Morning Briefing",
            "description": row["content"][:4000],  # Discord embed limit
            "color": 3447003,  # blue
            "timestamp": row["generated_at"],
            "footer": {"text": "kotori"},
        }]
    }
    try:
        resp = await client.post(webhook_url, json=payload, timeout=10.0)
        resp.raise_for_status()
        return True
    except httpx.HTTPError:
        log.exception("post_latest_briefing_to_discord: POST failed")
        return False
```

Then update the scheduled `generate_briefing` job (or wrap it in `__main__.py`) so that after generation, the post is attempted. Cleanest: extend the existing scheduled-job wrapper in `__main__.py`:

```python
async def _scheduled_briefing():
    """Run briefing generation; on success, post to Discord if configured."""
    import httpx as _httpx
    try:
        async with get_db(DB_PATH) as db:
            await jobs.generate_briefing(db)
            url = webhook_url()
            if url:
                async with _httpx.AsyncClient() as client:
                    await jobs.post_latest_briefing_to_discord(db, client, url)
    except Exception:
        log.exception("scheduled briefing failed")
```

And update the existing `scheduler.add_job(jobs.generate_briefing, ...)` call to point at `_scheduled_briefing` instead.

(The existing `jobs.generate_briefing` signature may not match the call shown — check it. If it doesn't accept a db arg, factor out the briefing-writing into a function that does, or call the existing one and then read+post separately.)

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_briefing_discord_post.py tests/test_briefing_view_formatting.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/jobs.py kotorid/__main__.py tests/test_briefing_discord_post.py
git commit -m "post morning briefing to discord"
```

---

## Task 16: EOD recap synthesis + scheduled job

**Files:**
- Modify: `kotorid/jobs.py` (add `build_eod_recap_payload` + `eod_recap_job`)
- Create: `tests/test_eod_recap.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_eod_recap.py`:

```python
import pytest
from datetime import date

from kotorid.db import get_db, init_db
from kotorid.jobs import build_eod_recap_payload


@pytest.mark.asyncio
async def test_eod_recap_includes_realized_pnl_today(tmp_path):
    db_path = str(tmp_path / "kotori.db")
    async with get_db(db_path) as db:
        await init_db(db)
        # Closed today: profit_target, +$50 realized
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit, exit_reason,
                exit_debit, realized_pnl)
               VALUES ('SPY','2026-05-19','2026-05-22',
                       760,765,735,730,5,1.00,1,400,0.50,
                       'profit_target', 0.50, 50.0)"""
        )
        # Open: SPY 5/29, current debit $0.82
        await db.execute(
            """INSERT INTO ic_positions
               (symbol, entry_date, expiry, short_call, long_call,
                short_put, long_put, spread_width, entry_credit,
                contracts, max_loss, current_debit)
               VALUES ('SPY','2026-05-22','2026-05-29',
                       760,765,735,730,5,1.00,1,400,0.82)"""
        )
        await db.commit()

        # Pretend the profit_target close happened today via the entry_date matching
        # — for realistic tests, also insert an alerts row with date('now') as triggered_at:
        await db.execute(
            "INSERT INTO alerts (symbol, alert_type, message, triggered_at) "
            "VALUES ('SPY','profit_target','SPY closed', datetime('now'))"
        )
        await db.commit()

        payload = await build_eod_recap_payload(db)

    assert payload["embeds"][0]["title"].startswith("📈 EOD Recap")
    desc = payload["embeds"][0]["description"]
    assert "+$50" in desc
    assert "1 IC open" in desc or "1 ICs open" in desc
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_eod_recap.py -v
```

Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Implement**

In `kotorid/jobs.py`:

```python
async def build_eod_recap_payload(db: aiosqlite.Connection) -> dict:
    """Build the EOD recap Discord payload from today's activity."""
    from datetime import date
    today_iso = date.today().isoformat()

    # Realized P/L today: sum of realized_pnl for ICs whose closing alert
    # was triggered today.
    realized_cur = await db.execute(
        """SELECT IFNULL(SUM(p.realized_pnl), 0) AS total,
                  COUNT(*) AS closed_count,
                  SUM(CASE WHEN p.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
           FROM ic_positions p
           JOIN alerts a ON a.symbol = p.symbol
           WHERE a.alert_type IN ('profit_target','stop_loss','force_close')
             AND date(a.triggered_at)=?
             AND p.exit_reason IS NOT NULL"""
,
        (today_iso,),
    )
    realized_row = await realized_cur.fetchone()
    realized = realized_row["total"] or 0.0
    closed_count = realized_row["closed_count"] or 0
    wins = realized_row["wins"] or 0
    losses = closed_count - wins

    open_cur = await db.execute(
        "SELECT COUNT(*) AS n FROM ic_positions WHERE exit_reason IS NULL"
    )
    open_count = (await open_cur.fetchone())["n"]

    lines = [
        f"📈 EOD Recap — {date.today().strftime('%a %b %d %Y')}",
        f"• Realized P/L today: ${realized:+.0f} "
        f"({wins} win{'s' if wins != 1 else ''}, "
        f"{losses} loss{'es' if losses != 1 else ''})",
        f"• {open_count} IC{'s' if open_count != 1 else ''} open",
        f"• {closed_count} closed today",
    ]
    return {
        "embeds": [{
            "title": lines[0],
            "description": "\n".join(lines[1:]),
            "color": 3066993,
            "footer": {"text": "kotori"},
        }]
    }


async def eod_recap_job():
    """Scheduled wrapper: build EOD recap and post to Discord."""
    import httpx as _httpx
    from kotorid.config import DB_PATH
    from kotorid.db import get_db as _get_db
    from kotorid.notify import webhook_url as _webhook_url
    url = _webhook_url()
    if not url:
        return
    try:
        async with _get_db(DB_PATH) as db:
            payload = await build_eod_recap_payload(db)
            async with _httpx.AsyncClient() as client:
                await client.post(url, json=payload, timeout=10.0)
    except Exception:
        log.exception("eod_recap_job failed")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_eod_recap.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kotorid/jobs.py tests/test_eod_recap.py
git commit -m "add eod recap builder and job"
```

---

## Task 17: Wire all new jobs in __main__.py

**Files:**
- Modify: `kotorid/__main__.py`

- [ ] **Step 1: Add wrappers + registrations**

In `kotorid/__main__.py`, add wrappers:

```python
async def _scheduled_dte_check():
    try:
        async with get_db(DB_PATH) as db:
            await jobs.dte_check(db)
    except Exception:
        log.exception("scheduled dte_check failed")


async def _scheduled_order_status_check():
    """Poll Tradier for unresolved order status; one-shot per call."""
    if not TRADIER_API_KEY:
        return
    try:
        async with get_db(DB_PATH) as db:
            async with build_client() as client:
                account_id = await get_account_id(client)
                from kotorid.order_status import check_open_orders
                await check_open_orders(db, client, account_id)
    except Exception:
        log.exception("scheduled order_status_check failed")
```

In the scheduler registration block, add (after the existing TRADIER_API_KEY-gated block):

```python
    # 09:00 CT — DTE warning sweep
    scheduler.add_job(
        _scheduled_dte_check,
        CronTrigger(hour=9, minute=0, timezone=CT),
        id="dte_check",
    )
    # 15:30 CT — EOD recap
    scheduler.add_job(
        jobs.eod_recap_job,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=CT),
        id="eod_recap",
    )
    # Every 90s during market hours — poll Tradier for order status (cheap)
    if TRADIER_API_KEY:
        scheduler.add_job(
            _scheduled_order_status_check,
            "interval",
            seconds=90,
            id="order_status_check",
        )
```

- [ ] **Step 2: Validate the schedule registration by booting the daemon locally**

Run (in a separate terminal):

```bash
KOTORI_SEED_MOCK=1 python -m kotorid 2>&1 | head -50
```

Expected: log lines showing the new jobs registered without error. Look for:
```
Adding job ... id='heartbeat'
Adding job ... id='dte_check'
Adding job ... id='eod_recap'
Adding job ... id='order_status_check'  (only if TRADIER_API_KEY set)
```

Then Ctrl-C.

- [ ] **Step 3: Commit**

```bash
git add kotorid/__main__.py
git commit -m "register dte_check, eod_recap, order_status_check jobs"
```

---

## Task 18: Deploy & verify on Railway

**Files:**
- None (operational task)

- [ ] **Step 1: Push to origin/main**

```bash
git push origin main
```

Railway will auto-deploy on push.

- [ ] **Step 2: Wait for deployment**

```bash
until railway deployment list 2>&1 | head -2 | tail -1 | grep -qE "SUCCESS|FAILED|CRASHED"; do sleep 10; done
railway deployment list | head -3
```

Expected: top deployment shows `SUCCESS`.

- [ ] **Step 3: Verify startup logs**

```bash
railway logs 2>&1 | grep -E "notify_alerts|heartbeat|dte_check|eod_recap|order_status|Scheduler started|kotorid running" | tail -30
```

Expected: see lines registering each new job and `Scheduler started`.

- [ ] **Step 4: Wait for next 15-min heartbeat tick and verify in Discord**

Check the Discord channel — within 15 minutes of deployment a `ℹ️` gray heartbeat embed should appear with the live position state. If not seen within 20 min, check `railway logs` for `post_heartbeat: POST failed`.

- [ ] **Step 5: Final integration sweep — run all tests against the new code**

```bash
pytest tests/ -v
```

Expected: all tests PASS (96 prior + ~25 new).

- [ ] **Step 6: Final commit (if anything fixed up during verification)**

```bash
git add -A && git diff --cached --quiet || git commit -m "fixups from railway verification"
git push origin main
```

---

## Self-Review (run before handing off)

### Spec coverage
- [x] Heartbeat (15min, market hours) — Task 13, 14
- [x] 11 event alerts (5 enriched + 6 new) — Tasks 4, 5, 6, 7, 8, 9, 11, 12
- [x] Schema changes (3 columns) — Task 1
- [x] alerts_lib centralized helper — Task 2
- [x] notify enrichment — Task 3
- [x] Order tracking + status polling — Tasks 10, 11, 12
- [x] Morning briefing post — Task 15
- [x] EOD recap — Task 16
- [x] Wire-up + verification — Tasks 17, 18

### Type consistency
- `create_alert(db, *, alert_type, symbol, headline, body_lines, fields, triggered_at)` used identically across all callers.
- `ALERT_FIELDS_KEY` referenced from `alerts_lib` in every test that parses messages.
- `parse_alert_message` used in `notify.format_alert_embed`.
- New scheduled job names: `heartbeat`, `dte_check`, `eod_recap`, `order_status_check` — all referenced consistently in `__main__.py`.

### Open questions resolved in spec
1. Pre-market refresh staleness — heartbeat shows whatever `current_debit` is, labeled implicitly by the line itself.
2. EOD recap calendar day — uses CT (`date.today()` runs in daemon TZ, which is CT on Railway).
3. Short-strike threshold — 1% globally for v1.
