from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from kotorid.clock import BacktestClock, MarketState

ET = ZoneInfo("America/New_York")

def test_market_state_during_hours():
    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2))
    ticks = list(clock.tick())
    # First tick should be gap_open at 9:30 ET
    assert ticks[0][1] == MarketState.GAP_OPEN
    assert ticks[0][0].hour == 9 and ticks[0][0].minute == 30
    # Second tick is normal market_open
    assert ticks[1][1] == MarketState.MARKET_OPEN
    # Last tick should be at or before 16:00 ET
    last_ts, last_state = ticks[-1]
    assert last_ts.hour <= 16

def test_skips_weekends():
    clock = BacktestClock(start=date(2024, 1, 5), end=date(2024, 1, 8))  # Fri-Mon
    ticks = list(clock.tick())
    dates_seen = {t[0].date() for t in ticks}
    assert date(2024, 1, 6) not in dates_seen  # Saturday
    assert date(2024, 1, 7) not in dates_seen  # Sunday
    assert date(2024, 1, 5) in dates_seen
    assert date(2024, 1, 8) in dates_seen

def test_tick_count_single_day():
    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2))
    ticks = list(clock.tick())
    # 9:30 to 16:00 = 6.5 hours = 390 min / 15 = 26 ticks + 1 gap_open = 27
    assert len(ticks) == 27

def test_step_override():
    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2), step=timedelta(minutes=30))
    ticks = list(clock.tick())
    # 390 min / 30 = 13 ticks + 1 gap_open = 14
    assert len(ticks) == 14
