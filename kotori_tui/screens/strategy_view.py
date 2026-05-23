"""Aggregate IC strategy performance view.

Pushed from BriefingView via the 'p' keybinding. Shows three sections:

1. Portfolio header — open / closed counts, realized P&L, unrealized P&L,
   win rate.
2. Open ICs table — every IC with exit_reason IS NULL, showing live
   current_debit / pct_max_profit from ic_refresh.
3. Closed ICs table — historical record of every IC that's been
   exited (profit_target / stop_loss / force_close / manual_close),
   showing realized_pnl. Empty-state copy when nothing's closed yet.

Reads aggregations via kotori_tui.db helpers; the screen itself stays
declarative (no SQL inline).
"""
from __future__ import annotations

from datetime import date

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import Label
from textual import work

import kotori_tui.db as db


def _format_strikes(ic: dict) -> str:
    """Compact strike notation: e.g. '730/735 760/765'."""
    return (
        f"{int(ic['long_put'])}/{int(ic['short_put'])} "
        f"{int(ic['short_call'])}/{int(ic['long_call'])}"
    )


def _days_between(start_iso: str, end_iso: str) -> int | None:
    try:
        return (date.fromisoformat(end_iso) - date.fromisoformat(start_iso)).days
    except (TypeError, ValueError):
        return None


class StrategyView(Screen):
    DEFAULT_CSS = """
    StrategyView { layout: vertical; padding: 1 2; }
    StrategyView VerticalScroll { height: 1fr; }
    StrategyView Container { height: auto; }
    .strategy-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .section-header { text-style: bold; color: $text-muted; margin-top: 1; }
    .stats-row { padding-bottom: 0; }
    .stat-pos { color: $success; }
    .stat-neg { color: $error; }
    .table-row { color: $text; }
    .empty-state { color: $text-muted; padding: 1 0; }
    """

    BINDINGS = [
        Binding("escape,q,p", "dismiss", "Back", show=True),
        Binding("r", "reload", "Refresh", show=True),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Strategy — Iron Condor Performance", classes="strategy-title")
            yield Container(id="stats-section")
            yield Label("OPEN POSITIONS", classes="section-header")
            yield Container(id="open-section")
            yield Label("CLOSED POSITIONS", classes="section-header")
            yield Container(id="closed-section")

    def on_mount(self) -> None:
        self.action_reload()

    @work(exclusive=True)
    async def action_reload(self) -> None:
        stats = await db.get_strategy_stats()
        open_ics = await db.get_open_ics()
        closed_ics = await db.get_closed_ics()
        self._render_stats(stats)
        self._render_open(open_ics)
        self._render_closed(closed_ics)

    def _render_stats(self, stats: dict) -> None:
        section = self.query_one("#stats-section")
        section.remove_children()
        realized = stats["total_realized_pnl"]
        unrealized = stats["unrealized_pnl"]
        wr = stats["win_rate"]
        wr_text = f"{wr:.0%}" if wr is not None else "—"
        rl_class = "stat-pos" if realized >= 0 else "stat-neg"
        un_class = "stat-pos" if unrealized >= 0 else "stat-neg"
        section.mount(
            Label(
                f"Open: {stats['open_count']}   Closed: {stats['closed_count']}   "
                f"Win rate: {wr_text} ({stats['pnl_known_count']} settled)",
                classes="stats-row",
            ),
            Label(
                f"Realized P&L: ${realized:+,.0f}", classes=f"stats-row {rl_class}",
            ),
            Label(
                f"Unrealized P&L: ${unrealized:+,.0f}", classes=f"stats-row {un_class}",
            ),
        )

    def _render_open(self, open_ics: list[dict]) -> None:
        section = self.query_one("#open-section")
        section.remove_children()
        if not open_ics:
            section.mount(Label("(no open positions)", classes="empty-state"))
            return
        today = date.today().isoformat()
        # Header
        section.mount(Label(
            f"{'Symbol':<7}{'Expiry':<13}{'Strikes':<18}"
            f"{'Credit':>8}{'Debit':>8}{'P&L%':>7}{'DTE':>5}",
            classes="section-header",
        ))
        for ic in open_ics:
            dte = _days_between(today, ic["expiry"])
            dte_text = str(dte) if dte is not None else "?"
            credit = float(ic["entry_credit"] or 0)
            debit = ic["current_debit"]
            debit_text = f"${debit:.2f}" if debit is not None else "—"
            pct = ic["pct_max_profit"]
            pct_text = f"{pct*100:+.0f}%" if pct is not None else "—"
            line = (
                f"{ic['symbol']:<7}{ic['expiry']:<13}"
                f"{_format_strikes(ic):<18}"
                f"${credit:>7.2f}{debit_text:>8}{pct_text:>7}{dte_text:>5}"
            )
            classes = "table-row"
            if pct is not None:
                classes += " stat-pos" if pct >= 0 else " stat-neg"
            section.mount(Label(line, classes=classes))

    def _render_closed(self, closed_ics: list[dict]) -> None:
        section = self.query_one("#closed-section")
        section.remove_children()
        if not closed_ics:
            section.mount(Label(
                "(no closed positions yet — first IC closes the day after expiry)",
                classes="empty-state",
            ))
            return
        section.mount(Label(
            f"{'Symbol':<7}{'Expiry':<13}{'Strikes':<18}"
            f"{'Reason':<14}{'Credit':>8}{'Debit':>8}{'P&L $':>9}",
            classes="section-header",
        ))
        for ic in closed_ics:
            credit = float(ic["entry_credit"] or 0)
            debit = ic["exit_debit"]
            debit_text = f"${debit:.2f}" if debit is not None else "—"
            pnl = ic["realized_pnl"]
            pnl_text = f"${pnl:+,.0f}" if pnl is not None else "?"
            line = (
                f"{ic['symbol']:<7}{ic['expiry']:<13}"
                f"{_format_strikes(ic):<18}"
                f"{(ic['exit_reason'] or '?'):<14}"
                f"${credit:>7.2f}{debit_text:>8}{pnl_text:>9}"
            )
            classes = "table-row"
            if pnl is not None:
                classes += " stat-pos" if pnl >= 0 else " stat-neg"
            section.mount(Label(line, classes=classes))
