import json
from datetime import date, timedelta

import pytest
from kotorid.alerts_lib import ALERT_FIELDS_KEY
from kotorid.mock_data import seed_mock_data
from kotorid.jobs import gap_monitor, run_position_monitor

@pytest.mark.asyncio
async def test_position_monitor_no_triggers_on_fresh_data(conn):
    await seed_mock_data(conn)
    # Override to a debit safely between profit_target (0.925) and stop_loss (3.70)
    # so neither trigger fires. entry_credit=1.85, safe zone: 0.926 < debit < 3.70
    await conn.execute(
        "UPDATE ic_positions SET current_debit = 1.20 WHERE symbol = 'TSLA'"
    )
    closed = await run_position_monitor(conn)
    assert closed == []

@pytest.mark.asyncio
async def test_position_monitor_fires_profit_target(conn):
    await seed_mock_data(conn)
    await conn.execute(
        "UPDATE ic_positions SET current_debit = 0.925 WHERE symbol = 'TSLA'"
    )
    closed = await run_position_monitor(conn)
    assert len(closed) == 1
    assert closed[0]["symbol"] == "TSLA"
    assert closed[0]["exit_reason"] == "profit_target"

@pytest.mark.asyncio
async def test_position_monitor_fires_stop_loss(conn):
    await seed_mock_data(conn)
    await conn.execute(
        "UPDATE ic_positions SET current_debit = 3.70 WHERE symbol = 'TSLA'"
    )
    closed = await run_position_monitor(conn)
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "stop_loss"


# Use dead-zone debits in expiry tests so profit_target / stop_loss don't fire
# and pre-empt force_close (the actual code path under test).
# For entry_credit=$1.00, dead zone is $0.50 < debit < $2.00.


@pytest.mark.asyncio
async def test_force_close_does_not_fire_on_expiry_day(conn):
    """expiry day is too early — broker legs are still open until 3pm CT.

    force_close must wait until the calendar day AFTER expiry. Old code
    used `expiry <= today` and fired prematurely.
    """
    today = date.today().isoformat()
    # IC expires TODAY, debit in the dead zone (no profit/stop trigger).
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
            spread_width, entry_credit, contracts, max_loss, regime_at_entry,
            current_debit)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
        "SPY", today, today, 760.0, 765.0, 735.0, 730.0,
        5.0, 1.00, 1, 400.0, "normal", 0.75,
    )
    closed = await run_position_monitor(conn)
    assert closed == []  # IC stays open on expiry day itself


@pytest.mark.asyncio
async def test_force_close_fires_day_after_expiry_with_real_pnl(conn):
    """The day after expiry, force_close fires and computes realized P&L
    from the last-known current_debit instead of hardcoding 0."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    # IC expired yesterday, last debit refresh recorded $0.75 (dead zone
    # — modest gain, neither profit_target nor stop_loss had fired).
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
            spread_width, entry_credit, contracts, max_loss, regime_at_entry,
            current_debit)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
        "SPY", yesterday, yesterday, 760.0, 765.0, 735.0, 730.0,
        5.0, 1.00, 1, 400.0, "normal", 0.75,
    )
    closed = await run_position_monitor(conn)

    row = await conn.fetchrow(
        "SELECT exit_reason, exit_debit, realized_pnl FROM ic_positions WHERE symbol='SPY'"
    )
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "force_close"
    # realized_pnl = (1.00 - 0.75) * 100 * 1 = $25 (modest gain captured)
    assert closed[0]["realized_pnl"] == pytest.approx(25.0)
    assert row["exit_reason"] == "force_close"
    assert row["exit_debit"] == pytest.approx(0.75)
    assert row["realized_pnl"] == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_force_close_leaves_pnl_null_when_debit_missing(conn):
    """If ic_refresh never landed a debit, realized_pnl stays NULL rather
    than getting a misleading hardcoded 0."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
            spread_width, entry_credit, contracts, max_loss, regime_at_entry,
            current_debit)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
        "SPY", yesterday, yesterday, 760.0, 765.0, 735.0, 730.0,
        5.0, 1.00, 1, 400.0, "normal", None,
    )
    closed = await run_position_monitor(conn)

    row = await conn.fetchrow(
        "SELECT exit_reason, exit_debit, realized_pnl FROM ic_positions WHERE symbol='SPY'"
    )
    assert len(closed) == 1
    assert closed[0]["realized_pnl"] is None
    assert row["exit_reason"] == "force_close"
    assert row["exit_debit"] is None
    assert row["realized_pnl"] is None


