from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static
from textual.binding import Binding
from textual import work
import portfolio_tui.db as db

LANES = [
    ("watching", "WATCHING"),
    ("candidate", "CANDIDATE"),
    ("open", "OPEN IC"),
    ("closing", "CLOSING"),
    ("closed", "CLOSED"),
]


class KanbanCard(Static):
    def __init__(self, card: dict, **kwargs):
        super().__init__(**kwargs)
        self.card = card
        self.can_focus = True

    def compose(self) -> ComposeResult:
        sym = self.card.get("symbol", "—")
        metric = self.card.get("metric", "")
        status = self.card.get("status", "")
        yield Label(f"{sym}", classes="card-sym")
        yield Label(metric, classes="card-metric")
        if status:
            yield Label(status, classes=f"card-status card-{status}")


class KanbanLane(Widget):
    DEFAULT_CSS = """
    KanbanLane { width: 1fr; border: solid $panel-lighten-1; padding: 0 1; margin: 0 1; }
    .lane-header { text-style: bold; color: $text-muted; border-bottom: solid $panel-lighten-2; margin-bottom: 1; }
    KanbanCard { border: solid $panel-lighten-2; padding: 1; margin-bottom: 1; }
    KanbanCard:focus { border: solid $accent; }
    .card-sym { text-style: bold; }
    .card-metric { color: $success; }
    .card-status { color: $text-muted; }
    """

    def __init__(self, lane_id: str, title: str, **kwargs):
        super().__init__(**kwargs)
        self.lane_id = lane_id
        self.lane_title = title

    def compose(self) -> ComposeResult:
        yield Label(f"{self.lane_title} (0)", id=f"lane-header-{self.lane_id}", classes="lane-header")
        yield Widget(id=f"lane-cards-{self.lane_id}")

    def update_cards(self, cards: list) -> None:
        self.query_one(f"#lane-header-{self.lane_id}", Label).update(
            f"{self.lane_title} ({len(cards)})"
        )
        container = self.query_one(f"#lane-cards-{self.lane_id}")
        container.remove_children()
        for c in cards:
            container.mount(KanbanCard(c, id=f"card-{c['id']}"))


class KanbanView(Widget):
    DEFAULT_CSS = """
    KanbanView { layout: horizontal; padding: 1; }
    """

    BINDINGS = [
        Binding("enter", "open_detail", "Detail", show=True),
        Binding("right", "approve_candidate", "Approve →", show=True),
        Binding("left", "reject_candidate", "Reject ←", show=True),
    ]

    def compose(self) -> ComposeResult:
        for lane_id, title in LANES:
            yield KanbanLane(lane_id, title, id=f"lane-{lane_id}")

    def on_mount(self) -> None:
        self.set_interval(3, self.refresh_cards)

    @work(exclusive=True)
    async def refresh_cards(self) -> None:
        positions = await db.query(
            "SELECT p.symbol, p.unrealized_pnl_pct, COALESCE(t.status,'intact') as status "
            "FROM positions p LEFT JOIN thesis t ON p.symbol=t.symbol "
            "WHERE p.instrument_type='stock'"
        )
        open_ics = await db.query(
            "SELECT id, symbol, pct_max_profit, entry_credit, current_debit "
            "FROM ic_positions WHERE exit_reason IS NULL"
        )
        candidates = await db.query(
            "SELECT id, symbol, expected_credit, contracts "
            "FROM candidates WHERE order_status IN ('pending_approval','approved')"
        )
        closed_ics = await db.query(
            "SELECT id, symbol, realized_pnl FROM ic_positions "
            "WHERE exit_reason IS NOT NULL ORDER BY rowid DESC LIMIT 5"
        )
        self.call_from_thread(self._update_lanes, positions, open_ics, candidates, closed_ics)

    def _update_lanes(self, positions, open_ics, candidates, closed_ics) -> None:
        watching = [{"id": f"w-{p['symbol']}", "symbol": p["symbol"],
                     "metric": f"{p['unrealized_pnl_pct']:+.1%}",
                     "status": p["status"]} for p in positions]
        candidate_cards = [{"id": c["id"], "symbol": c["symbol"],
                            "metric": f"cr ${c['expected_credit']:.2f} ×{c['contracts']}",
                            "status": "pending"} for c in candidates]
        open_cards = [{"id": ic["id"], "symbol": ic["symbol"],
                       "metric": f"{(ic['pct_max_profit'] or 0):.0%} profit",
                       "status": "active"} for ic in open_ics]
        closed_cards = [{"id": ic["id"], "symbol": ic["symbol"],
                         "metric": f"${ic['realized_pnl']:+.0f}" if ic["realized_pnl"] is not None else "$0",
                         "status": "closed"} for ic in closed_ics]

        self.query_one("#lane-watching", KanbanLane).update_cards(watching)
        self.query_one("#lane-candidate", KanbanLane).update_cards(candidate_cards)
        self.query_one("#lane-open", KanbanLane).update_cards(open_cards)
        self.query_one("#lane-closing", KanbanLane).update_cards([])
        self.query_one("#lane-closed", KanbanLane).update_cards(closed_cards)

    async def action_approve_candidate(self) -> None:
        focused = self.app.focused
        if isinstance(focused, KanbanCard) and focused.card.get("status") == "pending":
            await db.execute(
                "UPDATE candidates SET order_status='approved' WHERE id=?",
                (focused.card["id"],)
            )

    async def action_reject_candidate(self) -> None:
        focused = self.app.focused
        if isinstance(focused, KanbanCard) and focused.card.get("status") == "pending":
            await db.execute(
                "UPDATE candidates SET order_status='rejected' WHERE id=?",
                (focused.card["id"],)
            )

    def action_open_detail(self) -> None:
        focused = self.app.focused
        if isinstance(focused, KanbanCard):
            self.app.open_position_detail(focused.card["symbol"])
