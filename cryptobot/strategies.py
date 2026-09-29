"""Long/flat strategies. Each returns a target position (0 or 1) per bar, computed from
closes up to and including that bar only."""
from __future__ import annotations


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def buy_and_hold(closes: list[float], **_) -> list[int]:
    return [1] * len(closes)


def sma_cross(closes: list[float], fast: int = 24, slow: int = 168) -> list[int]:
    f, s = sma(closes, fast), sma(closes, slow)
    return [1 if a is not None and b is not None and a > b else 0 for a, b in zip(f, s)]


def breakout(closes: list[float], entry: int = 168, exit: int = 48) -> list[int]:
    """Donchian: long after a close above the prior `entry`-bar high, flat below the prior `exit`-bar low."""
    pos, out = 0, []
    for i, c in enumerate(closes):
        if i >= entry:
            if c > max(closes[i - entry:i]):
                pos = 1
            elif c < min(closes[max(0, i - exit):i]):
                pos = 0
        out.append(pos)
    return out


STRATEGIES = {
    "sma_cross": (sma_cross, {"fast": [12, 24, 48], "slow": [96, 168, 336]}),
    "breakout": (breakout, {"entry": [72, 168, 336], "exit": [24, 48, 96]}),
}
