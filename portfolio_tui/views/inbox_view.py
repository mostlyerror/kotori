from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static
from textual import work
from textual.binding import Binding
import portfolio_tui.db as db


class InboxItemCard(Static):
    PRIORITY_ICON = {"urgent": "🔴", "action_required": "🟡", "for_review": "🔵"}

    def __init__(self, item: dict, **kwargs):
        super().__init__(**kwargs)
        self.item = item

    def compose(self) -> ComposeResult:
        import json
        icon = self.PRIORITY_ICON.get(self.item["priority"], "⚪")
        actions = json.loads(self.item["actions"])
        action_hints = "  ".join(f"[{a[0]}] {a.replace('_',' ')}" for a in actions)
        yield Label(f"{icon}  {self.item['title']}", classes="card-title")
        yield Label(self.item["body"], classes="card-body")
        yield Label(action_hints, classes="card-actions")


class InboxZero(Static):
    def compose(self) -> ComposeResult:
        yield Label("✓  Inbox zero. Portfolio running autonomously.", classes="zero-title")


class InboxView(Widget):
    DEFAULT_CSS = """
    InboxView { layout: vertical; padding: 1 2; }
    .section-header { color: $text-muted; text-style: bold; margin-top: 1; }
    InboxItemCard {
        border: solid $panel-lighten-1;
        padding: 1;
        margin-bottom: 1;
    }
    InboxItemCard:focus { border: solid $accent; }
    .card-title { text-style: bold; }
    .card-body { color: $text-muted; }
    .card-actions { color: $accent; margin-top: 1; }
    InboxZero { align: center middle; height: 1fr; }
    .zero-title { color: $success; text-style: bold; }
    """

    BINDINGS = [
        Binding("j,down", "focus_next", "Next", show=False),
        Binding("k,up", "focus_previous", "Prev", show=False),
    ]

    items: reactive[list] = reactive([], always_update=True)

    def compose(self) -> ComposeResult:
        yield Label("Loading...", id="inbox-content")

    def on_mount(self) -> None:
        self.set_interval(2, self.refresh_items)

    @work(exclusive=True)
    async def refresh_items(self) -> None:
        self.items = await db.query(
            "SELECT * FROM inbox_items WHERE dismissed_at IS NULL "
            "ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'action_required' THEN 1 ELSE 2 END, created_at"
        )

    def watch_items(self, items: list) -> None:
        try:
            self.query_one("#inbox-content").remove()
        except Exception:
            pass

        if not items:
            self.mount(InboxZero(id="inbox-content"))
            return

        urgent = [i for i in items if i["priority"] == "urgent"]
        action = [i for i in items if i["priority"] == "action_required"]
        review = [i for i in items if i["priority"] == "for_review"]

        children = []
        for header, group in [("URGENT", urgent), ("ACTION REQUIRED", action), ("FOR REVIEW", review)]:
            if group:
                children.append(Label(header, classes="section-header"))
                children.extend(
                    InboxItemCard(item, id=f"item-{item['id']}") for item in group
                )

        self.mount(Widget(*children, id="inbox-content"))
