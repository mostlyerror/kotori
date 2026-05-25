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

from kotorid.candidate_scan import scan_candidates, get_watchlist
from kotorid.earnings import refresh_earnings
from kotorid.config import DATABASE_URL, TRADIER_API_KEY
from kotorid.db import get_db, init_db, create_pool, close_pool
from kotorid.market_calendar import is_market_open
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
    """Initialize DB pool and schema; sync real positions when credentials are set."""
    await create_pool(DATABASE_URL)
    async with get_db() as conn:
        await init_db(conn)
        if TRADIER_API_KEY:
            try:
                async with build_client() as client:
                    account_id = await get_account_id(client)
                    count = await sync_positions(conn, client, account_id)
                log.info(
                    "ensure_db: synced %d positions from Tradier (account=%s)",
                    count,
                    account_id,
                )
            except Exception:
                log.exception("ensure_db: Tradier sync failed; continuing")
        elif os.environ.get("KOTORI_SEED_MOCK"):
            await seed_mock_data(conn)
            log.info("ensure_db: KOTORI_SEED_MOCK set — seeded mock data")
        else:
            log.info(
                "ensure_db: TRADIER_API_KEY not set and KOTORI_SEED_MOCK not set "
                "— starting empty (set KOTORI_SEED_MOCK=1 to populate demo data)"
            )
        try:
            await refresh_earnings(conn, get_watchlist())
        except Exception:
            log.exception("ensure_db: earnings refresh failed; continuing")
        log.info("DB ready (Postgres)")


async def _scheduled_position_sync():
    if not is_market_open():
        return
    try:
        async with get_db() as conn:
            async with build_client() as client:
                account_id = await get_account_id(client)
                await sync_positions(conn, client, account_id)
    except Exception:
        log.exception("scheduled position_sync failed")


async def _scheduled_ic_refresh():
    if not is_market_open():
        return
    try:
        async with get_db() as conn:
            async with build_client() as client:
                await refresh_ic_state(conn, client)
    except Exception:
        log.exception("scheduled ic_refresh failed")


async def _scheduled_earnings_refresh():
    try:
        async with get_db() as conn:
            await refresh_earnings(conn, get_watchlist())
    except Exception:
        log.exception("scheduled earnings_refresh failed")


async def _scheduled_candidate_scan():
    if not is_market_open():
        log.debug("candidate_scan: market closed, skipping")
        return
    try:
        async with get_db() as conn:
            async with build_client() as client:
                await scan_candidates(conn, client)
    except Exception:
        log.exception("scheduled candidate_scan failed")


async def _scheduled_notify_alerts():
    url = webhook_url()
    if not url:
        return
    try:
        async with get_db() as conn:
            async with httpx.AsyncClient() as client:
                await notify_pending_alerts(conn, client, url)
    except Exception:
        log.exception("scheduled notify_alerts failed")


async def _scheduled_briefing():
    try:
        await jobs.generate_briefing()
        url = webhook_url()
        if not url:
            return
        async with get_db() as conn:
            async with httpx.AsyncClient() as client:
                await jobs.post_latest_briefing_to_discord(conn, client, url)
    except Exception:
        log.exception("scheduled briefing failed")


async def _scheduled_heartbeat():
    if not is_market_open():
        log.debug("heartbeat: market closed, skipping")
        return
    from kotorid.heartbeat import build_heartbeat_line, post_heartbeat
    from datetime import datetime
    url = webhook_url()
    if not url:
        return
    try:
        async with get_db() as conn:
            async with httpx.AsyncClient() as client:
                now_ct = datetime.now(tz=CT).strftime("%H:%M CT")
                line = await build_heartbeat_line(conn, now_ct_label=now_ct)
                await post_heartbeat(client, url, line)
    except Exception:
        log.exception("scheduled heartbeat failed")


async def _scheduled_dte_check():
    try:
        async with get_db() as conn:
            await jobs.dte_check(conn)
    except Exception:
        log.exception("scheduled dte_check failed")


async def _scheduled_order_status_check():
    if not TRADIER_API_KEY:
        return
    try:
        async with get_db() as conn:
            async with build_client() as client:
                account_id = await get_account_id(client)
                from kotorid.order_status import check_open_orders
                await check_open_orders(conn, client, account_id)
    except Exception:
        log.exception("scheduled order_status_check failed")


async def run():
    await ensure_db()

    scheduler = AsyncIOScheduler(timezone=CT)
    demo_mode = bool(os.environ.get("KOTORI_SEED_MOCK"))

    scheduler.add_job(
        jobs.gap_monitor, CronTrigger(hour=8, minute=0, timezone=CT), id="gap_monitor"
    )
    scheduler.add_job(
        _scheduled_earnings_refresh,
        CronTrigger(day_of_week="mon-fri", hour=6, minute=0, timezone=CT),
        id="earnings_refresh",
    )
    scheduler.add_job(
        _scheduled_briefing,
        CronTrigger(hour=7, minute=0, timezone=CT),
        id="generate_briefing",
    )
    scheduler.add_job(
        _scheduled_dte_check,
        CronTrigger(hour=9, minute=0, timezone=CT),
        id="dte_check",
    )
    scheduler.add_job(
        jobs.eod_recap_job,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=CT),
        id="eod_recap",
    )
    scheduler.add_job(jobs.position_monitor, "interval", seconds=30, id="position_monitor")

    if webhook_url():
        scheduler.add_job(
            _scheduled_notify_alerts,
            "interval",
            seconds=30,
            id="notify_alerts",
        )
        log.info("notify_alerts: Discord webhook configured, notifications enabled")

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
        scheduler.add_job(
            _scheduled_order_status_check,
            "interval",
            seconds=90,
            id="order_status_check",
        )
        scheduler.add_job(
            _scheduled_candidate_scan,
            CronTrigger(hour=14, minute=30, timezone=CT),
            id="candidate_scan",
        )
        scheduler.add_job(
            jobs.order_executor,
            CronTrigger(hour=14, minute=50, timezone=CT),
            id="order_executor",
        )

    if demo_mode:
        log.info("KOTORI_SEED_MOCK set — registering demo-mode stub jobs")
        scheduler.add_job(
            jobs.iv_ingest_morning,
            CronTrigger(hour=5, minute=30, timezone=CT),
            id="iv_ingest_morning",
        )
        scheduler.add_job(
            jobs.iv_ingest_preclose,
            CronTrigger(hour=14, minute=15, timezone=CT),
            id="iv_ingest_preclose",
        )
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
    await close_pool()
    log.info("kotorid stopped")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
