"""Tests for the Tradier -> SQLite position sync."""
import httpx
import pytest

from kotorid.db import get_db, init_db
from kotorid.position_sync import sync_positions
from kotorid.tradier_client import build_client


def _make_client(handler):
    transport = httpx.MockTransport(handler)
    return build_client(
        base_url="https://sandbox.tradier.com/v1",
        api_key="testkey",
        transport=transport,
    )


def _handler_for(positions_payload, quotes_payload):
    """Build a MockTransport handler returning fixed positions + quotes."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/positions"):
            return httpx.Response(200, json=positions_payload)
        if path.endswith("/markets/quotes"):
            return httpx.Response(200, json=quotes_payload)
        raise AssertionError(f"unexpected request: {path}")

    return handler


@pytest.mark.asyncio
async def test_sync_positions_writes_rows(tmp_path):
    positions_payload = {
        "positions": {
            "position": [
                {
                    "symbol": "NVDA",
                    "quantity": 100,
                    "cost_basis": 84210.0,
                    "date_acquired": "2025-01-01T00:00:00",
                    "id": 1,
                },
                {
                    "symbol": "META",
                    "quantity": 50,
                    "cost_basis": 29160.0,
                    "date_acquired": "2025-01-02T00:00:00",
                    "id": 2,
                },
            ]
        }
    }
    quotes_payload = {
        "quotes": {
            "quote": [
                {"symbol": "META", "last": 572.44, "bid": 572.0, "ask": 573.0},
                {"symbol": "NVDA", "last": 869.42, "bid": 869.0, "ask": 870.0},
            ]
        }
    }

    db_path = str(tmp_path / "sync.db")
    async with get_db(db_path) as db:
        await init_db(db)
        async with _make_client(_handler_for(positions_payload, quotes_payload)) as c:
            count = await sync_positions(db, c, "ACCT-X")
        assert count == 2

        cursor = await db.execute(
            "SELECT symbol, quantity, avg_cost, current_price, market_value, "
            "unrealized_pnl, unrealized_pnl_pct, instrument_type "
            "FROM positions ORDER BY symbol"
        )
        rows = await cursor.fetchall()

    by_symbol = {r["symbol"]: r for r in rows}
    assert set(by_symbol) == {"NVDA", "META"}

    nvda = by_symbol["NVDA"]
    assert nvda["quantity"] == 100.0
    assert nvda["avg_cost"] == pytest.approx(842.10)
    assert nvda["current_price"] == pytest.approx(869.42)
    assert nvda["market_value"] == pytest.approx(86942.0)
    assert nvda["unrealized_pnl"] == pytest.approx(86942.0 - 84210.0)
    assert nvda["unrealized_pnl_pct"] == pytest.approx(
        (86942.0 - 84210.0) / 84210.0
    )
    assert nvda["instrument_type"] == "stock"

    meta = by_symbol["META"]
    assert meta["instrument_type"] == "stock"
    assert meta["avg_cost"] == pytest.approx(29160.0 / 50)


@pytest.mark.asyncio
async def test_sync_positions_parses_occ_option(tmp_path):
    positions_payload = {
        "positions": {
            "position": {
                "symbol": "NVDA250516C00880000",
                "quantity": -5,
                "cost_basis": -1050.0,
                "date_acquired": "2025-04-01T00:00:00",
                "id": 7,
            }
        }
    }
    quotes_payload = {
        "quotes": {
            "quote": {
                "symbol": "NVDA250516C00880000",
                "last": 4.35,
                "bid": 4.30,
                "ask": 4.40,
                "contract_size": 100,
            }
        }
    }

    db_path = str(tmp_path / "sync_opt.db")
    async with get_db(db_path) as db:
        await init_db(db)
        async with _make_client(_handler_for(positions_payload, quotes_payload)) as c:
            count = await sync_positions(db, c, "ACCT-X")
        assert count == 1

        cursor = await db.execute(
            "SELECT symbol, instrument_type, underlying, expiry, strike, "
            "put_call, quantity, avg_cost, market_value, unrealized_pnl "
            "FROM positions"
        )
        row = await cursor.fetchone()

    assert row["symbol"] == "NVDA250516C00880000"
    assert row["instrument_type"] == "option"
    assert row["underlying"] == "NVDA"
    assert row["expiry"] == "2025-05-16"
    assert row["strike"] == pytest.approx(880.0)
    assert row["put_call"] == "C"
    assert row["quantity"] == -5.0
    # Premium collected = $1050 on 5 short contracts = $2.10/share credit.
    assert row["avg_cost"] == pytest.approx(2.10)
    # market_value = -5 * 4.35 * 100 = -2175.0 (cost to buy back to close)
    assert row["market_value"] == pytest.approx(-2175.0)
    # unrealized_pnl = -2175 - (-1050) = -1125.0 (loss — option moved against the short)
    assert row["unrealized_pnl"] == pytest.approx(-1125.0)


@pytest.mark.asyncio
async def test_sync_positions_replaces_existing_rows(tmp_path):
    first_positions = {
        "positions": {
            "position": [
                {"symbol": "NVDA", "quantity": 100, "cost_basis": 84210.0},
                {"symbol": "META", "quantity": 50, "cost_basis": 29160.0},
            ]
        }
    }
    first_quotes = {
        "quotes": {
            "quote": [
                {"symbol": "NVDA", "last": 869.42},
                {"symbol": "META", "last": 572.44},
            ]
        }
    }
    second_positions = {
        "positions": {
            "position": {"symbol": "AAPL", "quantity": 10, "cost_basis": 1900.0}
        }
    }
    second_quotes = {
        "quotes": {"quote": {"symbol": "AAPL", "last": 195.0}}
    }

    db_path = str(tmp_path / "sync_replace.db")
    async with get_db(db_path) as db:
        await init_db(db)
        async with _make_client(_handler_for(first_positions, first_quotes)) as c:
            await sync_positions(db, c, "ACCT-X")
        cursor = await db.execute("SELECT symbol FROM positions ORDER BY symbol")
        first_syms = [r["symbol"] for r in await cursor.fetchall()]
        assert first_syms == ["META", "NVDA"]

        async with _make_client(_handler_for(second_positions, second_quotes)) as c:
            count = await sync_positions(db, c, "ACCT-X")
        assert count == 1
        cursor = await db.execute("SELECT symbol FROM positions")
        rows = await cursor.fetchall()
        symbols = [r["symbol"] for r in rows]

    assert symbols == ["AAPL"]


@pytest.mark.asyncio
async def test_sync_positions_handles_empty_account(tmp_path):
    positions_payload = {"positions": "null"}
    quotes_payload = {"quotes": "null"}

    db_path = str(tmp_path / "sync_empty.db")
    async with get_db(db_path) as db:
        await init_db(db)
        async with _make_client(_handler_for(positions_payload, quotes_payload)) as c:
            count = await sync_positions(db, c, "ACCT-X")
        assert count == 0
        cursor = await db.execute("SELECT COUNT(*) FROM positions")
        n = (await cursor.fetchone())[0]
    assert n == 0
