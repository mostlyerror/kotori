import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from kotorid.config import DB_PATH, TRADIER_API_KEY
from kotorid.db import get_db, init_db
from kotorid.mock_data import seed_mock_data
from kotorid.position_sync import sync_positions
from kotorid.tradier_client import build_client, get_account_id

log = logging.getLogger(__name__)


async def ensure_db():
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
        else:
            await seed_mock_data(db)


def main():
    asyncio.run(ensure_db())
    from kotori_tui.app import KotoriApp
    KotoriApp().run()


if __name__ == "__main__":
    main()
