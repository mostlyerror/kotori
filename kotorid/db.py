from contextlib import asynccontextmanager
from pathlib import Path
import asyncpg

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_pool: asyncpg.Pool | None = None


async def create_pool(dsn: str) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_db(dsn: str | None = None):
    """Acquire a connection from the pool.

    The dsn parameter is accepted for backward compatibility but ignored
    when a pool exists.
    """
    if _pool is None:
        if dsn is None:
            raise RuntimeError("DB pool not initialized — call create_pool() first")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        async with pool.acquire() as conn:
            try:
                yield conn
            finally:
                await pool.close()
        return
    async with _pool.acquire() as conn:
        yield conn


async def init_db(conn: asyncpg.Connection) -> None:
    schema = SCHEMA_PATH.read_text()
    for statement in _split_statements(schema):
        await conn.execute(statement)


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements."""
    statements = []
    current = []
    for line in sql.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []
    if current:
        statements.append("\n".join(current))
    return [s for s in statements if s.strip()]
