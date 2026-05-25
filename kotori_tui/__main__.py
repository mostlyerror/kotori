import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

import asyncpg
from kotorid.config import DATABASE_URL
from kotorid.db import init_db

log = logging.getLogger(__name__)


async def ensure_schema():
    """Init schema using a one-shot connection (not a pool).

    The TUI's db module creates its own pool lazily inside Textual's
    event loop — we can't share a pool across asyncio.run() boundaries.
    """
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await init_db(conn)
    finally:
        await conn.close()


def main():
    asyncio.run(ensure_schema())
    from kotori_tui.app import KotoriApp
    KotoriApp().run()


if __name__ == "__main__":
    main()
