from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label
from textual import work
import os
import kotori_tui.db as db


class StatusBar(Widget):
    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text-muted;
        layout: horizontal;
    }
    StatusBar Label { padding: 0 1; }
    StatusBar .sandbox { color: $warning; }
    StatusBar .alert-badge { color: $error; }
    StatusBar .regime-normal { color: $success; }
    StatusBar .regime-caution { color: $warning; }
    StatusBar .regime-no_trade { color: $error; }
    StatusBar .spacer { width: 1fr; }
    StatusBar .keys { color: $text-muted; }
    """

    nav: reactive[float] = reactive(0.0)
    pnl: reactive[float] = reactive(0.0)
    vix: reactive[float] = reactive(0.0)
    regime: reactive[str] = reactive("—")
    alerts: reactive[int] = reactive(0)
    sandbox: reactive[bool] = reactive(True)

    def compose(self) -> ComposeResult:
        yield Label("", id="sb-sandbox", classes="sandbox")
        yield Label("", id="sb-nav")
        yield Label("", id="sb-pnl")
        yield Label("", id="sb-vix")
        yield Label("", id="sb-regime")
        yield Label("", id="sb-alerts", classes="alert-badge")
        yield Label("", id="sb-spacer", classes="spacer")
        yield Label("↵:detail a:approve r:reject p:strategy q:quit", id="sb-keys", classes="keys")

    def on_mount(self) -> None:
        self.set_interval(2, self.refresh_stats)

    @work(exclusive=True)
    async def refresh_stats(self) -> None:
        self.nav = await db.get_nav()
        self.pnl = await db.get_today_pnl()
        self.vix = await db.get_vix()
        self.regime = await db.get_market_regime()
        self.alerts = await db.get_unread_alert_count()
        self.sandbox = os.environ.get("TRADIER_ENV", "sandbox") == "sandbox"

    def watch_nav(self, val: float) -> None:
        self.query_one("#sb-nav", Label).update(f"NAV ${val:,.0f}")

    def watch_pnl(self, val: float) -> None:
        sign = "+" if val >= 0 else ""
        self.query_one("#sb-pnl", Label).update(f"{sign}${val:,.0f} today")

    def watch_vix(self, val: float) -> None:
        self.query_one("#sb-vix", Label).update(f"VIX {val:.1f}")

    def watch_regime(self, val: str) -> None:
        label = self.query_one("#sb-regime", Label)
        label.update(val)
        label.set_class(val == "normal", "regime-normal")
        label.set_class(val == "caution", "regime-caution")
        label.set_class(val == "no_trade", "regime-no_trade")

    def watch_alerts(self, val: int) -> None:
        self.query_one("#sb-alerts", Label).update(
            f"⚡ {val} alerts" if val > 0 else ""
        )

    def watch_sandbox(self, val: bool) -> None:
        self.query_one("#sb-sandbox", Label).update("[SANDBOX]" if val else "")
