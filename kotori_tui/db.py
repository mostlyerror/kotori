from pathlib import Path
import aiosqlite
from kotorid.config import DB_PATH


async def query(sql: str, params: tuple = ()) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def execute(sql: str, params: tuple = ()) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, params)
        await db.commit()


async def get_nav() -> float:
    rows = await query("SELECT SUM(market_value) as nav FROM positions")
    return rows[0]["nav"] or 0.0


async def get_today_pnl() -> float:
    rows = await query("SELECT SUM(unrealized_pnl) as pnl FROM positions")
    return rows[0]["pnl"] or 0.0


async def get_vix() -> float:
    rows = await query(
        "SELECT vix FROM regime_snapshots ORDER BY timestamp DESC LIMIT 1"
    )
    return rows[0]["vix"] if rows else 0.0


async def get_market_regime() -> str:
    rows = await query(
        "SELECT market_regime FROM regime_snapshots ORDER BY timestamp DESC LIMIT 1"
    )
    return rows[0]["market_regime"] if rows else "unknown"


async def get_unread_alert_count() -> int:
    rows = await query("SELECT COUNT(*) as n FROM alerts WHERE acknowledged=0")
    return rows[0]["n"]


async def get_inbox_count() -> int:
    rows = await query("SELECT COUNT(*) as n FROM inbox_items WHERE dismissed_at IS NULL")
    return rows[0]["n"]
