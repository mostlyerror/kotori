from kotorid.strategy.config import ICConfig


def test_ic_config_defaults():
    cfg = ICConfig()
    assert cfg.target_delta == 0.16
    assert cfg.wing_width == 5.0
    assert cfg.min_dte == 5
    assert cfg.max_dte == 9
    assert cfg.min_credit_ratio == 0.20
    assert cfg.profit_target == 0.50
    assert cfg.stop_loss == 2.00


def test_ic_config_override():
    cfg = ICConfig(target_delta=0.20, wing_width=10.0)
    assert cfg.target_delta == 0.20
    assert cfg.wing_width == 10.0
    assert cfg.profit_target == 0.50
