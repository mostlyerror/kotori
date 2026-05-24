import pytest


@pytest.mark.asyncio
async def test_init_db_creates_tables(conn):
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    )
    tables = {row["tablename"] for row in rows}
    assert "positions" in tables
    assert "ic_positions" in tables
    assert "inbox_items" in tables
    assert "briefings" in tables


@pytest.mark.asyncio
async def test_init_db_adds_discord_stream_columns(conn):
    """ic_positions has order_id, position_warning_at, short_strike_warned_at."""
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ic_positions'"
    )
    cols = {row["column_name"] for row in rows}
    assert "order_id" in cols
    assert "position_warning_at" in cols
    assert "short_strike_warned_at" in cols
