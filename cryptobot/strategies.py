"""Long/flat strategies. Each returns a target position (0 or 1) per bar, computed from
closes up to and including that bar only."""
from __future__ import annotations

import math


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


def _realized_vol(closes: list[float], i: int, n: int) -> float:
    rets = [closes[j] / closes[j - 1] - 1 for j in range(i - n + 1, i + 1)]
    m = sum(rets) / n
    return math.sqrt(sum((r - m) ** 2 for r in rets) / n * 24 * 365)


def vol_trend(closes: list[float], lookback: int = 336, target_vol: float = 0.4,
              every: int = 24) -> list[float]:
    """Long when the `lookback`-bar return is positive, sized to `target_vol` annualized
    (capped at 100%). Resized only every `every` bars to limit fee churn."""
    pos, out = 0.0, []
    for i, c in enumerate(closes):
        if i >= max(lookback, 168) and i % every == 0:
            if c > closes[i - lookback]:
                vol = _realized_vol(closes, i, 168)
                pos = round(min(1.0, target_vol / vol), 1) if vol else 0.0
            else:
                pos = 0.0
        out.append(pos)
    return out


def regime_trend(closes: list[float], lookback: int = 168, regime: int = 1200) -> list[int]:
    """Long only if the `lookback`-bar return is positive AND price is above its `regime`-bar SMA."""
    long_sma = sma(closes, regime)
    return [1 if long_sma[i] is not None and c > long_sma[i] and c > closes[i - lookback] else 0
            for i, c in enumerate(closes)]


def dip_buy(closes: list[float], short: int = 24, dip: float = 0.03, regime: int = 720) -> list[int]:
    """Mean reversion inside an uptrend: buy when price is `dip` below its `short`-bar SMA while above
    the `regime`-bar SMA; sell when it gets back to the short SMA or the uptrend breaks."""
    s, r = sma(closes, short), sma(closes, regime)
    pos, out = 0, []
    for i, c in enumerate(closes):
        if r[i] is not None:
            if c < r[i]:
                pos = 0
            elif not pos and c < s[i] * (1 - dip):
                pos = 1
            elif pos and c >= s[i]:
                pos = 0
        out.append(pos)
    return out


STRATEGIES = {
    "sma_cross": (sma_cross, {"fast": [12, 24, 48], "slow": [96, 168, 336]}),
    "breakout": (breakout, {"entry": [72, 168, 336], "exit": [24, 48, 96]}),
    "vol_trend": (vol_trend, {"lookback": [336, 720], "target_vol": [0.3, 0.5]}),
    "regime_trend": (regime_trend, {"lookback": [168, 336], "regime": [720, 1200]}),
    "dip_buy": (dip_buy, {"short": [24, 48], "dip": [0.03, 0.06], "regime": [720]}),
}
