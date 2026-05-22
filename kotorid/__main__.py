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

from kotorid.config import DB_PATH, TRADIER_API_KEY
from kotorid.db import get_db, init_db
from kotorid.ic_sync import refresh_ic_state
from kotorid.mock_data import seed_mock_data
from kotorid.position_sync import sync_positions
from kotorid.tradier_client import build_client, get_account_id
from kotorid import jobs

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


async def run():
    await ensure_db()

    scheduler = AsyncIOScheduler(timezone=CT)

    # 05:30 CT — morning IV ingest (Polygon historical)
    scheduler.add_job(
        jobs.iv_ingest_morning,
        CronTrigger(hour=5, minute=30, timezone=CT),
        id="iv_ingest_morning",
    )
    # 08:00 CT — pre-market gap monitor
    scheduler.add_job(
        jobs.gap_monitor, CronTrigger(hour=8, minute=0, timezone=CT), id="gap_monitor"
    )
    # 14:15 CT — pre-close IV refresh (Tradier live chains)
    scheduler.add_job(
        jobs.iv_ingest_preclose,
        CronTrigger(hour=14, minute=15, timezone=CT),
        id="iv_ingest_preclose",
    )
    # 14:30 CT — IC scan + 4-agent pipeline
    scheduler.add_job(
        jobs.ic_scan, CronTrigger(hour=14, minute=30, timezone=CT), id="ic_scan"
    )
    # 14:50 CT — order executor (approved candidates)
    scheduler.add_job(
        jobs.order_executor,
        CronTrigger(hour=14, minute=50, timezone=CT),
        id="order_executor",
    )
    # 07:00 CT — daily briefing
    scheduler.add_job(
        jobs.generate_briefing,
        CronTrigger(hour=7, minute=0, timezone=CT),
        id="generate_briefing",
    )
    # Every 30s — position monitor
    scheduler.add_job(jobs.position_monitor, "interval", seconds=30, id="position_monitor")

    # Every 60s — Tradier position sync + IC state refresh (live API only)
    if TRADIER_API_KEY:
        scheduler.add_job(
            _scheduled_position_sync,
            "interval",
            seconds=60,
            id="position_sync",
        )
        scheduler.add_job(
            _scheduled_ic_refresh,
            "interval",
            seconds=60,
            id="ic_refresh",
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
