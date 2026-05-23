# Deploying kotorid to Railway

`kotorid` is a long-running daemon — no HTTP server, no inbound traffic.
It needs persistent storage for the SQLite DB and outbound HTTPS access
to Tradier. Railway's Hobby plan ($5/mo) is right-sized.

## What this deploys

- **The daemon only.** The TUI (`kotori`) doesn't run here; it's
  interactive and needs a terminal. Until we add a remote DB or a
  notification surface, monitoring happens via Railway's log viewer.
- **A persistent volume** at `/data` for `kotori.db`. SQLite state
  survives restarts and redeploys.
- **No public URL.** This service has no inbound traffic.

## One-time setup

1. **Create a Railway project** and link this repo.
   - Web: <https://railway.app/new> → "Deploy from GitHub repo" → pick `kotori`.
   - CLI: `railway login && railway init && railway link`.

2. **Add a persistent volume.**
   - Service → Settings → Volumes → "Mount Path" = `/data`.
   - Railway provisions the volume automatically.

3. **Set environment variables** (Service → Variables):

   | Key | Value | Notes |
   |-----|-------|-------|
   | `TRADIER_API_KEY` | your sandbox or prod key | required |
   | `TRADIER_ENV` | `sandbox` or `production` | required |
   | `TRADIER_ACCOUNT_ID` | your account number (e.g. `VA46630435`) | required; skips a `/user/profile` call |
   | `ANTHROPIC_API_KEY` | your Claude key | optional; enables AI briefings |
   | `KOTORI_WATCHLIST` | `SPY,QQQ,META,...` | optional; defaults to the 10 names in `candidate_scan.DEFAULT_WATCHLIST` |
   | `KOTORI_DB` | `/data/kotori.db` | **leave the default** — matches the volume mount |

   Do **not** set `KOTORI_SEED_MOCK` in production. It enables demo
   stub jobs (fake IV ingest, hardcoded candidate JSON) that pollute
   live data.

4. **Deploy.** Railway picks up the `Dockerfile` and `railway.json`
   automatically. First build takes ~2-3 minutes.

## Verifying it's running

- **Logs** — Service → Deployments → click the latest → "View Logs".
  Look for:
  ```
  ensure_db: synced N positions from Tradier (account=...)
  Scheduler started
  kotorid running (TRADIER_ENV=sandbox)
  ```
  And on the 60s cadence:
  ```
  position_sync: N positions synced
  refresh_ic_state: refreshed M IC(s)
  ```

- **DB inspection** — `railway shell` into the container, then
  `sqlite3 /data/kotori.db "SELECT symbol, current_debit, pct_max_profit FROM ic_positions WHERE exit_reason IS NULL;"`.

## Operating

- **Updating** — `git push` to main. Railway auto-deploys; the daemon
  restarts cleanly (SIGTERM → scheduler.shutdown → graceful exit before
  Railway's 30s kill timeout).
- **Rolling back** — Service → Deployments → click the previous green
  one → "Redeploy".
- **Watching for alerts** — Until a notification surface is wired,
  `railway logs --tail 100 | grep -E 'profit_target|stop_loss|ic_placed|force_close'`
  catches the events that matter.

## Open follow-ups

- **TUI access.** Daemon writes to `/data/kotori.db` on Railway, but
  the laptop's `kotori` TUI reads from `~/.kotori/kotori.db`. They're
  disconnected. Options for resolving later: (a) migrate the schema to
  Postgres so both can hit the same remote DB; (b) one-way replicate
  with `litestream`; (c) ship the TUI as a textual-serve web app on
  the same Railway service.
- **Notifications.** No SMS/email/push integration yet. Alerts only
  live in SQLite + the inbox; without the TUI you only see them in
  logs. A Discord/Slack webhook or Twilio integration would close
  this gap.
- **`entry_credit` precision.** Currently set from pre-trade mid
  prices in the candidate scan. After fill, a post-fill order detail
  query (`/accounts/{id}/orders/{order_id}`) would give actual fill
  prices for a more accurate `entry_credit`.
