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
