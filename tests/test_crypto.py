from cryptobot.backtest import simulate
from cryptobot.strategies import breakout, buy_and_hold, sma, sma_cross


def test_sma():
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


def test_fees_cost_money():
    closes = [100.0] * 10
    r = simulate(closes, [0, 1, 0, 1, 0, 1, 0, 1, 0, 0], fee=0.01, slippage=0)
    assert r.total_return < 0 and r.trades == 7


def test_no_lookahead():
    # position set on bar i must earn bar i+1's return, not bar i's
    closes = [100.0, 200.0, 200.0]
    assert abs(simulate(closes, [1, 0, 0], fee=0, slippage=0).total_return - 1.0) < 1e-9
    assert simulate(closes, [0, 1, 0], fee=0, slippage=0).total_return == 0.0


def test_buy_and_hold_matches_price():
    closes = [100.0, 110.0, 121.0]
    r = simulate(closes, buy_and_hold(closes), fee=0, slippage=0)
    assert abs(r.total_return - 0.21) < 1e-9


def test_signals_flat_during_warmup():
    closes = [float(i) for i in range(50)]
    assert sma_cross(closes, 3, 20)[:19] == [0] * 19
    assert breakout(closes, 10, 5)[:10] == [0] * 10
