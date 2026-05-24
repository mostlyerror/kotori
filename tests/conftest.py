"""Shared test fixtures — Postgres-backed.

Each test gets its own connection with a transaction that rolls back,
so tests never pollute each other.
"""
import os
import asyncio

import asyncpg
import pytest
import pytest_asyncio
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_PUBLIC_URL", ""),
)

_schema_initialized = False


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def _ensure_schema():
    global _schema_initialized
    if not _schema_initialized:
        conn = await asyncpg.connect(DATABASE_URL)
        from kotorid.db import init_db
        await init_db(conn)
        await conn.close()
        _schema_initialized = True


@pytest_asyncio.fixture
async def conn(_ensure_schema):
    """Per-test connection with a transaction that rolls back."""
    connection = await asyncpg.connect(DATABASE_URL)
    tr = connection.transaction()
    await tr.start()
    try:
        yield connection
    finally:
        await tr.rollback()
        await connection.close()
