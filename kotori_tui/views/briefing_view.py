import json
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Markdown, Static
from textual.binding import Binding
from textual import work
import kotori_tui.db as db


PRIORITY_ICON = {"urgent": "🔴", "action_required": "🟡", "for_review": "🔵"}
PRIORITY_ORDER = ["urgent", "action_required", "for_review"]
SECTION_HEADERS = {
    "urgent": "URGENT",
    "action_required": "ACTION REQUIRED",
    "for_review": "FOR REVIEW",
}


class InboxCard(Static, can_focus=True):
    def __init__(self, item: dict, **kwargs):
        super().__init__(**kwargs)
        self.item = item
        self.add_class(f"prio-{item['priority']}")

    def compose(self) -> ComposeResult:
        icon = PRIORITY_ICON.get(self.item["priority"], "⚪")
        try:
            actions = json.loads(self.item["actions"])
        except Exception:
            actions = []
        action_hints = "  ".join(f"[{a[0]}] {a.replace('_', ' ')}" for a in actions)
        yield Label(f"{icon}  {self.item['title']}", classes="card-title")
        yield Label(self.item["body"], classes="card-body")
        if action_hints:
            yield Label(action_hints, classes="card-actions")


class BriefingView(Widget):
    DEFAULT_CSS = """
    BriefingView { layout: grid; grid-size: 2; grid-columns: 2fr 1fr; padding: 0; }
    .main-col { padding: 1 2; }
    .sidebar { border-left: solid $panel-lighten-1; padding: 1; }

    .section-header { color: $text-muted; text-style: bold; margin-top: 1; }
    .sidebar-title { text-style: bold; color: $text-muted; margin-bottom: 1; }

    InboxCard {
        border: solid $panel-lighten-1;
        padding: 1;
        margin-bottom: 1;
    }
    InboxCard:focus { border: solid $accent; }
    InboxCard.prio-urgent { border-left: thick $error; }
    InboxCard.prio-action_required { border-left: thick $warning; }
    InboxCard.prio-for_review { border-left: thick $primary; }

    .card-title { text-style: bold; }
    .card-body { color: $text-muted; }
    .card-actions { color: $accent; margin-top: 1; }

    .inbox-zero { color: $success; text-style: bold; padding: 1; }
    .briefing-md { margin-top: 1; }

    .position-row { padding: 0; }
    .thesis-intact { color: $success; }
    .thesis-weakening { color: $warning; }
    .thesis-invalidated { color: $error; }
    """

    BINDINGS = [
        Binding("j,down", "focus_next", "Next", show=False),
        Binding("k,up", "focus_previous", "Prev", show=False),
        Binding("enter", "open_detail", "Detail", show=True),
    ]

    def compose(self) -> ComposeResult:
        with Widget(classes="main-col"):
            yield Widget(id="inbox-section")
            yield Label("BRIEFING", classes="section-header")
            yield Markdown("_Loading briefing..._", id="briefing-md", classes="briefing-md")
        with Widget(classes="sidebar"):
            yield Label("POSITIONS", classes="sidebar-title")
            yield Widget(id="positions-list")

    def on_mount(self) -> None:
        self.refresh_data()
        self.set_interval(3, self.refresh_data)

    @work(exclusive=True)
    async def refresh_data(self) -> None:
        items = await db.query(
            "SELECT * FROM inbox_items WHERE dismissed_at IS NULL "
            "ORDER BY CASE priority WHEN 'urgent' THEN 0 "
            "WHEN 'action_required' THEN 1 ELSE 2 END, created_at"
        )
        briefing = await db.query(
            "SELECT content FROM briefings WHERE period='daily' "
            "ORDER BY generated_at DESC LIMIT 1"
        )
        positions = await db.query(
            "SELECT p.symbol, p.current_price, p.unrealized_pnl_pct, "
            "COALESCE(t.status,'—') as status "
            "FROM positions p LEFT JOIN thesis t ON p.symbol=t.symbol "
            "ORDER BY p.symbol"
        )

        await self._render_inbox(items)
        self._render_briefing(briefing[0]["content"] if briefing else "_No briefing available._")
        await self._render_positions(positions)

    async def _render_inbox(self, items: list) -> None:
        section = self.query_one("#inbox-section")
        had_focus = isinstance(self.app.focused, InboxCard)
        await section.remove_children()

        if not items:
            await section.mount(Label("✓  Inbox zero — portfolio running autonomously", classes="inbox-zero"))
            return

        first_card: InboxCard | None = None
        for priority in PRIORITY_ORDER:
            group = [i for i in items if i["priority"] == priority]
            if not group:
                continue
            await section.mount(Label(SECTION_HEADERS[priority], classes="section-header"))
            for item in group:
                card = InboxCard(item)
                await section.mount(card)
                if first_card is None:
                    first_card = card

        if first_card is not None and not had_focus:
            first_card.focus()

    def _render_briefing(self, content: str) -> None:
        self.query_one("#briefing-md", Markdown).update(content)

    async def _render_positions(self, positions: list) -> None:
        container = self.query_one("#positions-list")
        await container.remove_children()
        status_icon = {"intact": "●", "weakening": "~", "invalidated": "✗", "—": "—"}
        for p in positions:
            icon = status_icon.get(p["status"], "—")
            pct = (p["unrealized_pnl_pct"] or 0) * 100
            line = f"{p['symbol']:<6} ${p['current_price']:>8.2f} {pct:+5.1f}% {icon}"
            await container.mount(Label(line, classes=f"position-row thesis-{p['status']}"))

    def action_open_detail(self) -> None:
        focused = self.app.focused
        if not isinstance(focused, InboxCard):
            cards = list(self.query(InboxCard))
            if not cards:
                return
            focused = cards[0]
            focused.focus()
        symbol = focused.item.get("symbol")
        if symbol:
            self.app.open_position_detail(symbol)
