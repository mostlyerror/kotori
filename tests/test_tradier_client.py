"""Tests for the Tradier API client.

We mock httpx using its built-in MockTransport so no real network calls
are made.
"""
import json

import httpx
import pytest

from kotorid import tradier_client
from kotorid.tradier_client import (
    _as_list,
    build_client,
    get_account_id,
    get_positions,
    get_quotes,
    parse_occ_symbol,
)


# ---------- parse_occ_symbol ----------

def test_parse_occ_symbol_call():
    parsed = parse_occ_symbol("NVDA250516C00880000")
    assert parsed == {
        "underlying": "NVDA",
        "expiry": "2025-05-16",
        "strike": 880.0,
        "put_call": "C",
    }


def test_parse_occ_symbol_put_with_decimal_strike():
    parsed = parse_occ_symbol("SPY250620P00447500")
    assert parsed == {
        "underlying": "SPY",
        "expiry": "2025-06-20",
        "strike": 447.5,
        "put_call": "P",
    }


def test_parse_occ_symbol_stock_returns_none():
    assert parse_occ_symbol("NVDA") is None
    assert parse_occ_symbol("AAPL") is None


def test_parse_occ_symbol_non_string_returns_none():
    assert parse_occ_symbol(None) is None
    assert parse_occ_symbol(123) is None


def test_parse_occ_symbol_garbage_returns_none():
    assert parse_occ_symbol("NOTANOPTION") is None
    assert parse_occ_symbol("NVDA250516X00880000") is None  # bad pc letter


# ---------- _as_list defensive parser ----------

def test_as_list_none():
    assert _as_list(None) == []


def test_as_list_dict():
    assert _as_list({"a": 1}) == [{"a": 1}]


def test_as_list_list():
    assert _as_list([1, 2]) == [1, 2]


# ---------- get_account_id ----------

def _client_with_handler(handler):
    transport = httpx.MockTransport(handler)
    return build_client(
        base_url="https://sandbox.tradier.com/v1",
        api_key="testkey",
        transport=transport,
    )


@pytest.mark.asyncio
async def test_get_account_id_uses_env_var(monkeypatch):
    monkeypatch.setattr(tradier_client, "TRADIER_ACCOUNT_ID", "ENV123")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not hit network when env var is set")

    async with _client_with_handler(handler) as client:
        account_id = await get_account_id(client)
    assert account_id == "ENV123"


@pytest.mark.asyncio
async def test_get_account_id_fetches_profile_when_env_blank(monkeypatch):
    monkeypatch.setattr(tradier_client, "TRADIER_ACCOUNT_ID", "")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/user/profile"
        assert request.headers["Authorization"] == "Bearer testkey"
        assert request.headers["Accept"] == "application/json"
        body = {
            "profile": {
                "id": "id-x",
                "name": "Test",
                "account": [
                    {"account_number": "ACCT-A", "type": "margin"},
                    {"account_number": "ACCT-B", "type": "cash"},
                ],
            }
        }
        return httpx.Response(200, json=body)

    async with _client_with_handler(handler) as client:
        account_id = await get_account_id(client)
    assert account_id == "ACCT-A"


@pytest.mark.asyncio
async def test_get_account_id_handles_single_account_dict(monkeypatch):
    monkeypatch.setattr(tradier_client, "TRADIER_ACCOUNT_ID", "")

    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "profile": {
                "account": {"account_number": "SOLO", "type": "margin"},
            }
        }
        return httpx.Response(200, json=body)

    async with _client_with_handler(handler) as client:
        account_id = await get_account_id(client)
    assert account_id == "SOLO"


# ---------- get_positions ----------

@pytest.mark.asyncio
async def test_get_positions_null_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/accounts/ACCT/positions"
        return httpx.Response(200, json={"positions": "null"})

    async with _client_with_handler(handler) as client:
        result = await get_positions(client, "ACCT")
    assert result == []


@pytest.mark.asyncio
async def test_get_positions_single_dict_wrapped_as_list():
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "positions": {
                "position": {
                    "cost_basis": 84210.0,
                    "date_acquired": "2025-01-01T00:00:00",
                    "id": 1,
                    "quantity": 100,
                    "symbol": "NVDA",
                }
            }
        }
        return httpx.Response(200, json=body)

    async with _client_with_handler(handler) as client:
        result = await get_positions(client, "ACCT")
    assert len(result) == 1
    assert result[0]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_get_positions_list_passthrough():
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "positions": {
                "position": [
                    {"symbol": "NVDA", "quantity": 100, "cost_basis": 84210.0},
                    {"symbol": "META", "quantity": 50, "cost_basis": 29160.0},
                ]
            }
        }
        return httpx.Response(200, json=body)

    async with _client_with_handler(handler) as client:
        result = await get_positions(client, "ACCT")
    assert [p["symbol"] for p in result] == ["NVDA", "META"]


# ---------- get_quotes ----------

@pytest.mark.asyncio
async def test_get_quotes_empty_symbols_no_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call network for empty symbol list")

    async with _client_with_handler(handler) as client:
        result = await get_quotes(client, [])
    assert result == {}


@pytest.mark.asyncio
async def test_get_quotes_null_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"quotes": "null"})

    async with _client_with_handler(handler) as client:
        result = await get_quotes(client, ["FOO"])
    assert result == {}


@pytest.mark.asyncio
async def test_get_quotes_single_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/markets/quotes"
        assert request.url.params["symbols"] == "NVDA"
        body = {
            "quotes": {
                "quote": {"symbol": "NVDA", "last": 869.42, "bid": 869.0, "ask": 870.0}
            }
        }
        return httpx.Response(200, json=body)

    async with _client_with_handler(handler) as client:
        result = await get_quotes(client, ["NVDA"])
    assert set(result) == {"NVDA"}
    assert result["NVDA"]["last"] == 869.42


@pytest.mark.asyncio
async def test_get_quotes_list_returns_dict_by_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "quotes": {
                "quote": [
                    {"symbol": "NVDA", "last": 869.42},
                    {"symbol": "META", "last": 572.44},
                ]
            }
        }
        return httpx.Response(200, json=body)

    async with _client_with_handler(handler) as client:
        result = await get_quotes(client, ["NVDA", "META"])
    assert set(result) == {"NVDA", "META"}
    assert result["META"]["last"] == 572.44
