from cryptobot.backtest import simulate
from cryptobot.strategies import breakout, buy_and_hold, sma, sma_cross


def test_sma():
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


def test_fees_cost_money():
    closes = [100.0] * 10
    r = simulate(closes, [0, 1, 0, 1, 0, 1, 0, 1, 0, 0], fee=0.01, slippage=0)
    assert r.total_return < 0 and r.trades == 8


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


def test_fractional_position_scales_return_and_cost():
    closes = [100.0, 110.0]
    r = simulate(closes, [0.5, 0.5], fee=0.01, slippage=0)
    assert abs(r.total_return - ((1 + 0.05) * (1 - 0.005) - 1)) < 1e-9


def test_new_strategies_flat_in_warmup_and_bounded():
    from cryptobot.strategies import dip_buy, regime_trend, vol_trend
    closes = [100 + (i % 50) - 25 * (i % 7 == 0) for i in range(3000)]
    closes = [float(c) for c in closes]
    for pos, warm in ((vol_trend(closes, 336, 0.4), 336), (regime_trend(closes, 168, 1200), 1199),
                      (dip_buy(closes, 24, 0.03, 720), 719)):
        assert all(p == 0 for p in pos[:warm]) and all(0 <= p <= 1 for p in pos)
