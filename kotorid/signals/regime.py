from kotorid.signals.mesh import SignalMesh

# --- Hard gates: return True to BLOCK entry ---

VIX_GATE = 45.0
HY_SPREAD_GATE = 6.0


def should_hard_gate(vix: float | None, hy_spread: float | None) -> bool:
    """True if conditions are too extreme to sell premium."""
    if vix is not None and vix >= VIX_GATE:
        return True
    if hy_spread is not None and hy_spread >= HY_SPREAD_GATE:
        return True
    return False


# --- Soft signals: feed into mesh for composite scoring ---

def update_vix_regime(mesh: SignalMesh, vix: float, timestamp) -> str:
    if vix >= 45.0:
        regime = "crisis"
        value = -1.0
    elif vix >= 35.0:
        regime = "caution"
        value = -0.5
    elif vix >= 20.0:
        regime = "normal"
        value = 0.5
    else:
        regime = "normal"
        value = 1.0
    mesh.update("vix_regime", value, half_life_days=5.0, timestamp=timestamp)
    return regime


def update_hy_spread(mesh: SignalMesh, hy_spread: float, timestamp) -> None:
    """High-yield spread: wider = more credit stress = worse for selling premium.

    OAS below 3.5% is benign, 3.5-5% is caution, above 5% is danger.
    """
    if hy_spread < 3.5:
        value = 1.0
    elif hy_spread < 4.5:
        value = 0.3
    elif hy_spread < 5.0:
        value = -0.3
    else:
        value = -0.8
    mesh.update("hy_spread", value, half_life_days=10.0, timestamp=timestamp)


def update_yield_curve(mesh: SignalMesh, spread_10y2y: float, timestamp) -> None:
    """10Y-2Y yield curve: inverted = recession signal = risk-off.

    Positive and steepening is constructive. Negative is a warning.
    """
    if spread_10y2y > 0.5:
        value = 0.8
    elif spread_10y2y > 0.0:
        value = 0.3
    elif spread_10y2y > -0.5:
        value = -0.3
    else:
        value = -0.8
    mesh.update("yield_curve", value, half_life_days=20.0, timestamp=timestamp)


def update_pead(
    mesh: SignalMesh, surprise_pct: float, days_since: int, timestamp,
) -> None:
    """Post-earnings announcement drift signal.

    Strong recent surprise → stock is trending → bad for symmetric ICs.
    Weak/old surprise → stock absorbed the move → rangebound, good for ICs.

    Signal is NEGATIVE for large surprises (trending = IC risk) and
    POSITIVE when surprise is small or old (rangebound = IC opportunity).
    """
    if days_since > 60:
        return  # too old, PEAD has faded

    magnitude = abs(surprise_pct)

    if magnitude < 2.0:
        value = 0.8   # tiny surprise, stock stays rangebound
    elif magnitude < 5.0:
        value = 0.3   # moderate surprise, some drift but manageable
    elif magnitude < 10.0:
        value = -0.3  # big surprise, stock likely trending
    else:
        value = -0.8  # huge surprise, strong directional drift

    remaining_life = max(1.0, 60.0 - days_since)
    mesh.update("pead", value, half_life_days=remaining_life / 4, timestamp=timestamp)
