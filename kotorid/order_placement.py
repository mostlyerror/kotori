"""Place iron-condor orders at Tradier and materialize ic_positions rows.

Two entry points:

- ``place_iron_condor`` — pure broker call. POSTs a 4-leg multileg
  market-day order. Returns Tradier's order response.
- ``place_approved_candidates`` — orchestration. Reads candidates with
  ``order_status='approved'``, places each, marks them ``'placed'``,
  inserts the matching ``ic_positions`` row so ic_refresh can take over.

The candidate's expiry isn't stored on the ``candidates`` table — it
lives in ``agent_runs.scanner_output`` JSON. We read it back from there
rather than migrating the schema for now.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

import aiosqlite
import httpx

from kotorid.alerts_lib import create_alert
from kotorid.tradier_client import format_occ_symbol

log = logging.getLogger(__name__)


async def place_iron_condor(
    client: httpx.AsyncClient,
    account_id: str,
    underlying: str,
    expiry: str,
    short_call: float,
    long_call: float,
    short_put: float,
    long_put: float,
    contracts: int = 1,
) -> dict:
    """POST a market-day multileg iron-condor order to Tradier.

    Tradier multileg uses indexed params (side[0], quantity[0],
    option_symbol[0], ...). Order class = multileg.
    """
    legs = [
        ("buy_to_open", long_put, "P"),
        ("sell_to_open", short_put, "P"),
        ("sell_to_open", short_call, "C"),
        ("buy_to_open", long_call, "C"),
    ]
    data: dict[str, str] = {
        "class": "multileg",
        "symbol": underlying,
        "type": "market",
        "duration": "day",
    }
    for i, (side, strike, pc) in enumerate(legs):
        data[f"side[{i}]"] = side
        data[f"quantity[{i}]"] = str(contracts)
        data[f"option_symbol[{i}]"] = format_occ_symbol(underlying, expiry, strike, pc)

    resp = await client.post(f"/accounts/{account_id}/orders", data=data)
    resp.raise_for_status()
    return resp.json()


async def _materialize_ic_position(
    db: aiosqlite.Connection,
    candidate: aiosqlite.Row,
    expiry: str,
    order_id: str | None = None,
) -> None:
    """Insert an ic_positions row mirroring a placed candidate."""
    spread_width = float(candidate["long_call"]) - float(candidate["short_call"])
    contracts = candidate["contracts"] or 1
    await db.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
            spread_width, entry_credit, contracts, max_loss, regime_at_entry,
            agent_run_id, order_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            candidate["symbol"], date.today().isoformat(), expiry,
            float(candidate["short_call"]), float(candidate["long_call"]),
            float(candidate["short_put"]), float(candidate["long_put"]),
            spread_width, float(candidate["expected_credit"]),
            contracts, float(candidate["max_loss"]), "normal",
            candidate["agent_run_id"], order_id,
        ),
    )


async def place_approved_candidates(
    db: aiosqlite.Connection,
    client: httpx.AsyncClient,
    account_id: str,
) -> list[dict]:
    """Place every candidate with order_status='approved'.

    On success: candidate -> 'placed', ic_positions row inserted, any
    pending ic_candidate inbox card for that symbol+scan_date is dismissed.
    On API failure: candidate stays 'approved' (will retry next cron),
    error is logged.

    Returns the list of placed candidates (each as a dict).
    """
    cur = await db.execute(
        """SELECT c.id, c.symbol, c.scan_date, c.agent_run_id,
                  c.short_call, c.long_call, c.short_put, c.long_put,
                  c.expected_credit, c.contracts, c.max_loss,
                  ar.scanner_output
           FROM candidates c
           LEFT JOIN agent_runs ar ON c.agent_run_id = ar.id
           WHERE c.order_status='approved'"""
    )
    approved = await cur.fetchall()
    placed: list[dict] = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    for cand in approved:
        try:
            scanner = json.loads(cand["scanner_output"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            scanner = {}
        expiry = scanner.get("expiry")
        if not expiry:
            log.error(
                "place_approved_candidates: candidate %s (%s) missing expiry in "
                "scanner_output; skipping",
                cand["id"], cand["symbol"],
            )
            continue

        try:
            order_resp = await place_iron_condor(
                client, account_id,
                underlying=cand["symbol"], expiry=expiry,
                short_call=float(cand["short_call"]),
                long_call=float(cand["long_call"]),
                short_put=float(cand["short_put"]),
                long_put=float(cand["long_put"]),
                contracts=cand["contracts"] or 1,
            )
        except httpx.HTTPError as e:
            log.exception(
                "place_approved_candidates: order placement failed for %s — "
                "candidate stays 'approved' for retry: %s", cand["symbol"], e,
            )
            continue

        # On Tradier success, transition candidate, write IC, dismiss inbox card.
        await db.execute(
            "UPDATE candidates SET order_status='placed' WHERE id=?", (cand["id"],)
        )
        order_id_str = str(order_resp.get("order", {}).get("id", "")) or None
        await _materialize_ic_position(db, cand, expiry, order_id=order_id_str)
        await db.execute(
            """UPDATE inbox_items
               SET dismissed_at=?
               WHERE item_type='ic_candidate' AND symbol=? AND dismissed_at IS NULL""",
            (now_iso, cand["symbol"]),
        )
        await create_alert(
            db,
            alert_type="ic_placed",
            symbol=cand["symbol"],
            headline=f"Order Placed — {cand['symbol']}",
            body_lines=[
                f"4-leg IC submitted to Tradier for {expiry}.",
                f"Strikes: SC{int(float(cand['short_call']))}/LC{int(float(cand['long_call']))} "
                f"SP{int(float(cand['short_put']))}/LP{int(float(cand['long_put']))}.",
                f"Estimated credit ${float(cand['expected_credit']):.2f}, "
                f"max loss ${float(cand['max_loss']):.0f}.",
                f"Tradier order id: {order_id_str}.",
            ],
            fields={
                "order_id": order_id_str,
                "expiry": expiry,
                "expected_credit": float(cand["expected_credit"]),
                "max_loss": float(cand["max_loss"]),
                "contracts": cand["contracts"] or 1,
            },
            triggered_at=now_iso,
        )
        placed.append({
            "candidate_id": cand["id"],
            "symbol": cand["symbol"],
            "expiry": expiry,
            "order_id": order_resp.get("order", {}).get("id"),
        })

    await db.commit()
    log.info("place_approved_candidates: placed %d order(s)", len(placed))
    return placed
