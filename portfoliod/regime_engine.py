def get_vix_regime(vix: float) -> str:
    if vix >= 45.0:
        return "no_trade"
    if vix >= 35.0:
        return "caution"
    return "normal"


def get_iv_regime(iv_rank: float) -> str:
    if iv_rank >= 0.50:
        return "high"
    if iv_rank >= 0.25:
        return "normal"
    return "low"
