from textual.app import App, ComposeResult
from textual.binding import Binding
from portfolio_tui.widgets.status_bar import StatusBar


class TraderApp(App):
    TITLE = "The Trader"
    BINDINGS = [
        Binding("i", "show_view('inbox')", "Inbox", show=True),
        Binding("g", "show_view('grid')", "Grid", show=True),
        Binding("k", "show_view('kanban')", "Kanban", show=True),
        Binding("b", "show_view('briefing')", "Briefing", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    CSS = """
    ContentSwitcher { height: 1fr; }
    """

    _current_view: str = "inbox"

    def compose(self) -> ComposeResult:
        from textual.widgets import ContentSwitcher
        from portfolio_tui.views.inbox_view import InboxView
        from portfolio_tui.views.grid_view import GridView
        from portfolio_tui.views.kanban_view import KanbanView
        from portfolio_tui.views.briefing_view import BriefingView

        with ContentSwitcher(initial="inbox"):
            yield InboxView(id="inbox")
            yield GridView(id="grid")
            yield KanbanView(id="kanban")
            yield BriefingView(id="briefing")
        yield StatusBar()

    def action_show_view(self, view_id: str) -> None:
        self.query_one("ContentSwitcher").current = view_id
        self._current_view = view_id

    def open_position_detail(self, symbol: str) -> None:
        from portfolio_tui.screens.position_detail import PositionDetail
        self.push_screen(PositionDetail(symbol))
