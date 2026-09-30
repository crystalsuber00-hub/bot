"""SPY 0/3 model, pure logic (no I/O).

The model, from the SpyLieu "SPY 0/3 - The DTE Timing Guide":
  Clock first: 9:30-11:30 ET = 0DTE eligible, 11:30-1:00 = no forced trade, 1:00-4:00 = 3DTE eligible.
  Level -> Reaction -> Contract -> Risk. A touch is not a trade; the reaction is the confirmation.
  Bullish thesis -> call, bearish -> put. Invalidation defined before entry.
  3DTE only continues a morning thesis that is still intact. A morning loss does not create an afternoon trade.
  Exits: symmetric 20% premium target / 20% premium stop. Size from a fixed % of the account.

The guide leaves "what counts as a clean reaction" to live judgement; the rules below are a mechanical
stand-in for it (decisive bar through/off a mapped level, not chop, not chasing, room to the next zone).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .config import Spy03
from .models import Bar, OptionQuote
from .strategy import next_weekdays

MORNING, MIDDLE, AFTERNOON = "0dte", "middle", "3dte"


def phase(hhmm: str, m: Spy03) -> str:
    """Which part of the clock we are in: pre | 0dte | middle | 3dte | after."""
    if hhmm < m.zero_dte_start:
        return "pre"
    if hhmm < m.zero_dte_end:
        return MORNING
    if hhmm < m.three_dte_start:
        return MIDDLE
    if hhmm < m.three_dte_end:
        return AFTERNOON
    return "after"


# --- the map -----------------------------------------------------------------

@dataclass
class Level:
    price: float
    label: str
    manual: bool = False

    def to_dict(self) -> dict:
        return {"price": self.price, "label": self.label, "manual": self.manual}


def build_map(prior_day: Optional[Bar], premarket: list[Bar], open_price: float, m: Spy03) -> list[Level]:
    """A small set of reference zones for the day (three-zone rule: selective, not twenty lines)."""
    cands: list[Level] = [Level(float(p), "your level", True) for p in m.manual_levels]
    if m.use_prior_day and prior_day:
        cands += [Level(prior_day.high, "prior-day high"), Level(prior_day.low, "prior-day low"),
                  Level(prior_day.close, "prior close")]
    if m.use_premarket and premarket:
        cands += [Level(max(b.high for b in premarket), "pre-market high"),
                  Level(min(b.low for b in premarket), "pre-market low")]
    if m.round_step > 0:
        base = math.floor(open_price / m.round_step) * m.round_step
        cands += [Level(round(base + k * m.round_step, 2), f"${base + k * m.round_step:g} zone") for k in (-1, 0, 1, 2)]

    # merge levels that are effectively the same zone
    zones: list[Level] = []
    for lv in sorted(cands, key=lambda x: x.price):
        if zones and lv.price - zones[-1].price <= m.merge_distance:
            z = zones[-1]
            keep = z if (z.manual or not lv.manual) else lv
            zones[-1] = Level(keep.price, f"{z.label} + {lv.label}", z.manual or lv.manual)
        else:
            zones.append(Level(round(lv.price, 2), lv.label, lv.manual))

    manual = [z for z in zones if z.manual]
    auto = sorted((z for z in zones if not z.manual), key=lambda z: abs(z.price - open_price))
    keep = manual + auto[:max(0, m.max_zones - len(manual))]
    return sorted(keep, key=lambda z: z.price)


# --- the reaction --------------------------------------------------------------

@dataclass
class Reaction:
    direction: str      # bullish | bearish
    level: Level
    bar: Bar
    entry_ref: float    # SPY at the reaction (bar close)
    invalidation: float
    target: float
    reward_risk: float
    why: str

    @property
    def right(self) -> str:
        return "call" if self.direction == "bullish" else "put"


def _decisive(b: Bar, m: Spy03) -> bool:
    rng = b.high - b.low
    return rng > 0 and abs(b.close - b.open) / rng >= m.min_body_ratio


def _crosses(bars: list[Bar], level: float) -> int:
    sides = [1 if b.close > level else -1 for b in bars if b.close != level]
    return sum(1 for a, b in zip(sides, sides[1:]) if a != b)


def find_reaction(bars: list[Bar], levels: list[Level], m: Spy03) -> tuple[Optional[Reaction], list[str]]:
    """Look at the latest completed bar. Returns (reaction or None, reasons for setups that were passed on)."""
    if not bars or not levels:
        return None, []
    b = bars[-1]
    passed: list[str] = []
    for lv in sorted(levels, key=lambda z: abs(b.close - z.price)):
        L = lv.price
        if b.low <= L + m.touch_tolerance and b.close >= L + m.confirm_distance and b.close > b.open:
            direction = "bullish"
        elif b.high >= L - m.touch_tolerance and b.close <= L - m.confirm_distance and b.close < b.open:
            direction = "bearish"
        else:
            continue
        name = f"{lv.label} {L:.2f}"
        if not _decisive(b, m):
            passed.append(f"{name}: touch without a decisive bar (mostly wick)")
            continue
        recent = bars[-(m.chop_lookback + 1):]
        if _crosses(recent, L) >= m.max_level_crosses:
            passed.append(f"{name}: chop around the level, no clean reaction")
            continue
        if abs(b.close - L) > m.max_chase:
            passed.append(f"{name}: move already ran {abs(b.close - L):.2f} past the level (no chasing)")
            continue
        up = direction == "bullish"
        inv = round(L - m.invalidation_buffer if up else L + m.invalidation_buffer, 2)
        risk = abs(b.close - inv)
        beyond = [z.price for z in levels if (z.price > b.close + 0.05 if up else z.price < b.close - 0.05)]
        if beyond:
            target = min(beyond) if up else max(beyond)
        else:
            target = b.close + m.default_reward_risk * risk if up else b.close - m.default_reward_risk * risk
        rr = abs(target - b.close) / risk if risk else 0
        if rr < m.min_reward_risk:
            passed.append(f"{name}: only {rr:.1f}:1 room to the next zone at {target:.2f}")
            continue
        kind = ("held as support" if b.open >= L else "broke above") if up else \
               ("rejected as resistance" if b.open <= L else "broke below")
        why = f"SPY {kind} {name} on a decisive {m.bar_minutes}-min bar (close {b.close:.2f})"
        return Reaction(direction, lv, b, b.close, inv, round(target, 2), rr, why), passed
    return None, passed


def thesis_broken(direction: str, invalidation: float, bars_after: list[Bar]) -> Optional[Bar]:
    """First completed bar that closed through the invalidation level, if any."""
    for b in bars_after:
        if (b.close < invalidation) if direction == "bullish" else (b.close > invalidation):
            return b
    return None


# --- the contract --------------------------------------------------------------

def pick_expiration(expirations: list[str], today: date, trading_days: int) -> Optional[str]:
    """0 -> today's expiry (must exist). N -> first listed expiry at least N weekdays out (holidays roll forward)."""
    exps = sorted(e for e in expirations if e >= today.isoformat())
    if trading_days == 0:
        return today.isoformat() if today.isoformat() in exps else None
    target = next_weekdays(today, trading_days).isoformat()
    return next((e for e in exps if e >= target), None)


