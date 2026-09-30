"""`spxbot -c stocks.toml --report`: how the recorded 0DTE stock signals actually did.

Two P&L columns: at the mid (what the alerts show) and "realistic" (bought at the ask, sold at the bid, plus
per-contract fees), which is closer to what you would have gotten placing the trades yourself.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .stocks import StocksState, Trade

FEE = 0.04  # $ per contract per side: regulatory/exchange fees at commission-free brokers (approximate)


def realistic(t: Trade) -> Optional[float]:
    """P&L buying at the ask and selling at the bid, minus fees. None if the quotes weren't recorded."""
    e = t.extra
    buy, sell = e.get("entry_ask"), e.get("exit_bid")
    if buy is None or t.exit is None:
        return None
    if sell is None:
        sell = t.exit
    return (sell - buy) * 100 * t.contracts - 2 * FEE * t.contracts


def kind(t: Trade) -> str:
    label = t.extra.get("label") or ""
    if label.startswith("LAST-RESORT"):
        return "trend fallback"
    return "high conviction" if t.extra.get("conviction", 0) >= 6 else "lower conviction"


def _line(name: str, trades: list[Trade]) -> str:
    if not trades:
        return f"  {name:<18} no trades"
    mid = [t.pnl() for t in trades]
    real = [x for x in (realistic(t) for t in trades) if x is not None]
    wins = sum(x > 0 for x in mid)
    eq = peak = dd = 0.0
    for x in mid:
        eq += x
        peak, dd = max(peak, eq), max(dd, peak - eq)
    return (f"  {name:<18} {len(trades):>3} trades  win {wins / len(trades):>4.0%}  "
            f"P&L at mid {sum(mid):>+8.0f} $  realistic {sum(real):>+8.0f} $  "
            f"avg {sum(mid) / len(mid):>+6.0f} $  worst {min(mid):>+5.0f} $  max drawdown {dd:.0f} $")


def build_report(state: StocksState, days: Optional[int] = None) -> str:
    trades = [t for t in state.trades() if t.status == "closed"]
    if days:
        start = (date.today() - timedelta(days=days)).isoformat()
        trades = [t for t in trades if t.date >= start]
    trades.sort(key=lambda t: t.entry_time)
    if not trades:
        return "No closed signals recorded yet. Let the bot run for a few weeks, then try again."
    out = [f"0DTE stock signals {trades[0].date} .. {trades[-1].date}: {len({t.date for t in trades})} days",
           _line("all", trades)]
    for k in ("high conviction", "lower conviction", "trend fallback"):
        sub = [t for t in trades if kind(t) == k]
        if sub:
            out.append(_line(k, sub))
    out.append("\nBy exit reason: " + ", ".join(
        f"{r} {sum(t.exit_reason == r for t in trades)}" for r in sorted({t.exit_reason for t in trades})))
    by_sym = {}
    for t in trades:
        by_sym.setdefault(t.symbol, []).append(t.pnl())
    out.append("By ticker: " + ", ".join(f"{s} {len(v)}x {sum(v):+.0f}$" for s, v in sorted(by_sym.items())))
    out.append("\nEvery signal:")
    for t in trades:
        r = realistic(t)
        out.append(f"  {t.entry_time[:16].replace('T', ' ')} {t.symbol:<5} {t.strike:g}{t.right[0].upper():<2}"
                   f" {t.entry:>5.2f} -> {t.exit:>5.2f} {t.exit_reason:<12} {t.pnl():>+6.0f} $"
                   + (f"  (realistic {r:+.0f} $)" if r is not None else "") + f"  [{kind(t)}]")
    n = len(trades)
    out.append(f"\n{n} trades is {'far too few' if n < 30 else 'still few' if n < 100 else 'a start'} "
               "to tell skill from luck; judge the realistic column after 50-100 trades.")
    return "\n".join(out)
