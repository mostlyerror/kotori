import asyncpg
from kotorid.config import DATABASE_URL
from kotorid.db import _pool, create_pool

_tui_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _tui_pool
    if _pool is not None:
        return _pool
    if _tui_pool is None:
        _tui_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _tui_pool


async def query(sql: str, params: tuple = ()) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(sql, *params)
    return [dict(r) for r in rows]


async def execute(sql: str, params: tuple = ()) -> None:
    pool = await _get_pool()
    await pool.execute(sql, *params)


async def get_nav() -> float:
    rows = await query("SELECT SUM(market_value) as nav FROM positions")
    return rows[0]["nav"] or 0.0 if rows else 0.0


async def get_today_pnl() -> float:
    rows = await query("SELECT SUM(unrealized_pnl) as pnl FROM positions")
    return rows[0]["pnl"] or 0.0 if rows else 0.0


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


async def get_open_ics() -> list[dict]:
    return await query(
        """SELECT id, symbol, entry_date, expiry, short_call, long_call,
                  short_put, long_put, entry_credit, current_debit,
                  pct_max_profit, contracts, max_loss
           FROM ic_positions
           WHERE exit_reason IS NULL
           ORDER BY expiry, symbol"""
    )


async def get_closed_ics() -> list[dict]:
    return await query(
        """SELECT id, symbol, entry_date, expiry, exit_reason, exit_debit,
                  entry_credit, realized_pnl, contracts, max_loss
           FROM ic_positions
           WHERE exit_reason IS NOT NULL
           ORDER BY expiry DESC, symbol"""
    )


async def get_strategy_stats() -> dict:
    rows = await query(
        """SELECT
              SUM(CASE WHEN exit_reason IS NULL THEN 1 ELSE 0 END) AS open_count,
              SUM(CASE WHEN exit_reason IS NOT NULL THEN 1 ELSE 0 END) AS closed_count,
              SUM(CASE WHEN exit_reason IS NOT NULL AND realized_pnl IS NOT NULL
                       THEN realized_pnl ELSE 0 END) AS total_realized_pnl,
              SUM(CASE WHEN exit_reason IS NOT NULL AND realized_pnl IS NOT NULL
                       THEN 1 ELSE 0 END) AS pnl_known_count,
              SUM(CASE WHEN exit_reason IS NOT NULL AND realized_pnl > 0
                       THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN exit_reason IS NULL AND current_debit IS NOT NULL
                       THEN (entry_credit - current_debit) * 100 * contracts
                       ELSE 0 END) AS unrealized_pnl
           FROM ic_positions"""
    )
    r = rows[0] if rows else {}
    open_count = r.get("open_count") or 0
    closed_count = r.get("closed_count") or 0
    pnl_known = r.get("pnl_known_count") or 0
    wins = r.get("wins") or 0
    return {
        "open_count": open_count,
        "closed_count": closed_count,
        "total_realized_pnl": float(r.get("total_realized_pnl") or 0),
        "unrealized_pnl": float(r.get("unrealized_pnl") or 0),
        "win_rate": (wins / pnl_known) if pnl_known else None,
        "pnl_known_count": pnl_known,
    }