def pick_contract(chain: list[OptionQuote], right: str, spot: float, m: Spy03) -> tuple[Optional[OptionQuote], str]:
    """Near-the-money contract that passes the guide's strike questions: liquid, tight spread, sane premium, delta known."""
    opts = [o for o in chain if o.right == right]
    if not opts:
        return None, f"no {right}s in the chain"
    reasons = []
    for o in sorted(opts, key=lambda o: abs(o.strike - spot)):
        if o.bid <= 0 or o.ask <= 0:
            continue
        if o.delta is None or not m.min_delta <= abs(o.delta) <= m.max_delta:
            continue
        if (o.ask - o.bid) / o.mid > m.max_spread_pct:
            reasons.append(f"{o.strike:g} spread {o.bid:.2f}/{o.ask:.2f} too wide")
            continue
        if not m.min_premium <= o.mid <= m.max_premium:
            reasons.append(f"{o.strike:g} premium {o.mid:.2f} outside {m.min_premium:.2f}-{m.max_premium:.2f}")
            continue
        return o, "ok"
    return None, "; ".join(reasons[:3]) or f"no liquid {right} with delta {m.min_delta}-{m.max_delta}"


def size(premium: float, m: Spy03) -> tuple[int, float]:
    """Contracts that keep the stop-loss inside risk_pct of the account. Returns (contracts, $ risk per contract)."""
    per = premium * 100 * m.premium_stop_pct
    return min(m.max_contracts, int(m.account_size * m.risk_pct // per)) if per > 0 else 0, per


# --- the exit ------------------------------------------------------------------

@dataclass
class Signal:
    """A paper-tracked long option position."""
    id: str
    date: str
    window: str          # 0dte | 3dte
    direction: str       # bullish | bearish
    symbol: str
    right: str
    strike: float
    expiration: str
    quantity: int
    entry: float         # option mid at signal
    entry_time: str
    spy_entry: float
    level: float
    level_label: str
    invalidation: float
    target: float
    why: str
    status: str = "open"  # open | closed
    exit: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None
    last_mid: Optional[float] = None
    extra: dict = field(default_factory=dict)

    def pnl(self, price: Optional[float] = None) -> float:
        p = self.exit if price is None else price
        return 0.0 if p is None else (p - self.entry) * 100 * self.quantity

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def check_exit(sig: Signal, mid: float, spy: float, last_close: Optional[float], today: str, hhmm: str,
               m: Spy03) -> Optional[tuple[str, str]]:
    """(kind, text) to exit, or None to hold. last_close = latest completed bar close (for invalidation)."""
    up = sig.direction == "bullish"
    if mid >= sig.entry * (1 + m.premium_target_pct):
        return "target", f"premium target +{m.premium_target_pct:.0%} ({sig.entry:.2f} -> {mid:.2f})"
    if mid <= sig.entry * (1 - m.premium_stop_pct):
        return "stop", f"premium stop -{m.premium_stop_pct:.0%} ({sig.entry:.2f} -> {mid:.2f})"
    if last_close is not None and ((last_close < sig.invalidation) if up else (last_close > sig.invalidation)):
        return "invalidation", f"thesis invalidated: SPY closed {last_close:.2f} through {sig.invalidation:.2f}"
    if (spy >= sig.target) if up else (spy <= sig.target):
        return "level_target", f"SPY reached the next zone {sig.target:.2f}"
    if sig.window == MORNING and hhmm >= m.zero_dte_exit_time:
        return "time", f"0DTE time exit at {m.zero_dte_exit_time}"
    if sig.window == AFTERNOON:
        if m.three_dte_exit_time and (sig.date < today or hhmm >= m.three_dte_exit_time):
            return "time", f"3DTE time exit at {m.three_dte_exit_time}"
        if sig.expiration <= today and hhmm >= "15:55":
            return "time", "expiration day close"
    return None
