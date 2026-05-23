import json
from datetime import date
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Markdown, Static
from textual.binding import Binding
from textual import work
import kotori_tui.db as db


def format_position_label(position: dict) -> str:
    """Render a compact, human-readable label for a position row.

    Stocks return their ticker as-is. Options return broker shorthand
    derived from the OCC components already split out by position_sync:
    ``SPY 5/29 747C`` (current-year) or ``SPY 1/15/27 747C`` (year cross).
    Falls back to the raw OCC symbol if any required column is missing.
    """
    if position.get("instrument_type") != "option":
        return position["symbol"]
    underlying = position.get("underlying")
    expiry = position.get("expiry")
    strike = position.get("strike")
    put_call = position.get("put_call")
    if not (underlying and expiry and strike is not None and put_call):
        return position["symbol"]
    try:
        exp = date.fromisoformat(expiry)
    except ValueError:
        return position["symbol"]
    if exp.year == date.today().year:
        date_part = f"{exp.month}/{exp.day}"
    else:
        date_part = f"{exp.month}/{exp.day}/{exp.year % 100:02d}"
    return f"{underlying} {date_part} {strike:g}{put_call}"


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
        Binding("a", "approve_candidate", "Approve", show=True),
        Binding("r", "reject_candidate", "Reject", show=True),
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
            "p.instrument_type, p.underlying, p.expiry, p.strike, p.put_call, "
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
        valid_thesis_classes = {"intact", "weakening", "invalidated"}
        for p in positions:
            status = p["status"]
            icon = status_icon.get(status, "—")
            pct = (p["unrealized_pnl_pct"] or 0) * 100
            label = format_position_label(p)
            line = f"{label:<15} ${p['current_price']:>8.2f} {pct:+5.1f}% {icon}"
            classes = "position-row"
            if status in valid_thesis_classes:
                classes += f" thesis-{status}"
            await container.mount(Label(line, classes=classes))

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

    def _focused_candidate_card(self) -> InboxCard | None:
        """Return the focused inbox card if it represents an ic_candidate."""
        focused = self.app.focused
        if not isinstance(focused, InboxCard):
            return None
        if focused.item.get("item_type") != "ic_candidate":
            return None
        return focused

    @work(exclusive=True)
    async def action_approve_candidate(self) -> None:
        """Approve the focused candidate and place the IC immediately.

        Marks the candidate row order_status='approved', then invokes the
        same order placement path the daily cron uses (place_approved_-
        candidates). On success the candidate moves to 'placed', a new
        ic_positions row appears, the inbox card is dismissed, and the
        view refreshes. On API failure the candidate stays 'approved'
        for the cron to retry.
        """
        import aiosqlite
        from kotorid.config import DB_PATH, TRADIER_API_KEY
        from kotorid.db import get_db
        from kotorid.order_placement import place_approved_candidates
        from kotorid.tradier_client import build_client, get_account_id

        card = self._focused_candidate_card()
        if not card or not TRADIER_API_KEY:
            return
        symbol = card.item["symbol"]

        # Mark the most recent pending candidate for this symbol as approved.
        # Multiple-per-symbol-per-day is ruled out by candidate_scan dedupe;
        # ORDER BY scan_date DESC is belt-and-suspenders in case a row slipped
        # through under some other path.
        await db.execute(
            """UPDATE candidates SET order_status='approved'
               WHERE id = (
                   SELECT id FROM candidates
                   WHERE symbol=? AND order_status='pending_approval'
                   ORDER BY scan_date DESC LIMIT 1
               )""",
            (symbol,),
        )

        # Place against the live broker right now (don't wait for the cron).
        async with build_client() as client:
            account_id = await get_account_id(client)
            async with get_db(DB_PATH) as conn:
                await place_approved_candidates(conn, client, account_id)

        # Trigger a refresh so the new IC + dismissed card flow into the view.
        self.refresh_data()

    @work(exclusive=True)
    async def action_reject_candidate(self) -> None:
        """Reject the focused candidate and dismiss the inbox card."""
        from datetime import datetime, timezone

        card = self._focused_candidate_card()
        if not card:
            return
        symbol = card.item["symbol"]
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        await db.execute(
            """UPDATE candidates SET order_status='rejected'
               WHERE id = (
                   SELECT id FROM candidates
                   WHERE symbol=? AND order_status='pending_approval'
                   ORDER BY scan_date DESC LIMIT 1
               )""",
            (symbol,),
        )
        await db.execute(
            """UPDATE inbox_items SET dismissed_at=?
               WHERE item_type='ic_candidate' AND symbol=? AND dismissed_at IS NULL""",
            (now_iso, symbol),
        )
        self.refresh_data()
