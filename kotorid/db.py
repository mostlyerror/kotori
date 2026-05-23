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


async def _ensure_column(
    db: aiosqlite.Connection, table: str, column: str, ddl: str,
) -> None:
    """ALTER TABLE ADD COLUMN if the column is missing.

    SQLite has no IF NOT EXISTS for ADD COLUMN, so we PRAGMA the table
    info first. Lets us evolve the schema without a full migration system.
    """
    cursor = await db.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in await cursor.fetchall()}
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


async def init_db(db: aiosqlite.Connection) -> None:
    """Initialize the database with schema and pragmas."""
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    schema = SCHEMA_PATH.read_text()
    await db.executescript(schema)
    # Schema migrations for columns added after the initial release.
    await _ensure_column(db, "alerts", "notified_at", "notified_at TEXT")
    await db.commit()
