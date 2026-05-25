from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    symbol: str
    entry_credit: float
    max_loss: float
    contracts: int
    legs: dict
    opened_at: datetime
    current_debit: float | None = None


@dataclass
class TradeRecord:
    symbol: str
    entry_credit: float
    exit_debit: float
    contracts: int
    realized_pnl: float
    reason: str
    opened_at: datetime
    closed_at: datetime


class Portfolio:
    def __init__(self, initial_cash: float = 100_000.0):
        self.cash: float = initial_cash
        self.initial_cash: float = initial_cash
        self.positions: dict[str, Position] = {}
        self.trade_log: list[TradeRecord] = []
        self.equity_curve: list[tuple[datetime, float]] = []
        self._peak: float = initial_cash

    def open_position(self, symbol: str, entry_credit: float, max_loss: float,
                      contracts: int, legs: dict, timestamp: datetime) -> None:
        self.positions[symbol] = Position(
            symbol=symbol, entry_credit=entry_credit,
            max_loss=max_loss, contracts=contracts, legs=legs, opened_at=timestamp,
        )
        self.cash += entry_credit * 100 * contracts

    def close_position(self, symbol: str, exit_debit: float, reason: str,
                       timestamp: datetime) -> TradeRecord:
        pos = self.positions.pop(symbol)
        cost = exit_debit * 100 * pos.contracts
        self.cash -= cost
        pnl = (pos.entry_credit - exit_debit) * 100 * pos.contracts
        record = TradeRecord(
            symbol=pos.symbol, entry_credit=pos.entry_credit,
            exit_debit=exit_debit, contracts=pos.contracts, realized_pnl=pnl,
            reason=reason, opened_at=pos.opened_at, closed_at=timestamp,
        )
        self.trade_log.append(record)
        return record

    def record_equity(self, timestamp: datetime) -> None:
        equity = self.cash
        self.equity_curve.append((timestamp, equity))
        if equity > self._peak:
            self._peak = equity

    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0][1]
        max_dd = 0.0
        for _, equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return max_dd
