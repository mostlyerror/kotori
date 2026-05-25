from textual.app import App, ComposeResult
from textual.binding import Binding
from kotori_tui.widgets.status_bar import StatusBar


class KotoriApp(App):
    TITLE = "🐦 kotori"
    BINDINGS = [
        Binding("e", "open_earnings", "Earnings", show=True),
        Binding("p", "open_strategy", "Strategy", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    CSS = """
    BriefingView { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        from kotori_tui.views.briefing_view import BriefingView
        yield BriefingView(id="briefing")
        yield StatusBar()

    def action_open_earnings(self) -> None:
        from kotori_tui.screens.earnings_view import EarningsView
        self.push_screen(EarningsView())

    def action_open_strategy(self) -> None:
        from kotori_tui.screens.strategy_view import StrategyView
        self.push_screen(StrategyView())

    def open_position_detail(self, symbol: str) -> None:
        from kotori_tui.screens.position_detail import PositionDetail
        self.push_screen(PositionDetail(symbol))
