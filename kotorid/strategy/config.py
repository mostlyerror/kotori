from dataclasses import dataclass


@dataclass(frozen=True)
class ICConfig:
    target_delta: float = 0.16
    wing_width: float = 5.0
    min_dte: int = 5
    max_dte: int = 9
    min_credit_ratio: float = 0.20
    profit_target: float = 0.50
    stop_loss: float = 2.00
