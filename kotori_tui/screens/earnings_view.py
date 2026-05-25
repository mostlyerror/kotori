"""Earnings calendar TUI screen — shows upcoming earnings for watchlist symbols."""
from __future__ import annotations

from datetime import date
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import DataTable, Label, Static
from textual import work
import kotori_tui.db as db


EARNINGS_WINDOW_START = 3
EARNINGS_WINDOW_END = 14


class EarningsCalendar(Widget):
    DEFAULT_CSS = """
    EarningsCalendar { height: 1fr; padding: 1 2; }
    .earnings-header { text-style: bold; color: $text-muted; margin-bottom: 1; }
    .window-label { color: $text-muted; margin-bottom: 1; }
    DataTable { height: 1fr; }
    .no-data { color: $text-muted; padding: 2; }
    """

    def compose(self) -> ComposeResult:
        yield Label("EARNINGS CALENDAR", classes="earnings-header")
        yield Label(
            f"IC window: {EARNINGS_WINDOW_START}-{EARNINGS_WINDOW_END} days before earnings",
            classes="window-label",
        )
        yield DataTable(id="earnings-table")

    def on_mount(self) -> None:
        table = self.query_one("#earnings-table", DataTable)
        table.add_columns(
            "Symbol", "Earnings Date", "Days", "Window", "EPS Est", "Last EPS", "Surprise",
        )
        self.load_data()

    @work(exclusive=True)
    async def load_data(self) -> None:
        today = date.today()
        rows = await db.query(
            """SELECT symbol, earnings_date, eps_estimate, reported_eps,
                      surprise_pct, is_confirmed
               FROM earnings_calendar
               WHERE earnings_date >= $1
               ORDER BY earnings_date, symbol""",
            (today,),
        )

        table = self.query_one("#earnings-table", DataTable)
        table.clear()

        if not rows:
            table.add_row("—", "No earnings data. Run kotorid to refresh.", "", "", "", "", "")
            return

        for r in rows:
            earn_date = r["earnings_date"]
            days_until = (earn_date - today).days
            in_window = EARNINGS_WINDOW_START <= days_until <= EARNINGS_WINDOW_END

            if days_until <= 2:
                day_style = "bold red"
            elif in_window:
                day_style = "bold green"
            else:
                day_style = ""

            window_marker = "●" if in_window else ""
            eps_est = f"{r['eps_estimate']:.2f}" if r["eps_estimate"] is not None else "—"
            last_eps = f"{r['reported_eps']:.2f}" if r["reported_eps"] is not None else "—"
            surprise = f"{r['surprise_pct']:+.1f}%" if r["surprise_pct"] is not None else "—"

            table.add_row(
                r["symbol"],
                earn_date.strftime("%b %d"),
                str(days_until),
                window_marker,
                eps_est,
                last_eps,
                surprise,
            )


class EarningsView(Screen):
    BINDINGS = [
        Binding("escape,q", "pop_screen", "Back", show=True),
        Binding("r", "refresh", "Refresh", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield EarningsCalendar()

    def action_refresh(self) -> None:
        self.query_one(EarningsCalendar).load_data()
