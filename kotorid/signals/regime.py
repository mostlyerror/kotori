from kotorid.signals.mesh import SignalMesh

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
