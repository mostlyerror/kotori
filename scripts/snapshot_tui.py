"""Render kotori_tui to an SVG snapshot for visual inspection.

Boots the app in Textual's test pilot, waits for the data refresh to
populate widgets, then exports the screen as SVG and exits.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from kotori_tui.app import KotoriApp

OUT = Path("/tmp/kotori_snapshot.svg")


async def main() -> None:
    app = KotoriApp()
    async with app.run_test(size=(120, 40)) as pilot:
        # Give the BriefingView/StatusBar's set_interval(3) refresh + initial
        # mount queries time to finish populating before we snapshot.
        await pilot.pause(3.5)
        svg = app.export_screenshot()
        OUT.write_text(svg)
        print(f"wrote {OUT} ({len(svg)} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
