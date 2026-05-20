from kotorid.regime_engine import get_vix_regime, get_iv_regime


def test_vix_normal():
    assert get_vix_regime(18.4) == "normal"


def test_vix_normal_boundary():
    assert get_vix_regime(34.9) == "normal"


def test_vix_caution():
    assert get_vix_regime(35.0) == "caution"


def test_vix_caution_upper():
    assert get_vix_regime(44.9) == "caution"


def test_vix_no_trade():
    assert get_vix_regime(45.0) == "no_trade"


def test_iv_regime_high():
    assert get_iv_regime(0.75) == "high"


def test_iv_regime_normal():
    assert get_iv_regime(0.45) == "normal"


def test_iv_regime_low():
    assert get_iv_regime(0.20) == "low"
