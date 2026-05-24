"""Tests for the order_placement module."""
import json
from datetime import date

import httpx
import pytest

from kotorid.alerts_lib import ALERT_FIELDS_KEY
from kotorid.order_placement import place_approved_candidates, place_iron_condor
from kotorid.tradier_client import build_client


def _make_client(handler):
    transport = httpx.MockTransport(handler)
    return build_client(
        base_url="https://sandbox.tradier.com/v1",
        api_key="testkey",
        transport=transport,
    )


def _ok_order_handler(captured: dict):
    """Capture the POST body for assertion, return Tradier success response."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/orders" in request.url.path:
            captured["body"] = dict(
                (k.decode() if isinstance(k, bytes) else k,
                 v.decode() if isinstance(v, bytes) else v)
                for k, v in [
                    pair.split(b"=", 1) for pair in request.content.split(b"&")
                ]
            )
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"order": {"id": 999, "status": "ok"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return handler


@pytest.mark.asyncio
async def test_place_iron_condor_posts_correct_multileg_payload():
    """Verify the indexed multileg params Tradier expects."""
    captured: dict = {}
    async with _make_client(_ok_order_handler(captured)) as c:
        resp = await place_iron_condor(
            c, account_id="ACCT-X",
            underlying="SPY", expiry="2026-05-29",
            short_call=760, long_call=765, short_put=735, long_put=730,
            contracts=1,
        )
    assert resp == {"order": {"id": 999, "status": "ok"}}
    body = captured["body"]
    # URL-decoded form-encoded values: '%5B' = '[', '%5D' = ']'
    assert body["class"] == "multileg"
    assert body["symbol"] == "SPY"
    assert body["type"] == "market"
    assert body["duration"] == "day"
    # 4 legs, properly indexed
    # Tradier expects: leg 0 = buy_to_open long_put 730
    assert body["side%5B0%5D"] == "buy_to_open"
    assert body["option_symbol%5B0%5D"] == "SPY260529P00730000"
    # leg 1 = sell_to_open short_put 735
    assert body["side%5B1%5D"] == "sell_to_open"
    assert body["option_symbol%5B1%5D"] == "SPY260529P00735000"
    # leg 2 = sell_to_open short_call 760
    assert body["side%5B2%5D"] == "sell_to_open"
    assert body["option_symbol%5B2%5D"] == "SPY260529C00760000"
    # leg 3 = buy_to_open long_call 765
    assert body["side%5B3%5D"] == "buy_to_open"
    assert body["option_symbol%5B3%5D"] == "SPY260529C00765000"


async def _seed_one_approved_candidate(conn, symbol="SPY", expiry="2026-05-29"):
    """Helper: insert agent_runs row + candidate in 'approved' state +
    a pending inbox card for the symbol."""
    ar_id = await conn.fetchval(
        """INSERT INTO agent_runs
           (symbol, earnings_date, scanner_output, strategist_output,
            risk_manager_output, devils_advocate_output, portfolio_manager_output,
            final_decision, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
           RETURNING id""",
        symbol, date.today().isoformat(),
        json.dumps({"expiry": expiry, "spot": 747.5}),
        "{}", "{}", "{}", "{}", "pending",
        "2026-05-22T00:00:00",
    )
    cand_id = await conn.fetchval(
        """INSERT INTO candidates
           (agent_run_id, symbol, scan_date, order_status,
            short_call, long_call, short_put, long_put,
            expected_credit, contracts, max_loss)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           RETURNING id""",
        ar_id, symbol, date.today().isoformat(), "approved",
        760.0, 765.0, 735.0, 730.0, 1.00, 1, 400.0,
    )
    await conn.execute(
        """INSERT INTO inbox_items
           (priority, item_type, symbol, title, body, actions, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7)""",
        "action_required", "ic_candidate", symbol,
        f"{symbol} IC Candidate", "test body", '["approve","reject"]',
        "2026-05-22T00:00:00",
    )
    return cand_id


@pytest.mark.asyncio
async def test_place_approved_candidates_happy_path(conn):
    """Approved candidate gets placed, transitioned to 'placed', writes
    ic_positions, dismisses the inbox card."""
    captured: dict = {}
    cand_id = await _seed_one_approved_candidate(conn)
    async with _make_client(_ok_order_handler(captured)) as c:
        placed = await place_approved_candidates(conn, c, "ACCT-X")
    assert len(placed) == 1
    assert placed[0]["symbol"] == "SPY"
    assert placed[0]["order_id"] == 999

    # Candidate transitioned to 'placed'
    row = await conn.fetchrow(
        "SELECT order_status FROM candidates WHERE id=$1", cand_id,
    )
    assert row["order_status"] == "placed"

    # ic_positions row materialized with correct fields
    ic = await conn.fetchrow(
        "SELECT symbol, expiry, short_call, long_call, short_put, long_put, "
        "spread_width, entry_credit, contracts, max_loss FROM ic_positions"
    )
    assert ic["symbol"] == "SPY"
    assert ic["expiry"] == "2026-05-29"
    assert ic["short_call"] == 760.0
    assert ic["long_call"] == 765.0
    assert ic["spread_width"] == 5.0
    assert ic["entry_credit"] == pytest.approx(1.00)
    assert ic["max_loss"] == pytest.approx(400.0)

    # Inbox card dismissed
    inbox = await conn.fetchrow(
        "SELECT dismissed_at FROM inbox_items WHERE symbol='SPY'"
    )
    assert inbox["dismissed_at"] is not None

    # ic_placed alert created
    alert = await conn.fetchrow(
        "SELECT alert_type, message FROM alerts WHERE symbol='SPY'"
    )
    assert alert["alert_type"] == "ic_placed"
    assert "999" in alert["message"]


@pytest.mark.asyncio
async def test_place_approved_candidates_skips_when_expiry_missing(conn):
    """If scanner_output JSON has no expiry, skip the candidate (don't crash)."""
    def handler(request):
        raise AssertionError("should not call Tradier when expiry is missing")

    ar_id = await conn.fetchval(
        """INSERT INTO agent_runs
           (symbol, earnings_date, scanner_output, strategist_output,
            risk_manager_output, devils_advocate_output, portfolio_manager_output,
            final_decision, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
           RETURNING id""",
        "SPY", date.today().isoformat(),
        json.dumps({}),  # no expiry!
        "{}", "{}", "{}", "{}", "pending", "2026-05-22T00:00:00",
    )
    await conn.execute(
        """INSERT INTO candidates
           (agent_run_id, symbol, scan_date, order_status,
            short_call, long_call, short_put, long_put,
            expected_credit, contracts, max_loss)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
        ar_id, "SPY", date.today().isoformat(), "approved",
        760.0, 765.0, 735.0, 730.0, 1.00, 1, 400.0,
    )
    async with _make_client(handler) as c:
        placed = await place_approved_candidates(conn, c, "ACCT-X")
    assert placed == []


@pytest.mark.asyncio
async def test_place_approved_candidates_retains_status_on_api_failure(conn):
    """If Tradier returns an error, candidate stays 'approved' (don't transition
    to 'placed' or insert ic_positions). Cron retries on next run."""
    def handler(request):
        return httpx.Response(400, json={"errors": {"error": "bad request"}})

    cand_id = await _seed_one_approved_candidate(conn)
    async with _make_client(handler) as c:
        placed = await place_approved_candidates(conn, c, "ACCT-X")
    assert placed == []

    # Candidate stays 'approved' for retry
    row = await conn.fetchrow(
        "SELECT order_status FROM candidates WHERE id=$1", cand_id,
    )
    assert row["order_status"] == "approved"

    # No ic_positions row inserted
    ic_count = await conn.fetchval(
        "SELECT COUNT(*) FROM ic_positions"
    )
    assert ic_count == 0


@pytest.mark.asyncio
async def test_place_approved_candidates_noop_when_no_approved_rows(conn):
    def handler(request):
        raise AssertionError("should not call Tradier when no approved rows")

    async with _make_client(handler) as c:
        placed = await place_approved_candidates(conn, c, "ACCT-X")
    assert placed == []


@pytest.mark.asyncio
async def test_ic_placed_alert_is_structured(conn):
    """ic_placed alert should carry structured body_lines + fields with
    order_id, expiry, expected_credit, max_loss."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/orders" in request.url.path:
            return httpx.Response(200, json={"order": {"id": 12345, "status": "ok"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    await _seed_one_approved_candidate(conn, symbol="SPY", expiry="2026-05-29")
    async with _make_client(handler) as c:
        placed = await place_approved_candidates(conn, c, "ACCT-X")
    assert len(placed) == 1

    alert = await conn.fetchrow(
        "SELECT message FROM alerts WHERE alert_type='ic_placed' AND symbol='SPY'"
    )

    assert alert is not None
    assert ALERT_FIELDS_KEY in alert["message"]
    _, _, json_tail = alert["message"].partition(ALERT_FIELDS_KEY)
    payload = json.loads(json_tail)
    fields = payload["fields"]
    assert fields["order_id"] == "12345"
    assert fields["expiry"] == "2026-05-29"
    assert fields["expected_credit"] == pytest.approx(1.00)
    assert fields["max_loss"] == pytest.approx(400.0)


@pytest.mark.asyncio
async def test_materialize_ic_position_stores_order_id(conn):
    """When Tradier returns order id 12345, ic_positions.order_id == '12345'."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/orders" in request.url.path:
            return httpx.Response(200, json={"order": {"id": 12345, "status": "ok"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    await _seed_one_approved_candidate(conn, symbol="SPY", expiry="2026-05-29")
    async with _make_client(handler) as c:
        placed = await place_approved_candidates(conn, c, "ACCT-X")
    assert len(placed) == 1

    ic = await conn.fetchrow(
        "SELECT order_id FROM ic_positions WHERE symbol='SPY'"
    )
    assert ic is not None
    assert ic["order_id"] == "12345"
