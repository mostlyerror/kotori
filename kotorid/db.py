from contextlib import asynccontextmanager
from pathlib import Path
import aiosqlite

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@asynccontextmanager
async def get_db(path: str):
    """Context manager for async SQLite database connections."""
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_db(db: aiosqlite.Connection) -> None:
    """Initialize the database with schema and pragmas."""
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    schema = SCHEMA_PATH.read_text()
    await db.executescript(schema)
    await db.commit()
