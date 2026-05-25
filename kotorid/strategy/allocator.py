from __future__ import annotations
from datetime import datetime
from kotorid.signals.mesh import SignalMesh

class StrategyAllocator:
    def __init__(self, entry_threshold: float = 0.0):
        self.entry_threshold = entry_threshold

    def should_trade(self, mesh: SignalMesh, timestamp: datetime, regime: str) -> bool:
        return mesh.composite_score(timestamp, regime) > self.entry_threshold

    def position_scale(self, mesh: SignalMesh, timestamp: datetime, regime: str) -> float:
        score = mesh.composite_score(timestamp, regime)
        return max(0.0, min(1.0, score))
