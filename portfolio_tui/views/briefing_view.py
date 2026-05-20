from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Markdown, TabbedContent, TabPane
from textual.binding import Binding
from textual import work
import portfolio_tui.db as db


class BriefingView(Widget):
    DEFAULT_CSS = """
    BriefingView { layout: grid; grid-size: 2; grid-columns: 2fr 1fr; padding: 0; }
    .briefing-main { padding: 1 2; }
    .briefing-sidebar { border-left: solid $panel-lighten-1; padding: 1; }
    .sidebar-title { text-style: bold; color: $text-muted; margin-bottom: 1; }
    .position-row { padding: 0; }
    .position-row:hover { background: $panel-lighten-1; }
    """

    BINDINGS = [
        Binding("d", "show_period('daily')", "Daily", show=True),
        Binding("w", "show_period('weekly')", "Weekly", show=True),
        Binding("m", "show_period('monthly')", "Monthly", show=True),
    ]

    _period: str = "daily"

    def compose(self) -> ComposeResult:
        with Widget(classes="briefing-main"):
            with TabbedContent(initial="tab-daily", id="briefing-tabs"):
                with TabPane("Daily", id="tab-daily"):
                    yield Markdown("_Loading..._", id="md-daily")
                with TabPane("Weekly", id="tab-weekly"):
                    yield Markdown("_Weekly briefing will appear here._", id="md-weekly")
                with TabPane("Monthly", id="tab-monthly"):
                    yield Markdown("_Monthly briefing will appear here._", id="md-monthly")
        with Widget(classes="briefing-sidebar"):
            yield Label("POSITIONS", classes="sidebar-title")
            yield Widget(id="sidebar-positions")
            yield Label("ALERTS", classes="sidebar-title")
            yield Widget(id="sidebar-alerts")

    def on_mount(self) -> None:
        self.refresh_briefing()
        self.set_interval(5, self.refresh_briefing)

    @work(exclusive=True)
    async def refresh_briefing(self) -> None:
        briefing = await db.query(
            "SELECT content FROM briefings WHERE period=? ORDER BY generated_at DESC LIMIT 1",
            (self._period,)
        )
        positions = await db.query(
            "SELECT p.symbol, p.current_price, p.unrealized_pnl_pct, "
            "COALESCE(t.status,'—') as status "
            "FROM positions p LEFT JOIN thesis t ON p.symbol=t.symbol"
        )
        alerts = await db.query(
            "SELECT symbol, message FROM alerts WHERE acknowledged=0 ORDER BY triggered_at DESC LIMIT 4"
        )
        content = briefing[0]["content"] if briefing else "_No briefing available._"
        await self._update_content(content, positions, alerts)

    async def _update_content(self, content: str, positions: list, alerts: list) -> None:
        md_id = f"md-{self._period}"
        self.query_one(f"#{md_id}", Markdown).update(content)

        pos_container = self.query_one("#sidebar-positions")
        await pos_container.remove_children()
        status_icon = {"intact": "●", "weakening": "~", "invalidated": "✗"}
        for p in positions:
            icon = status_icon.get(p["status"], "—")
            pct = p["unrealized_pnl_pct"] * 100
            await pos_container.mount(Label(
                f"{p['symbol']:<6} ${p['current_price']:>8.2f} {pct:+.1f}% {icon}",
                classes="position-row"
            ))

        alert_container = self.query_one("#sidebar-alerts")
        await alert_container.remove_children()
        for a in alerts:
            await alert_container.mount(Label(f"⚡ {a['symbol'] or ''} {a['message'][:30]}"))

    def action_show_period(self, period: str) -> None:
        self._period = period
        self.refresh_briefing()
