"""Tests for HY spread, yield curve signals, and hard gates."""
from datetime import datetime

from kotorid.signals.mesh import SignalMesh
from kotorid.signals.regime import (
    should_hard_gate,
    update_hy_spread,
    update_vix_regime,
    update_yield_curve,
)


def test_hard_gate_vix_extreme():
    assert should_hard_gate(vix=50.0, hy_spread=3.0)


def test_hard_gate_hy_extreme():
    assert should_hard_gate(vix=20.0, hy_spread=6.5)


def test_hard_gate_both_normal():
    assert not should_hard_gate(vix=20.0, hy_spread=3.0)


def test_hard_gate_none_values():
    assert not should_hard_gate(vix=None, hy_spread=None)
    assert should_hard_gate(vix=50.0, hy_spread=None)
    assert should_hard_gate(vix=None, hy_spread=7.0)


def test_hy_spread_benign():
    mesh = SignalMesh()
    ts = datetime(2024, 6, 1)
    update_hy_spread(mesh, 3.0, ts)
    score = mesh.composite_score(ts, "normal")
    assert score > 0


def test_hy_spread_danger():
    mesh = SignalMesh()
    ts = datetime(2024, 6, 1)
    update_hy_spread(mesh, 5.5, ts)
    score = mesh.composite_score(ts, "normal")
    assert score < 0


def test_yield_curve_positive():
    mesh = SignalMesh()
    ts = datetime(2024, 6, 1)
    update_yield_curve(mesh, 1.0, ts)
    score = mesh.composite_score(ts, "normal")
    assert score > 0


def test_yield_curve_inverted():
    mesh = SignalMesh()
    ts = datetime(2024, 6, 1)
    update_yield_curve(mesh, -1.0, ts)
    score = mesh.composite_score(ts, "normal")
    assert score < 0


def test_full_mesh_all_signals():
    mesh = SignalMesh()
    ts = datetime(2024, 6, 1)
    update_vix_regime(mesh, 18.0, ts)  # low VIX = bullish
    update_hy_spread(mesh, 3.0, ts)     # tight = bullish
    update_yield_curve(mesh, 0.8, ts)   # positive = bullish
    score = mesh.composite_score(ts, "normal")
    assert score > 0.5  # all signals aligned bullish


def test_full_mesh_mixed_signals():
    mesh = SignalMesh()
    ts = datetime(2024, 6, 1)
    update_vix_regime(mesh, 18.0, ts)   # bullish
    update_hy_spread(mesh, 5.2, ts)     # bearish
    update_yield_curve(mesh, -0.3, ts)  # bearish
    score = mesh.composite_score(ts, "normal")
    assert -0.5 < score < 0.5  # mixed signals = near zero
