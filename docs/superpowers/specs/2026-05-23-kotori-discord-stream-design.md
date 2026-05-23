# Kotori Discord Stream Design

**Date:** 2026-05-23
**Status:** Approved, ready for implementation plan

## Goal

Turn the single Discord channel into a real-time window into kotori's behavior — not just a notification list. The user must be able to read the channel and answer three questions: *what's going on right now*, *why did the system do what it did*, and *when did it happen*.

The user does not yet run the TUI locally, so Discord is currently the only live interaction surface. The design therefore biases toward more content rather than less; tuning down later is mechanical, tuning up requires having seen what was missing.

## Non-goals

- Slash commands or two-way Discord interaction (separate Tier-2 plan)
- Multi-channel routing
- Per-event mute / DND windows (Discord client-side, user-configurable)
- Mobile push prioritization

## Architecture: three streams, one channel

1. **Heartbeats** — periodic state snapshots, predictable cadence. The "system is alive, here's its current thought" stream.
2. **Events** — punctual posts when something specific happens, each explaining *why* not just *what*.
3. **Narratives** — daily long-form: morning briefing (existing) + EOD recap (new).

All three go to the same webhook. Discrimination happens via embed color, emoji prefix, and content density. Heartbeats are gray and single-line; events are colored and multi-line; narratives are large embeds.

## Heartbeats

**Cadence:** every 15 minutes, 08:00–15:30 CT (covers pre-market through post-close), weekdays only. NYSE holiday-skip via existing market-calendar logic.

**Implementation:** new `kotorid/heartbeat.py`. Builds digest string from DB state, POSTs directly to Discord — bypasses the `alerts` table because heartbeats are state snapshots, not events. Registered as a new APScheduler interval job in `__main__.py`.

**Format:** single-line text in a low-color (gray) embed.

```
ℹ️  14:30 CT · 2 ICs · SPY 5/29 debit $0.42 (P/L -$0.18, -18%) · QQQ 5/27 debit $0.92 (P/L -$0.12, -12%) · scan: 0/12 candidates (IV<30%ile) · refresh: ok · 0 err 15m
```

**Fields, in order:**
- Time in CT (matches daemon timezone; logs and heartbeats line up)
- Position count
- Per-IC: symbol, expiry, current debit, P/L in dollars *and* % of entry credit
- Last `candidate_scan`: ran-time + count/total + brief reason if zero
- Last `ic_refresh`: ok / errors / stale
- Error count in last 15 min (folds in former `data_outage` and `job_failure` signals)

Quiet periods produce repeated identical lines. That's intentional and informative — it confirms nothing changed.

## Event alerts

Eleven types total. Five existing get enriched content; six are new. All flow through the existing `alerts` table and `notify_alerts` job, but `format_alert_embed` is enhanced to render structured fields rather than just `message`.

| # | Type | Severity prefix | Trigger | New? |
|---|------|-----------------|---------|------|
| 1 | `ic_placed` | 🟢 FYI | order submitted to Tradier | existing, enrich |
| 2 | `order_filled` | 🟢 FYI | Tradier order status = filled | **new** |
| 3 | `candidate_ready` | ⚠️ action | `candidate_scan` produced ≥1 candidate | **new** |
| 4 | `dte_warning` | ⚠️ action | open IC with `expiry == today+1` at 09:00 CT | **new** |
| 5 | `position_warning` | ⚠️ risk | open IC loss ≥50% of max_loss, stop not yet fired | **new** |
| 6 | `short_strike_threatened` | 🚨 risk | underlying within 1% of either short strike | **new** |
| 7 | `gap_risk` | 🟠 risk | overnight gap monitor at 08:00 CT | existing, enrich |
| 8 | `profit_target` | 🟢 close | IC reaches 25% profit, auto-close | existing, enrich |
| 9 | `stop_loss` | 🔴 close | IC reaches max-loss threshold, auto-close | existing, enrich |
| 10 | `force_close` | 🔵 close | day after expiry, IC marked closed | existing, enrich |
| 11 | `order_failed` | ⚠️ action | Tradier order status = rejected/canceled | **new** |

### De-dup strategy

- `position_warning` — fires once per IC, ever. New column `ic_positions.position_warning_at` records first trip.
- `short_strike_threatened` — fires once per IC per side per day. New column `ic_positions.short_strike_warned_at` stores ISO date.
- `dte_warning` — query the alerts table for an existing `(dte_warning, symbol, today)` before inserting.
- All others are inherently one-shot (only one fill/exit/scan per cycle).

### Content shape (richer alerts)

Each event alert leads with the *why*, then the numbers. Three worked examples:

**`stop_loss` — before:** `"SPY IC: stop loss — P&L $-400"`

**`stop_loss` — after:**
> 🔴 **Stop Loss — SPY 5/29**
> Closed at debit $1.85 (entry credit $1.00, max debit $5.00).
> Loss: **−$400** (100% of max).
> Driver: short put 730 went $0.45 → $1.20 (+167%) over last 4h.
> Underlying: SPY $743 → $727 (−2.2%) since entry.
> Exit order: filled at 14:32 CT.

**`candidate_ready`:**
> ⚠️ **Candidate Ready — SPY 5/30**
> Credit $1.05 · Max loss $395 · 5 DTE
> Short put 728 / long put 723 · Short call 762 / long call 767
> IV percentile 67% · Expected move ±$8.50 · Cushion 2.0% / 2.5%
> Beat 11 other underlyings: best risk/reward score 0.265.
> Approve in TUI or wait — auto-place fallback at 14:50 CT.

