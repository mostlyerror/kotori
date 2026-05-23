import asyncio
import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from kotorid.candidate_scan import scan_candidates
from kotorid.config import DB_PATH, TRADIER_API_KEY
from kotorid.db import get_db, init_db
from kotorid.ic_sync import refresh_ic_state
from kotorid.mock_data import seed_mock_data
from kotorid.notify import notify_pending_alerts, webhook_url
from kotorid.position_sync import sync_positions
from kotorid.tradier_client import build_client, get_account_id
from kotorid import jobs

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
CT = pytz.timezone("America/Chicago")


async def ensure_db():
    """Initialize DB; sync real positions when credentials are set.

    Mock data is only seeded when KOTORI_SEED_MOCK is set explicitly — it
    used to auto-seed whenever TRADIER_API_KEY was unset, but that mingled
    stale demo rows into live runs whenever the key was temporarily lost.
    """
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with get_db(DB_PATH) as db:
        await init_db(db)
        if TRADIER_API_KEY:
            try:
                async with build_client() as client:
                    account_id = await get_account_id(client)
                    count = await sync_positions(db, client, account_id)
                log.info(
                    "ensure_db: synced %d positions from Tradier (account=%s)",
                    count,
                    account_id,
                )
            except Exception:
                log.exception("ensure_db: Tradier sync failed; continuing")
        elif os.environ.get("KOTORI_SEED_MOCK"):
            await seed_mock_data(db)
            log.info("ensure_db: KOTORI_SEED_MOCK set — seeded mock data")
        else:
            log.info(
                "ensure_db: TRADIER_API_KEY not set and KOTORI_SEED_MOCK not set "
                "— starting empty (set KOTORI_SEED_MOCK=1 to populate demo data)"
            )
        log.info("DB ready at %s", DB_PATH)


async def _scheduled_position_sync():
    """Recurring job: re-sync positions from Tradier."""
    try:
        async with get_db(DB_PATH) as db:
            async with build_client() as client:
                account_id = await get_account_id(client)
                await sync_positions(db, client, account_id)
    except Exception:
        log.exception("scheduled position_sync failed")


async def _scheduled_ic_refresh():
    """Recurring job: refresh current_debit / pct_max_profit for open ICs."""
    try:
        async with get_db(DB_PATH) as db:
            async with build_client() as client:
                await refresh_ic_state(db, client)
    except Exception:
        log.exception("scheduled ic_refresh failed")


async def _scheduled_candidate_scan():
    """Daily job: scan the watchlist for iron-condor candidates."""
    try:
        async with get_db(DB_PATH) as db:
            async with build_client() as client:
                await scan_candidates(db, client)
    except Exception:
        log.exception("scheduled candidate_scan failed")


async def _scheduled_notify_alerts():
    """Recurring job: post unnotified alerts to Discord."""
    url = webhook_url()
    if not url:
        return
    try:
        async with get_db(DB_PATH) as db:
            async with httpx.AsyncClient() as client:
                await notify_pending_alerts(db, client, url)
    except Exception:
        log.exception("scheduled notify_alerts failed")


async def _scheduled_briefing():
    """Run briefing generation; on success, post to Discord if configured.

    generate_briefing() is a no-arg job that opens its own DB connection
    and writes the new row itself. We then open a fresh connection to read
    the row back and POST to Discord.
    """
    try:
        await jobs.generate_briefing()
        url = webhook_url()
        if not url:
            return
        async with get_db(DB_PATH) as db:
            async with httpx.AsyncClient() as client:
                await jobs.post_latest_briefing_to_discord(db, client, url)
    except Exception:
        log.exception("scheduled briefing failed")


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


async def run():
    await ensure_db()

    scheduler = AsyncIOScheduler(timezone=CT)
    demo_mode = bool(os.environ.get("KOTORI_SEED_MOCK"))

    # Live jobs — registered unconditionally; safe against no-data.
    # 08:00 CT — pre-market gap monitor (consumes ic_positions.expected_move)
    scheduler.add_job(
        jobs.gap_monitor, CronTrigger(hour=8, minute=0, timezone=CT), id="gap_monitor"
    )
    # 07:00 CT — daily briefing (uses Anthropic if key set, static fallback otherwise).
    # Wrapper posts the generated briefing to Discord when DISCORD_WEBHOOK_URL is set.
    scheduler.add_job(
        _scheduled_briefing,
        CronTrigger(hour=7, minute=0, timezone=CT),
        id="generate_briefing",
    )
    # Every 30s — position monitor (consumes ic_positions.current_debit)
    scheduler.add_job(jobs.position_monitor, "interval", seconds=30, id="position_monitor")

    # Every 30s — Discord notifier (no-ops when DISCORD_WEBHOOK_URL is unset)
    if webhook_url():
        scheduler.add_job(
            _scheduled_notify_alerts,
            "interval",
            seconds=30,
            id="notify_alerts",
        )
        log.info("notify_alerts: Discord webhook configured, notifications enabled")

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

    # Live API jobs — only when TRADIER_API_KEY is set.
    if TRADIER_API_KEY:
        # Every 60s — Tradier position sync (broker -> positions table)
        scheduler.add_job(
            _scheduled_position_sync,
            "interval",
            seconds=60,
            id="position_sync",
        )
        # Every 60s — IC state refresh (live quotes -> current_debit, pct_max_profit)
        scheduler.add_job(
            _scheduled_ic_refresh,
            "interval",
            seconds=60,
            id="ic_refresh",
        )
        # 14:30 CT — daily IC candidate scan against the watchlist
        scheduler.add_job(
            _scheduled_candidate_scan,
            CronTrigger(hour=14, minute=30, timezone=CT),
            id="candidate_scan",
        )
        # 14:50 CT — fallback executor for any approved-but-not-yet-placed candidates.
        # The TUI's 'a' keystroke places immediately; this catches off-hours approvals.
        scheduler.add_job(
            jobs.order_executor,
            CronTrigger(hour=14, minute=50, timezone=CT),
            id="order_executor",
        )

    # Demo jobs — stubbed implementations that generate fake IV / candidates.
    # Only registered when KOTORI_SEED_MOCK=1 so they don't pollute live runs.
    # TODO: replace with real Polygon historical IV + real agent pipeline.
    if demo_mode:
        log.info("KOTORI_SEED_MOCK set — registering demo-mode stub jobs")
        # 05:30 CT — morning IV ingest (random.gauss stub; needs Polygon)
        scheduler.add_job(
            jobs.iv_ingest_morning,
            CronTrigger(hour=5, minute=30, timezone=CT),
            id="iv_ingest_morning",
        )
        # 14:15 CT — pre-close IV refresh (random.gauss stub; needs Tradier chains)
        scheduler.add_job(
            jobs.iv_ingest_preclose,
            CronTrigger(hour=14, minute=15, timezone=CT),
            id="iv_ingest_preclose",
        )
        # 14:30 CT — legacy IC scan stub (hardcoded JSON; superseded by
        # _scheduled_candidate_scan in live mode but kept for demo runs).
        scheduler.add_job(
            jobs.ic_scan, CronTrigger(hour=14, minute=30, timezone=CT), id="ic_scan"
        )

    scheduler.start()
    log.info(
        "kotorid running (TRADIER_ENV=%s)",
        __import__("kotorid.config", fromlist=["TRADIER_ENV"]).TRADIER_ENV,
    )

    loop = asyncio.get_event_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    scheduler.shutdown()
    log.info("kotorid stopped")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
