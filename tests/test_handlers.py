from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from kotorid.clock import BacktestClock, MarketState
from kotorid.engine import Engine
from kotorid.handlers import Handler, Frequency

ET = ZoneInfo("America/New_York")


class CountingHandler(Handler):
    def __init__(self, frequency: Frequency):
        super().__init__(frequency)
        self.call_count = 0

    async def handle(self, timestamp: datetime, state: MarketState, context: dict) -> None:
        self.call_count += 1


def test_every_tick_handler_runs_each_tick():
    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2))
    handler = CountingHandler(Frequency.EVERY_TICK)
    engine = Engine(clock, [handler])
    engine.run()
    expected_ticks = len(list(BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2)).tick()))
    assert handler.call_count == expected_ticks


def test_daily_handler_runs_once_per_day():
    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 3))
    handler = CountingHandler(Frequency.DAILY_OPEN)
    engine = Engine(clock, [handler])
    engine.run()
    assert handler.call_count == 2


def test_handler_priority_order():
    call_order = []

    class OrderTracker(Handler):
        def __init__(self, name: str, frequency: Frequency):
            super().__init__(frequency)
            self.name = name

        async def handle(self, timestamp, state, context):
            call_order.append(self.name)

    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2))
    engine = Engine(clock, [
        OrderTracker("data", Frequency.EVERY_TICK),
        OrderTracker("signal", Frequency.EVERY_TICK),
        OrderTracker("allocator", Frequency.EVERY_TICK),
    ])
    engine.run()
    assert call_order[:3] == ["data", "signal", "allocator"]


def test_engine_passes_shared_context():
    class Writer(Handler):
        async def handle(self, timestamp, state, context):
            context["written"] = True

    class Reader(Handler):
        def __init__(self):
            super().__init__(Frequency.EVERY_TICK)
            self.saw_written = False

        async def handle(self, timestamp, state, context):
            if context.get("written"):
                self.saw_written = True

    writer = Writer(Frequency.EVERY_TICK)
    reader = Reader()
    clock = BacktestClock(start=date(2024, 1, 2), end=date(2024, 1, 2))
    engine = Engine(clock, [writer, reader])
    engine.run()
    assert reader.saw_written
