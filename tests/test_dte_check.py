import json
from datetime import date, timedelta

import pytest

from kotorid.alerts_lib import ALERT_FIELDS_KEY
from kotorid.jobs import dte_check


@pytest.mark.asyncio
async def test_dte_check_fires_for_ic_expiring_tomorrow(conn):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, current_debit)
           VALUES ('SPY','2026-05-22',$1,760,765,735,730,5,1.00,1,400,0.42)""",
        tomorrow,
    )

    await dte_check(conn)

    rows = await conn.fetch("SELECT message FROM alerts WHERE alert_type='dte_warning'")

    assert len(rows) == 1
    _, _, json_tail = rows[0]["message"].partition(ALERT_FIELDS_KEY)
    fields = json.loads(json_tail)["fields"]
    assert fields["dte"] == 1
    assert fields["current_debit"] == 0.42


@pytest.mark.asyncio
async def test_dte_check_dedup_same_day(conn):
    """Running twice in one day produces only one alert per IC."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, current_debit)
           VALUES ('SPY','2026-05-22',$1,760,765,735,730,5,1.00,1,400,0.42)""",
        tomorrow,
    )

    await dte_check(conn)
    await dte_check(conn)

    count = await conn.fetchval(
        "SELECT COUNT(*) FROM alerts WHERE alert_type='dte_warning'"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_dte_check_no_alert_when_not_tomorrow(conn):
    far = (date.today() + timedelta(days=5)).isoformat()
    await conn.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call,
            short_put, long_put, spread_width, entry_credit,
            contracts, max_loss, current_debit)
           VALUES ('SPY','2026-05-22',$1,760,765,735,730,5,1.00,1,400,0.42)""",
        far,
    )
    await dte_check(conn)
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM alerts WHERE alert_type='dte_warning'"
    )
    assert count == 0
