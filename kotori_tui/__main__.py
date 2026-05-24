import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from kotorid.config import DATABASE_URL, TRADIER_API_KEY
from kotorid.db import get_db, init_db, create_pool, close_pool
from kotorid.mock_data import seed_mock_data
from kotorid.position_sync import sync_positions
from kotorid.tradier_client import build_client, get_account_id

log = logging.getLogger(__name__)


async def ensure_db():
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


def main():
    asyncio.run(ensure_db())
    from kotori_tui.app import KotoriApp
    KotoriApp().run()


if __name__ == "__main__":
    main()
