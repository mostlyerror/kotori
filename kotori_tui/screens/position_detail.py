from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Label, Static
from textual.widget import Widget
from textual import work
import kotori_tui.db as db


class PositionDetail(Screen):
    DEFAULT_CSS = """
    PositionDetail { layout: grid; grid-size: 2; grid-columns: 1fr 1fr; }
    .detail-panel { border: solid $panel-lighten-1; padding: 1 2; }
    .detail-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .detail-label { color: $text-muted; }
    .detail-value { text-style: bold; }
    .notes-entry { color: $text-muted; margin-bottom: 1; }
    .thesis-status-intact { color: $success; }
    .thesis-status-weakening { color: $warning; }
    .thesis-status-invalidated { color: $error; }
    """

    BINDINGS = [
        Binding("escape,q", "dismiss", "Back", show=True),
        Binding("n", "add_note", "Add note", show=True),
        Binding("t", "edit_thesis", "Edit thesis", show=True),
    ]

    def __init__(self, symbol: str, **kwargs):
        super().__init__(**kwargs)
        self.symbol = symbol

    def compose(self) -> ComposeResult:
        with Widget(classes="detail-panel", id="left-panel"):
            yield Label(f"{self.symbol}", classes="detail-title", id="detail-header")
            yield Widget(id="price-section")
            yield Label("THESIS", classes="detail-label")
            yield Widget(id="thesis-section")
        with Widget(classes="detail-panel", id="right-panel"):
            yield Label("NOTES", classes="detail-label")
            yield Widget(id="notes-section")

    def on_mount(self) -> None:
        self.load_data()

    @work
    async def load_data(self) -> None:
        position = await db.query(
            "SELECT * FROM positions WHERE symbol=?", (self.symbol,)
        )
        ic = await db.query(
            "SELECT * FROM ic_positions WHERE symbol=? AND exit_reason IS NULL LIMIT 1",
            (self.symbol,)
        )
        thesis = await db.query("SELECT * FROM thesis WHERE symbol=?", (self.symbol,))
        notes = await db.query(
            "SELECT body, created_at FROM notes WHERE symbol=? ORDER BY created_at DESC LIMIT 10",
            (self.symbol,)
        )
        agent_run = []
        if ic and ic[0].get("agent_run_id"):
            agent_run = await db.query(
                "SELECT * FROM agent_runs WHERE id=?", (ic[0]["agent_run_id"],)
            )
        self._render(position, ic, thesis, notes, agent_run)

    def _render(self, position, ic, thesis, notes, agent_run) -> None:
        import json
        price_section = self.query_one("#price-section")
        price_section.remove_children()

        if ic:
            i = ic[0]
            price_section.mount(
                Label(f"SC {i['short_call']:.0f} / LC {i['long_call']:.0f} / SP {i['short_put']:.0f} / LP {i['long_put']:.0f}"),
                Label(f"Entry credit ${i['entry_credit']:.2f}  Current debit ${i['current_debit'] or 0:.2f}"),
                Label(f"Profit captured: {(i['pct_max_profit'] or 0):.0%}  Max loss ${i['max_loss']:.0f}"),
                Label(f"Expiry: {i['expiry']}  IVP at entry: {(i['iv_percentile_at_entry'] or 0):.0%}"),
            )
            if agent_run:
                ar = agent_run[0]
                strat = json.loads(ar.get("strategist_output") or "{}")
                pm = json.loads(ar.get("portfolio_manager_output") or "{}")
                price_section.mount(
                    Label("── Pipeline Reasoning ──", classes="detail-label"),
                    Label(f"Strategist: {strat.get('reasoning','—')[:80]}"),
                    Label(f"Decision: {pm.get('decision','—')} — {pm.get('reasoning','—')[:60]}"),
                )
        elif position:
            p = position[0]
            price_section.mount(
                Label(f"${p['current_price']:.2f}  avg ${p['avg_cost']:.2f}"),
                Label(f"P&L ${p['unrealized_pnl']:+.2f} ({p['unrealized_pnl_pct']:+.1%})"),
                Label(f"Market value ${p['market_value']:,.0f}"),
            )

        thesis_section = self.query_one("#thesis-section")
        thesis_section.remove_children()
        if thesis:
            t = thesis[0]
            status_class = f"thesis-status-{t['status']}"
            thesis_section.mount(
                Label(f"Catalyst: {t.get('entry_catalyst','—')}"),
                Label(f"Source: {t.get('catalyst_source','—')}"),
                Label(f"Target: ${t.get('price_target') or '—'}  Stop: ${t.get('stop_level') or '—'}"),
                Label(f"Horizon: {t.get('time_horizon','—')}"),
                Label(f"Status: {t['status']}", classes=status_class),
            )

        notes_section = self.query_one("#notes-section")
        notes_section.remove_children()
        for n in notes:
            notes_section.mount(Label(f"{n['created_at'][:10]}  {n['body']}", classes="notes-entry"))

    async def action_add_note(self) -> None:
        from kotori_tui.screens.note_input import NoteInput
        import asyncio

        def save_note(note: str) -> None:
            if note:
                asyncio.create_task(db.execute(
                    "INSERT INTO notes (symbol, body, created_at) VALUES (?,?,datetime('now'))",
                    (self.symbol, note)
                ))
        await self.app.push_screen(NoteInput(self.symbol), save_note)

    def action_edit_thesis(self) -> None:
        from kotori_tui.screens.thesis_editor import ThesisEditor
        self.app.push_screen(ThesisEditor(self.symbol))
