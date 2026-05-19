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

    scheduler.start()
    log.info(
        "portfoliod running (TRADIER_ENV=%s)",
        __import__("portfoliod.config", fromlist=["TRADIER_ENV"]).TRADIER_ENV,
    )

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
