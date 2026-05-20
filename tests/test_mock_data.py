import pytest
from kotorid.db import get_db, init_db
from kotorid.mock_data import seed_mock_data


@pytest.mark.asyncio
async def test_seed_creates_positions(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        cursor = await db.execute("SELECT COUNT(*) FROM positions")
        count = (await cursor.fetchone())[0]
    assert count == 5


@pytest.mark.asyncio
async def test_seed_creates_open_ic(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM ic_positions WHERE exit_reason IS NULL"
        )
        count = (await cursor.fetchone())[0]
    assert count == 1


@pytest.mark.asyncio
async def test_seed_is_idempotent(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        await seed_mock_data(db)
        cursor = await db.execute("SELECT COUNT(*) FROM positions")
        count = (await cursor.fetchone())[0]
    assert count == 5


@pytest.mark.asyncio
async def test_seed_creates_inbox_items(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        await seed_mock_data(db)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM inbox_items WHERE dismissed_at IS NULL"
        )
        count = (await cursor.fetchone())[0]
    assert count >= 3
