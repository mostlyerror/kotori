"""Snapshot the PositionDetail screen for a given symbol (default: SPY).

Boots the app via Textual's run_test pilot, opens the detail screen for
the symbol, waits for the worker query to populate, then exports SVG.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/benjaminpoon/dev/kotori/.env")

from kotori_tui.app import KotoriApp
from kotori_tui.screens.position_detail import PositionDetail


async def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    out = Path(f"/tmp/kotori_detail_{symbol.replace('/', '_').replace(' ', '_')}.svg")

    app = KotoriApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        await app.push_screen(PositionDetail(symbol))
        await pilot.pause(2.0)
        svg = app.export_screenshot()
        out.write_text(svg)
        print(f"wrote {out} ({len(svg)} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
