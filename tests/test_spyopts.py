from spyopts.backtest import STRATS, bs, trade


def test_put_call_parity_zero_rates():
    S, K, T, v = 100.0, 105.0, 0.1, 0.2
    assert abs(bs(S, K, T, v, "C") - bs(S, K, T, v, "P") - (S - K)) < 1e-9


def test_iron_condor_loss_capped_by_wing():
    # a 30% crash: loss can't exceed wing width minus credit plus costs
    pnl, risk = trade(STRATS["iron_condor"], 100.0, 70.0, 30 / 365, 20, 1.0, 1.0)
    assert pnl < 0 and abs(pnl) <= risk + 1e-9


def test_long_straddle_profits_on_big_move_and_loses_when_flat():
    assert trade(STRATS["long_straddle"], 100.0, 120.0, 30 / 365, 15, 1.0, 1.0)[0] > 0
    pnl, risk = trade(STRATS["long_straddle"], 100.0, 100.0, 30 / 365, 15, 1.0, 1.0)
    assert pnl < 0 and abs(pnl) <= risk + 1e-9
