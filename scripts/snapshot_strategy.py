"""Snapshot the StrategyView screen against the live DB."""
from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/benjaminpoon/dev/kotori/.env")

from kotori_tui.app import KotoriApp
from kotori_tui.screens.strategy_view import StrategyView

OUT = Path("/tmp/kotori_strategy.svg")


async def main() -> None:
    app = KotoriApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        await app.push_screen(StrategyView())
        await pilot.pause(2.0)
        svg = app.export_screenshot()
        OUT.write_text(svg)
        print(f"wrote {OUT} ({len(svg)} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
