def check_exit_trigger(entry_credit: float, exit_debit: float) -> str | None:
    if exit_debit <= entry_credit * 0.50:
        return "profit_target"
    if exit_debit >= entry_credit * 2.00:
        return "stop_loss"
    return None


def compute_exit_debit(
    sc_bid: float, sc_ask: float,
    sp_bid: float, sp_ask: float,
    lc_bid: float, lc_ask: float,
    lp_bid: float, lp_ask: float,
) -> float:
    sc_mid = (sc_bid + sc_ask) / 2
    sp_mid = (sp_bid + sp_ask) / 2
    lc_mid = (lc_bid + lc_ask) / 2
    lp_mid = (lp_bid + lp_ask) / 2
    return (sc_mid + sp_mid) - (lc_mid + lp_mid)
