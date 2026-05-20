from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Label
from textual import work
from textual.binding import Binding
import portfolio_tui.db as db


class GridView(Widget):
    DEFAULT_CSS = """
    GridView { layout: grid; grid-size: 3; grid-rows: 1fr; padding: 0; }
    .panel { border: solid $panel-lighten-1; padding: 1; }
    .panel-title { color: $text-muted; text-style: bold; margin-bottom: 1; }
    .green { color: $success; }
    .red { color: $error; }
    .yellow { color: $warning; }
    """

    BINDINGS = [
        Binding("enter", "open_detail", "Detail", show=True),
        Binding("j,down", "cursor_down", "Down", show=False),
        Binding("k,up", "cursor_up", "Up", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Widget(classes="panel", id="positions-panel"):
            yield Label("POSITIONS", classes="panel-title")
            yield DataTable(id="positions-table", cursor_type="row")
        with Widget(classes="panel", id="regime-panel"):
            yield Label("REGIME + IV", classes="panel-title")
            yield Widget(id="regime-content")
        with Widget(classes="panel", id="activity-panel"):
            yield Label("RECENT ACTIVITY", classes="panel-title")
            yield Widget(id="activity-content")

    def on_mount(self) -> None:
        table = self.query_one("#positions-table", DataTable)
        table.add_columns("Symbol", "Qty", "P&L%", "Thesis")
        self.set_interval(3, self.refresh_data)

    @work(exclusive=True)
    async def refresh_data(self) -> None:
        positions = await db.query(
            "SELECT p.symbol, p.quantity, p.unrealized_pnl_pct, "
            "COALESCE(t.status,'—') as thesis_status, p.instrument_type "
            "FROM positions p LEFT JOIN thesis t ON p.symbol=t.symbol"
        )
        regimes = await db.query(
            "SELECT DISTINCT symbol, market_regime, earnings_regime, iv_regime, vix "
            "FROM regime_snapshots ORDER BY timestamp DESC LIMIT 10"
        )
        alerts = await db.query(
            "SELECT symbol, message, triggered_at FROM alerts "
            "ORDER BY triggered_at DESC LIMIT 6"
        )
        self._update_table(positions)
        await self._update_regime(regimes)
        await self._update_activity(alerts)

    def _update_table(self, positions: list) -> None:
        table = self.query_one("#positions-table", DataTable)
        table.clear()
        status_icon = {"intact": "●", "weakening": "~", "invalidated": "✗"}
        for p in positions:
            pct = p["unrealized_pnl_pct"] * 100
            pct_str = f"{pct:+.1f}%"
            icon = status_icon.get(p["thesis_status"], "—")
            table.add_row(p["symbol"], str(int(p["quantity"])), pct_str, icon)

    async def _update_regime(self, regimes: list) -> None:
        content = self.query_one("#regime-content")
        await content.remove_children()
        seen = set()
        for r in regimes:
            if r["symbol"] in seen:
                continue
            seen.add(r["symbol"])
            iv_label = {"high": "IVR↑", "normal": "IVR~", "low": "IVR↓"}.get(r["iv_regime"], "")
            earnings = " PreEarnings" if r["earnings_regime"] == "pre_earnings" else ""
            await content.mount(Label(f"{r['symbol']:<6} {r['market_regime']}{earnings} {iv_label}"))

    async def _update_activity(self, alerts: list) -> None:
        content = self.query_one("#activity-content")
        await content.remove_children()
        for a in alerts:
            await content.mount(Label(f"{a['symbol'] or '—':<6} {a['message'][:40]}"))

    def action_open_detail(self) -> None:
        table = self.query_one("#positions-table", DataTable)
        if table.cursor_row >= 0:
            row = table.get_row_at(table.cursor_row)
            self.app.open_position_detail(row[0])

    def action_cursor_down(self) -> None:
        self.query_one("#positions-table", DataTable).action_scroll_down()

    def action_cursor_up(self) -> None:
        self.query_one("#positions-table", DataTable).action_scroll_up()
