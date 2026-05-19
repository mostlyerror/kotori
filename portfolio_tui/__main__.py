import asyncio
from pathlib import Path
from portfoliod.config import DB_PATH
from portfoliod.db import get_db, init_db
from portfoliod.mock_data import seed_mock_data


async def ensure_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with get_db(DB_PATH) as db:
        await init_db(db)
        await seed_mock_data(db)


def main():
    asyncio.run(ensure_db())
    from portfolio_tui.app import TraderApp
    TraderApp().run()


if __name__ == "__main__":
    main()
