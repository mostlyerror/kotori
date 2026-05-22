from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Select
from textual import work
import kotori_tui.db as db


class ThesisEditor(ModalScreen):
    DEFAULT_CSS = """
    ThesisEditor { align: center middle; }
    ThesisEditor > VerticalScroll {
        width: 70;
        height: 32;
        border: solid $accent;
        padding: 1 2;
        background: $surface;
    }
    .field-label { color: $text-muted; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def __init__(self, symbol: str, **kwargs):
        super().__init__(**kwargs)
        self.symbol = symbol

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label(f"Edit Thesis — {self.symbol}", classes="detail-title")
            yield Label("Entry Catalyst", classes="field-label")
            yield Input(id="catalyst", placeholder="e.g. Insider cluster buy")
            yield Label("Catalyst Source", classes="field-label")
            yield Input(id="catalyst-source", placeholder="e.g. SEC Form 4 / Unusual Whales")
            yield Label("Price Target", classes="field-label")
            yield Input(id="price-target", placeholder="e.g. 1050")
            yield Label("Stop Level", classes="field-label")
            yield Input(id="stop-level", placeholder="e.g. 820")
            yield Label("Time Horizon", classes="field-label")
            yield Input(id="horizon", placeholder="e.g. 4 weeks")
            yield Label("Status", classes="field-label")
            yield Select(
                [("intact", "intact"), ("weakening", "weakening"), ("invalidated", "invalidated")],
                id="status",
                value="intact"
            )
            yield Label("[Ctrl+S] Save   [Esc] Cancel", classes="field-label")

    def on_mount(self) -> None:
        self.load_existing()

    @work
    async def load_existing(self) -> None:
        rows = await db.query("SELECT * FROM thesis WHERE symbol=?", (self.symbol,))
        if rows:
            t = rows[0]
            def populate():
                if t.get("entry_catalyst"):
                    self.query_one("#catalyst", Input).value = t["entry_catalyst"]
                if t.get("catalyst_source"):
                    self.query_one("#catalyst-source", Input).value = t["catalyst_source"]
                if t.get("price_target"):
                    self.query_one("#price-target", Input).value = str(t["price_target"])
                if t.get("stop_level"):
                    self.query_one("#stop-level", Input).value = str(t["stop_level"])
                if t.get("time_horizon"):
                    self.query_one("#horizon", Input).value = t["time_horizon"]
                self.query_one("#status", Select).value = t["status"]
            populate()

    @work
    async def action_save(self) -> None:
        catalyst = self.query_one("#catalyst", Input).value
        source = self.query_one("#catalyst-source", Input).value
        target_str = self.query_one("#price-target", Input).value
        stop_str = self.query_one("#stop-level", Input).value
        horizon = self.query_one("#horizon", Input).value
        status = self.query_one("#status", Select).value

        target = float(target_str) if target_str else None
        stop = float(stop_str) if stop_str else None

        existing = await db.query("SELECT symbol FROM thesis WHERE symbol=?", (self.symbol,))
        if existing:
            await db.execute(
                """UPDATE thesis SET entry_catalyst=?, catalyst_source=?, price_target=?,
                   stop_level=?, time_horizon=?, status=?, updated_at=datetime('now')
                   WHERE symbol=?""",
                (catalyst, source, target, stop, horizon, status, self.symbol)
            )
        else:
            await db.execute(
                """INSERT INTO thesis (symbol, position_type, entry_catalyst, catalyst_source,
                   price_target, stop_level, time_horizon, status, auto_populated,
                   created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,0,datetime('now'),datetime('now'))""",
                (self.symbol, "directional", catalyst, source, target, stop, horizon, status)
            )
        self.dismiss()
