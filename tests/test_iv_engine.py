import pytest
from portfoliod.iv_engine import compute_iv_rank, compute_iv_percentile


def test_iv_rank_mid_range():
    history = [0.20, 0.25, 0.30, 0.35, 0.40]
    assert compute_iv_rank(0.35, history) == pytest.approx(0.75)


def test_iv_rank_at_max():
    history = [0.20, 0.30, 0.40]
    assert compute_iv_rank(0.40, history) == pytest.approx(1.0)


def test_iv_rank_at_min():
    history = [0.20, 0.30, 0.40]
    assert compute_iv_rank(0.20, history) == pytest.approx(0.0)


def test_iv_rank_flat_history_returns_zero():
    assert compute_iv_rank(0.30, [0.30, 0.30, 0.30]) == 0.0


def test_iv_rank_empty_raises():
    with pytest.raises(ValueError, match="iv_history cannot be empty"):
        compute_iv_rank(0.30, [])


def test_iv_percentile_basic():
    history = [0.20, 0.25, 0.30, 0.35, 0.40]
    # 3 values strictly below 0.32: 0.20, 0.25, 0.30
    assert compute_iv_percentile(0.32, history) == pytest.approx(0.60)


def test_iv_percentile_above_all():
    history = [0.20, 0.25, 0.30]
    assert compute_iv_percentile(0.50, history) == pytest.approx(1.0)


def test_iv_percentile_below_all():
    history = [0.20, 0.25, 0.30]
    assert compute_iv_percentile(0.10, history) == pytest.approx(0.0)


def test_iv_percentile_empty_raises():
    with pytest.raises(ValueError, match="iv_history cannot be empty"):
        compute_iv_percentile(0.30, [])
