from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    commission_per_contract: float = 0.65
    slippage_pct: float = 0.0
