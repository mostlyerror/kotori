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

    items: reactive[list | None] = reactive(None)
    _loaded: bool = False

    def compose(self) -> ComposeResult:
        with Widget(id="inbox-content"):
            yield Label("Loading...", id="inbox-loading")

    def on_mount(self) -> None:
        self.refresh_items()
        self.set_interval(2, self.refresh_items)

    @work(exclusive=True)
    async def refresh_items(self) -> None:
        items = await db.query(
            "SELECT * FROM inbox_items WHERE dismissed_at IS NULL "
            "ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'action_required' THEN 1 ELSE 2 END, created_at"
        )
        container = self.query_one("#inbox-content")
        await container.remove_children()

        if not items:
            await container.mount(InboxZero())
            self.items = items
            return

        urgent = [i for i in items if i["priority"] == "urgent"]
        action = [i for i in items if i["priority"] == "action_required"]
        review = [i for i in items if i["priority"] == "for_review"]

        for header, group in [("URGENT", urgent), ("ACTION REQUIRED", action), ("FOR REVIEW", review)]:
            if group:
                await container.mount(Label(header, classes="section-header"))
                for item in group:
                    await container.mount(InboxItemCard(item))

        self.items = items

    def watch_items(self, items: list | None) -> None:
        pass
