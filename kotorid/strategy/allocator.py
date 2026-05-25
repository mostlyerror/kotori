from __future__ import annotations
from datetime import datetime
from kotorid.signals.mesh import SignalMesh

SKIP_THRESHOLD = 0.0
MIN_SCALE = 0.5


class StrategyAllocator:
    def __init__(self, entry_threshold: float = SKIP_THRESHOLD):
        self.entry_threshold = entry_threshold

    def should_trade(self, mesh: SignalMesh, timestamp: datetime, regime: str) -> bool:
        return mesh.composite_score(timestamp, regime) > self.entry_threshold

    def position_scale(self, mesh: SignalMesh, timestamp: datetime, regime: str) -> float:
        """Scale between MIN_SCALE and 1.0 based on composite score.

        Score <= 0 → skip (handled by should_trade).
        Score 0-1 → linear interpolation from MIN_SCALE to 1.0.
        Score > 1 → capped at 1.0.
        """
        score = mesh.composite_score(timestamp, regime)
        if score <= 0:
            return 0.0
        return min(1.0, MIN_SCALE + (1.0 - MIN_SCALE) * min(score, 1.0))
