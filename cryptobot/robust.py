"""Robustness check: fixed parameters, non-overlapping windows, several coins.

Nothing is tuned here. A real edge should win in most windows and coins, and should survive
small parameter changes; a lucky fit wins in one place only.
"""
from __future__ import annotations

from .backtest import simulate
from .data import get_candles
from .strategies import breakout, buy_and_hold

COINS = ["BTC-USD", "ETH-USD", "SOL-USD", "LTC-USD", "ADA-USD"]


def windows(closes: list[float], k: int) -> list[tuple[int, int]]:
    size = len(closes) // k
    return [(i * size, (i + 1) * size) for i in range(k)]


def run(entry: int = 336, exit: int = 96, k: int = 4) -> None:
    print(f"breakout entry={entry} exit={exit}, {k} windows per coin (net of fees)\n")
    wins = total = 0
    for coin in COINS:
        closes = [c for _, c in get_candles(coin, 3600, refresh=False)]
        pos = breakout(closes, entry, exit)  # signals use only past bars, so slicing is safe
        row = []
        for a, b in windows(closes, k):
            s = simulate(closes[a:b], pos[a:b]).total_return
            h = simulate(closes[a:b], buy_and_hold(closes[a:b])).total_return
            total += 1
            wins += s > 0
            row.append(f"{s:+6.0%} (bh {h:+5.0%})")
        print(f"{coin:8s} " + "  ".join(row))
    print(f"\nprofitable windows: {wins}/{total}")

    print("\nSOL-USD, nearby parameters, same windows (strategy return per window):")
    closes = [c for _, c in get_candles("SOL-USD", 3600, refresh=False)]
    for e in (168, 336, 504):
        for x in (48, 96, 168):
            pos = breakout(closes, e, x)
            rets = [simulate(closes[a:b], pos[a:b]).total_return for a, b in windows(closes, k)]
            print(f"  entry {e:3d} exit {x:3d}: " + "  ".join(f"{r:+6.0%}" for r in rets))
