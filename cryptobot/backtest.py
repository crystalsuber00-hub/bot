"""Backtest with fees and slippage, plus an out-of-sample check.

Parameters are chosen on the first part of the data only and then scored on the last part,
which the search never saw. Judge a strategy by the out-of-sample numbers, not the in-sample ones.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from .strategies import STRATEGIES, buy_and_hold

FEE = 0.004       # per side; roughly Coinbase retail taker tier. Lower it only if you really get lower fees.
SLIPPAGE = 0.0005  # per side


@dataclass
class Result:
    total_return: float
    max_drawdown: float
    sharpe: float
    trades: int
    exposure: float


def simulate(closes: list[float], positions: list[int], fee: float = FEE,
             slippage: float = SLIPPAGE, bars_per_year: float = 24 * 365) -> Result:
    """Target set at bar i's close is held over bar i+1 (no look-ahead)."""
    equity, peak, max_dd = 1.0, 1.0, 0.0
    rets: list[float] = []
    held, trades, in_mkt = 0.0, 0, 0.0
    for i in range(len(closes) - 1):
        target = positions[i]  # fraction of equity, 0..1
        cost = 0.0
        if target != held:
            cost = (fee + slippage) * abs(target - held)
            trades += 1
            held = target
        r = (closes[i + 1] / closes[i] - 1) * held
        in_mkt += held
        net = (1 + r) * (1 - cost) - 1
        equity *= 1 + net
        rets.append(net)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in rets) / n) if n else 0.0
    sharpe = mean / sd * math.sqrt(bars_per_year) if sd else 0.0
    return Result(equity - 1, max_dd, sharpe, trades, in_mkt / n if n else 0.0)


def fmt(r: Result) -> str:
    return (f"return {r.total_return:+8.1%}  maxDD {r.max_drawdown:6.1%}  "
            f"sharpe {r.sharpe:5.2f}  trades {r.trades:4d}  exposure {r.exposure:4.0%}")


def run(closes: list[float], train_frac: float = 0.6) -> None:
    split = int(len(closes) * train_frac)
    test_closes = closes[split:]
    print(f"{len(closes)} bars; train {split}, test {len(test_closes)} (out-of-sample)")
    bh = simulate(test_closes, buy_and_hold(test_closes))
    print(f"\nOUT-OF-SAMPLE buy & hold:  {fmt(bh)}")
    for name, (fn, grid) in STRATEGIES.items():
        keys = list(grid)
        best = None
        for combo in itertools.product(*grid.values()):
            params = dict(zip(keys, combo))
            if name == "sma_cross" and params["fast"] >= params["slow"]:
                continue
            train = closes[:split]
            res = simulate(train, fn(train, **params))
            if best is None or res.sharpe > best[1].sharpe:
                best = (params, res)
        params, train_res = best
        # signals are warmed up on the training bars so the test period starts with real signals
        oos = simulate(test_closes, fn(closes, **params)[split:])
        print(f"\n{name} best on train: {params}")
        print(f"  in-sample:      {fmt(train_res)}")
        print(f"  OUT-OF-SAMPLE:  {fmt(oos)}")
        verdict = "beats" if oos.total_return > bh.total_return else "does NOT beat"
        print(f"  -> {verdict} buy & hold on return; "
              f"drawdown {oos.max_drawdown:.0%} vs {bh.max_drawdown:.0%}")