@pytest.mark.asyncio
async def test_stop_loss_alert_has_structured_content(conn):
    # IC entered at $1.00 credit, current debit $2.10 = stop_loss territory
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, current_debit)
           VALUES ('SPY','2026-05-22','2026-05-29',
                   760,765,735,730,5,1.00,1,400,2.10)"""
    )

    await run_position_monitor(conn)

    row = await conn.fetchrow(
        "SELECT message FROM alerts WHERE alert_type='stop_loss'"
    )

    assert row is not None
    assert ALERT_FIELDS_KEY in row["message"]
    plain, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    payload = json.loads(json_tail)
    fields = payload["fields"]
    assert fields["entry_credit"] == 1.00
    assert fields["exit_debit"] == 2.10
    assert fields["realized_pnl"] == pytest.approx(-110.0)  # (1.00-2.10)*100*1
    # Body lines must explain *why*
    body = "\n".join(payload["body_lines"])
    assert "$1.00" in body  # entry credit
    assert "$2.10" in body  # exit debit
    assert "-$110" in body or "−$110" in body  # realized loss


@pytest.mark.asyncio
async def test_force_close_alert_is_structured(conn):
    # Debit 0.75 is in the dead zone (entry_credit=1.00 -> profit_target
    # threshold 0.50, stop_loss threshold 2.00), so neither pre-empt
    # force_close. Expected pnl = (1.00 - 0.75) * 100 * 1 = 25.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, current_debit)
           VALUES ('SPY','2026-05-19',$1,760,765,735,730,5,1.00,1,400,0.75)""",
        yesterday,
    )

    await run_position_monitor(conn)

    row = await conn.fetchrow(
        "SELECT message FROM alerts WHERE alert_type='force_close'"
    )

    assert row is not None
    assert ALERT_FIELDS_KEY in row["message"]
    _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    payload = json.loads(json_tail)
    fields = payload["fields"]
    assert fields["realized_pnl"] == pytest.approx(25.0)
    assert fields["entry_credit"] == pytest.approx(1.00)
    assert fields["exit_debit"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_gap_monitor_emits_structured_alert(conn):
    """gap_monitor should write a structured gap_risk alert when an open
    IC's underlying price is within 50% of expected_move of a short strike."""

    # Open IC: short call 760, short put 735, expected_move 10.
    # 50% of expected_move = 5. If price = 757, cushion_call = 3 < 5 -> fire.
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, regime_at_entry, expected_move)
           VALUES ('SPY','2026-05-20','2026-05-29',
                   760,765,735,730,5,1.00,1,400,'normal',10.0)"""
    )
    # positions row with current_price (gap_monitor reads from positions).
    await conn.execute(
        """INSERT INTO positions
           (symbol, quantity, avg_cost, current_price, market_value,
            unrealized_pnl, unrealized_pnl_pct, instrument_type, last_updated)
           VALUES ('SPY',0,0,757.0,0,0,0,'stock','2026-05-22T00:00:00')"""
    )

    await gap_monitor(conn)

    row = await conn.fetchrow(
        "SELECT message FROM alerts WHERE alert_type='gap_risk'"
    )

    assert row is not None
    assert ALERT_FIELDS_KEY in row["message"]
    _, _, json_tail = row["message"].partition(ALERT_FIELDS_KEY)
    payload = json.loads(json_tail)
    fields = payload["fields"]
    assert fields["price"] == pytest.approx(757.0)
    assert fields["short_call"] == pytest.approx(760.0)
    assert fields["short_put"] == pytest.approx(735.0)
    assert fields["expected_move"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_position_warning_fires_once_at_50pct_to_stop(conn):
    """Fires once when debit >= 1.5x entry, dedups via position_warning_at."""
    # entry_credit=1.00 -> stop at debit=2.00. Warn at debit >= 1.50, < 2.00.
    # debit=1.60 is in the warning zone.
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, current_debit)
           VALUES ('SPY','2026-05-22','2026-05-29',
                   760,765,735,730,5,1.00,1,400,1.60)"""
    )

    # First call fires the warning
    await run_position_monitor(conn)
    count1 = await conn.fetchval(
        "SELECT COUNT(*) FROM alerts WHERE alert_type='position_warning'"
    )

    # Second call must NOT fire again
    await run_position_monitor(conn)
    count2 = await conn.fetchval(
        "SELECT COUNT(*) FROM alerts WHERE alert_type='position_warning'"
    )

    assert count1 == 1
    assert count2 == 1


@pytest.mark.asyncio
async def test_position_warning_does_not_fire_below_threshold(conn):
    """Debit at 1.40 (below 1.5x entry) should not warn."""
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, current_debit)
           VALUES ('SPY','2026-05-22','2026-05-29',
                   760,765,735,730,5,1.00,1,400,1.40)"""
    )
    await run_position_monitor(conn)
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM alerts WHERE alert_type='position_warning'"
    )
    assert count == 0
