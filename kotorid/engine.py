from __future__ import annotations
import asyncio
from kotorid.clock import BacktestClock
from kotorid.handlers import Handler


class Engine:
    def __init__(self, clock: BacktestClock, handlers: list[Handler]):
        self.clock = clock
        self.handlers = handlers

    def run(self) -> dict:
        return asyncio.run(self._run_async())

    async def _run_async(self) -> dict:
        context: dict = {}
        for timestamp, state in self.clock.tick():
            for handler in self.handlers:
                if handler.should_run(timestamp, state, None):
                    await handler.handle(timestamp, state, context)
        return context
