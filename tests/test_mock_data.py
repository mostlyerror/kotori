import pytest
from kotorid.mock_data import seed_mock_data


@pytest.mark.asyncio
async def test_seed_creates_positions(conn):
    await seed_mock_data(conn)
    count = await conn.fetchval("SELECT COUNT(*) FROM positions")
    assert count == 5


@pytest.mark.asyncio
async def test_seed_creates_open_ic(conn):
    await seed_mock_data(conn)
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM ic_positions WHERE exit_reason IS NULL"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_seed_is_idempotent(conn):
    await seed_mock_data(conn)
    await seed_mock_data(conn)
    count = await conn.fetchval("SELECT COUNT(*) FROM positions")
    assert count == 5


@pytest.mark.asyncio
async def test_seed_creates_inbox_items(conn):
    await seed_mock_data(conn)
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM inbox_items WHERE dismissed_at IS NULL"
    )
    assert count >= 3
