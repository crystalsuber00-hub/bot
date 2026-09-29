"""Pure strategy logic: direction, strike selection, exit rules. No I/O."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import Strategy
from .models import OptionQuote, Position, Quote


@dataclass
class Setup:
    side: str  # put_credit | call_credit
    short: OptionQuote
    long: OptionQuote
    credit: float  # mid credit per share
    move_pct: float


def choose_side(quote: Quote, cfg: Strategy) -> tuple[Optional[str], float, str]:
    """Sell the side price is moving away from (net bullish after an up move, bearish after a down move): SPX up -> put credit spread, SPX down -> call credit spread.

    Returns (side or None, move_pct, reason).
    """
    ref = quote.open if cfg.reference == "open" else quote.prev_close
    if not ref:
        return None, 0.0, f"no {cfg.reference} price available"
    move_pct = (quote.last - ref) / ref * 100
    if move_pct == 0 or abs(move_pct) < cfg.min_move_pct:
        return None, move_pct, f"move {move_pct:+.2f}% below min {cfg.min_move_pct}%"
    return ("put_credit" if move_pct > 0 else "call_credit"), move_pct, "ok"


def build_setup(side: str, move_pct: float, chain: list[OptionQuote], cfg: Strategy) -> tuple[Optional[Setup], str]:
    right = "put" if side == "put_credit" else "call"
    legs = {o.strike: o for o in chain if o.right == right and o.bid is not None and o.ask is not None}

    cands = [
        o for o in legs.values()
        if o.delta is not None and cfg.target_delta_min <= abs(o.delta) <= cfg.target_delta_max and o.bid > 0
    ]
    if not cands:
        return None, f"no {right} with |delta| in [{cfg.target_delta_min}, {cfg.target_delta_max}]"
    short = min(cands, key=lambda o: abs(abs(o.delta) - cfg.target_delta))

    long_strike = short.strike - cfg.spread_width if right == "put" else short.strike + cfg.spread_width
    long = legs.get(long_strike)
    if long is None:
        return None, f"no {right} at long strike {long_strike}"

    credit = round(short.mid - long.mid, 2)
    if credit < cfg.min_credit:
        return None, f"credit {credit:.2f} below min {cfg.min_credit:.2f}"
    return Setup(side, short, long, credit, move_pct), "ok"


@dataclass
class ExitSignal:
    kind: str  # profit | stop | time | kill  (anything but "profit" is urgent)
    text: str


def check_exit(pos: Position, debit: float, cfg: Strategy, now_hhmm: str) -> Optional[ExitSignal]:
    """Return an exit signal, or None to keep holding. `debit` = cost to buy back the spread."""
    captured = (pos.credit - debit) / pos.credit
    if captured >= cfg.profit_target:
        return ExitSignal("profit", f"profit target ({captured:.0%} of credit captured)")
    if cfg.stop_loss_multiple and debit >= pos.credit * cfg.stop_loss_multiple:
        return ExitSignal("stop", f"stop loss (debit {debit:.2f} >= {cfg.stop_loss_multiple}x credit)")
    if cfg.force_close_time and now_hhmm >= cfg.force_close_time:
        return ExitSignal("time", f"time exit at {cfg.force_close_time}")
    return None