**`order_filled`:**
> 🟢 **Order Filled — QQQ 5/27**
> Multileg order 30659298 filled at credit $1.02 (estimated $1.04, slippage −1.9%).
> 4 legs at market open 08:30 CT.

## Narratives

**Morning briefing** — already generated by `generate_briefing` at 07:00 CT and written to `briefings` table. New: post the briefing body as a large embed to Discord after generation. No new logic; just a Discord-post hook in `generate_briefing` (or a follow-up job).

**EOD recap** — new scheduled job `eod_recap` at 15:30 CT, weekdays. Synthesizes from `ic_positions` and today's `alerts` rows. Posts a single embed:

```
📈 EOD Recap — Mon May 26 2026
• Realized P/L today: +$120 (1 win, 0 losses)
• Open ICs: 2 (SPY 5/29 -$18, QQQ 5/27 -$12)
• Closed today: SPY 5/22 +$120 (profit_target)
• Scans: 1 ran (14:30 CT, 0 candidates)
• Errors: 0
```

## Schema changes

All additions go through the existing `_ensure_column` idempotent migration helper in `kotorid/db.py`. Compatible with both fresh and existing databases.

```sql
ALTER TABLE ic_positions ADD COLUMN order_id TEXT;
ALTER TABLE ic_positions ADD COLUMN position_warning_at TEXT;
ALTER TABLE ic_positions ADD COLUMN short_strike_warned_at TEXT;
```

- `order_id` — set in `order_placement.py` when Tradier returns the multileg order id; consumed by `position_sync` and `order_status_check` to correlate fills.
- `position_warning_at` — first-trip timestamp for the 50%-of-max-loss alert. Prevents re-firing.
- `short_strike_warned_at` — last date (ISO) the short-strike alert fired for this IC. Re-arms next day.

No new tables. De-dup queries piggyback on the existing `alerts` table.

## Code organization

**New files:**
- `kotorid/heartbeat.py` — builds the digest string from DB state; posts directly to Discord (bypasses `alerts`).
- `kotorid/alerts_lib.py` — centralized `create_alert(alert_type, symbol, **fields)` helper. Replaces sprawling `INSERT INTO alerts` call sites. Takes structured fields and renders the rich message string in one place, so format updates don't require touching every emit site.
- `kotorid/order_status.py` — polls Tradier `/accounts/{id}/orders/{order_id}` for 5 min after each submit. Emits `order_filled` on fill, `order_failed` on reject/cancel.

**Modified files:**
- `notify.py` — `format_alert_embed` reads structured fields where available; falls back to plain `message` for legacy rows.
- `candidate_scan.py` — emits `candidate_ready` at end of scan.
- `position_sync.py` — emits `order_filled` when a new position row appears with a matching `order_id`.
- `position_monitor.py` — emits `position_warning` on 50% breach.
- `ic_sync.py` — emits `short_strike_threatened` per refresh cycle, gated by `short_strike_warned_at`.
- `jobs.py` — richer content for existing `profit_target`/`stop_loss`/`force_close` rows; new jobs `dte_check` (09:00 CT) and `eod_recap` (15:30 CT).
- `__main__.py` — register new APScheduler jobs: `heartbeat` (15-min interval, market-day-gated), `dte_check` (cron 09:00 CT), `eod_recap` (cron 15:30 CT), `order_status_check` (60s interval, only active when open orders exist).
- `db.py` / `schema.sql` — three new `_ensure_column` invocations.

## Error handling & rate limits

- **Discord rate limit:** 30 messages/min per webhook. Heartbeat every 15 min + sporadic events = ceiling ~6 msgs/min at extreme. Nowhere near the cap.
- **Discord 4xx/5xx:** existing `notify.py` logic logs and continues; webhook stays armed for next cycle. Same behavior extended to heartbeat path.
- **Heartbeat on Discord outage:** failure is logged but does not block the daemon. The heartbeat is best-effort — a missed cycle is recoverable signal-wise (next one in 15 min).
- **Webhook URL unset:** every Discord-posting path checks `webhook_url()` first; no-op if missing. Behavior identical to current state.
- **Tradier order-status 404 during polling:** treat as transient, keep polling until the 5-min window expires. After window, give up and log.

## Testing

Estimated 25 new tests on top of the existing 96.

- **Per-alert (11):** mock DB state, invoke trigger logic, assert alerts row with expected structured content.
- **Heartbeat (5):** golden-text tests across scenarios — no positions, 1 IC, 2 ICs with errors, market closed (no post), market open with no scan yet (initial state).
- **De-dup (3):** assert `position_warning_at`, `short_strike_warned_at`, and the `dte_warning` query block second posts.
- **Order status polling (3):** simulated Tradier responses for filled, rejected, transient-404-then-filled.
- **Integration (3):** end-to-end with real SQLite — heartbeat path, event path through `alerts` + `notify_alerts`, and the morning briefing post hook.

## Open questions

- **Heartbeat content during pre-market (08:00–08:30 CT):** positions show prior-close debits since `ic_refresh` hasn't run for the day. Acceptable — heartbeat field labels the "refresh: last 16:00 CT" so context is clear.
- **EOD recap timezone for "today":** uses CT calendar day. Market close is 15:00 CT, EOD recap runs at 15:30 CT — same calendar day always.
- **Short-strike threshold for IWM / non-SPY underlyings:** 1% may be too tight for lower-priced underlyings. Punt: keep 1% globally for v1, revisit after we see IWM/QQQ behavior in practice.
