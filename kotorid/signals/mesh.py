from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime

DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "normal": {"vix_regime": 0.25, "hy_spread": 0.20, "yield_curve": 0.15, "pead": 0.20, "iv_rank": 0.20},
    "caution": {"vix_regime": 0.35, "hy_spread": 0.25, "yield_curve": 0.15, "pead": 0.15, "iv_rank": 0.10},
    "crisis": {"vix_regime": 0.45, "hy_spread": 0.30, "yield_curve": 0.10, "pead": 0.10, "iv_rank": 0.05},
}

@dataclass
class SignalEntry:
    value: float
    half_life_days: float
    fired_at: datetime

class SignalMesh:
    def __init__(self, weights: dict[str, dict[str, float]] | None = None):
        self.weights = weights or DEFAULT_WEIGHTS
        self._signals: dict[str, SignalEntry] = {}

    def update(self, name: str, value: float, half_life_days: float, timestamp: datetime) -> None:
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"Signal '{name}' value must be finite, got {value}")
        if half_life_days <= 0:
            raise ValueError(f"half_life_days must be positive, got {half_life_days}")
        self._signals[name] = SignalEntry(value, half_life_days, timestamp)

    def composite_score(self, as_of: datetime, regime: str = "normal") -> float:
        regime_weights = self.weights.get(regime, {})
        total = 0.0
        for name, entry in self._signals.items():
            weight = regime_weights.get(name, 0.0)
            if weight == 0:
                continue
            elapsed_days = (as_of - entry.fired_at).total_seconds() / 86400
            decay = math.exp(-elapsed_days * math.log(2) / entry.half_life_days)
            total += entry.value * weight * decay
        return total

    def active_signals(self) -> dict[str, SignalEntry]:
        return dict(self._signals)
