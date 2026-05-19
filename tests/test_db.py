import pytest
import aiosqlite
from portfoliod.db import get_db, init_db


@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}
    assert "positions" in tables
    assert "ic_positions" in tables
    assert "inbox_items" in tables
    assert "briefings" in tables


@pytest.mark.asyncio
async def test_get_db_enables_wal(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with get_db(db_path) as db:
        await init_db(db)
        cursor = await db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
    assert row[0] == "wal"
