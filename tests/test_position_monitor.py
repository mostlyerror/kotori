import pytest
from portfoliod.position_monitor import check_exit_trigger, compute_exit_debit

def test_profit_target_hit():
    assert check_exit_trigger(entry_credit=1.85, exit_debit=0.92) == "profit_target"

def test_profit_target_exact():
    assert check_exit_trigger(entry_credit=2.00, exit_debit=1.00) == "profit_target"

def test_stop_loss_hit():
    assert check_exit_trigger(entry_credit=1.85, exit_debit=3.70) == "stop_loss"

def test_stop_loss_exact():
    assert check_exit_trigger(entry_credit=1.00, exit_debit=2.00) == "stop_loss"

def test_no_trigger():
    assert check_exit_trigger(entry_credit=1.85, exit_debit=1.20) is None

def test_compute_exit_debit_all_worthless():
    debit = compute_exit_debit(
        sc_bid=0.01, sc_ask=0.02,
        sp_bid=0.01, sp_ask=0.02,
        lc_bid=0.00, lc_ask=0.01,
        lp_bid=0.00, lp_ask=0.01,
    )
    # mid prices: SC=0.015, SP=0.015, LC=0.005, LP=0.005
    # debit = (SC_mid + SP_mid) - (LC_mid + LP_mid) = 0.03 - 0.01 = 0.02
    assert debit == pytest.approx(0.02)

def test_compute_exit_debit_in_the_money():
    debit = compute_exit_debit(
        sc_bid=3.80, sc_ask=4.00,
        sp_bid=0.01, sp_ask=0.02,
        lc_bid=2.90, lc_ask=3.10,
        lp_bid=0.00, lp_ask=0.01,
    )
    # SC_mid=3.90, SP_mid=0.015, LC_mid=3.00, LP_mid=0.005
    # debit = (3.90 + 0.015) - (3.00 + 0.005) = 3.915 - 3.005 = 0.91
    assert debit == pytest.approx(0.91)
