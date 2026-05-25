import pytest
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from kotorid.signals.mesh import SignalMesh

ET = ZoneInfo("America/New_York")
T0 = datetime(2024, 1, 2, 10, 0, tzinfo=ET)

def test_update_and_read():
    mesh = SignalMesh()
    mesh.update("vix_regime", value=1.0, half_life_days=5.0, timestamp=T0)
    score = mesh.composite_score(T0, regime="normal")
    assert score > 0

def test_decay():
    mesh = SignalMesh()
    mesh.update("vix_regime", value=1.0, half_life_days=5.0, timestamp=T0)
    score_now = mesh.composite_score(T0, regime="normal")
    score_later = mesh.composite_score(T0 + timedelta(days=5), regime="normal")
    assert score_later == pytest.approx(score_now * 0.5, rel=0.01)

def test_multiple_signals_sum():
    mesh = SignalMesh()
    mesh.update("vix_regime", value=1.0, half_life_days=5.0, timestamp=T0)
    mesh.update("iv_rank", value=1.0, half_life_days=10.0, timestamp=T0)
    single = SignalMesh()
    single.update("vix_regime", value=1.0, half_life_days=5.0, timestamp=T0)
    assert mesh.composite_score(T0, regime="normal") > single.composite_score(T0, regime="normal")

def test_rejects_nan():
    mesh = SignalMesh()
    with pytest.raises(ValueError):
        mesh.update("bad", value=float("nan"), half_life_days=5.0, timestamp=T0)

def test_rejects_inf():
    mesh = SignalMesh()
    with pytest.raises(ValueError):
        mesh.update("bad", value=float("inf"), half_life_days=5.0, timestamp=T0)

def test_regime_conditional_weights():
    mesh = SignalMesh(weights={
        "normal": {"vix_regime": 0.3, "iv_rank": 0.6},
        "crisis": {"vix_regime": 0.9, "iv_rank": 0.1},
    })
    mesh.update("vix_regime", 1.0, 5.0, T0)
    mesh.update("iv_rank", 1.0, 10.0, T0)
    score_normal = mesh.composite_score(T0, regime="normal")
    score_crisis = mesh.composite_score(T0, regime="crisis")
    assert score_crisis != score_normal

def test_empty_mesh_returns_zero():
    mesh = SignalMesh()
    assert mesh.composite_score(T0, regime="normal") == 0.0
