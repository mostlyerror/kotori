"""Snapshot a modal screen (NoteInput or ThesisEditor) for a given symbol.

Usage: snapshot_modal.py thesis|note SYMBOL
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/benjaminpoon/dev/kotori/.env")

from kotori_tui.app import KotoriApp
from kotori_tui.screens.note_input import NoteInput
from kotori_tui.screens.thesis_editor import ThesisEditor


async def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: snapshot_modal.py thesis|note SYMBOL")
    kind = sys.argv[1]
    symbol = sys.argv[2]

    if kind == "thesis":
        screen_factory = lambda: ThesisEditor(symbol)
    elif kind == "note":
        screen_factory = lambda: NoteInput(symbol)
    else:
        raise SystemExit(f"unknown modal kind: {kind}")

    out = Path(f"/tmp/kotori_modal_{kind}_{symbol.replace('/', '_').replace(' ', '_')}.svg")

    app = KotoriApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        await app.push_screen(screen_factory())
        await pilot.pause(2.0)
        svg = app.export_screenshot()
        out.write_text(svg)
        print(f"wrote {out} ({len(svg)} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
