"""`spxbot --report`: summarize what the bot signalled/traded and compare it with the backtest model."""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta


def _weekdays(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _model_pnl(cfg, dates: list[str]) -> dict[str, float | None]:
    """Model (Black-Scholes) P&L for the same dates, or {} if the cached data isn't available."""
    try:
        from .backtest import Params, load_days, simulate_day
        days = {d["date"]: d for d in load_days()}
    except Exception:
        return {}
    p = Params(width=cfg.strategy.spread_width)
    out = {}
    for d in dates:
        if d in days:
            r = simulate_day(days[d], "with", cfg.strategy, p)
            out[d] = r["pnl"] if r and r.get("traded") else None
    return out


def build_report(cfg, state, days: int | None = None, today: date | None = None) -> str:
    today = today or date.today()
    positions = sorted(state.all_positions(), key=lambda p: p.date)
    skipped = dict(state.data.get("skipped", {}))
    dates = [p.date for p in positions] + list(skipped)
    if not dates:
        return "No signals recorded yet. Run the bot (spxbot -c config.toml) and check back."
    start = date.fromisoformat(min(dates))
    if days:
        start = max(start, today - timedelta(days=days))
    lo = start.isoformat()
    positions = [p for p in positions if p.date >= lo]
    skipped = {d: r for d, r in skipped.items() if d >= lo}

    L = [f"SPX bot report: {lo} to {today.isoformat()}  (mode: {cfg.mode})", ""]

    # coverage
    decided = {p.date for p in positions} | set(skipped)
    wd = _weekdays(start, today)
    missing = [d.isoformat() for d in wd if d.isoformat() not in decided and d < today]
    L.append(f"Coverage: {len(wd)} weekdays, {len(decided)} with a recorded decision"
             + (f"; NO decision on {', '.join(missing)} (bot not running, or a holiday it never saw)" if missing else ""))

    # trades
    traded = [p for p in positions if p.status in ("open", "pending_exit", "closed")]
    closed = [p for p in traded if p.status == "closed" and p.exit_debit is not None]
    model = _model_pnl(cfg, [p.date for p in closed])
    L += ["", "Trades", f"{'date':<11}{'side':<12}{'strikes':<12}{'delta':>6}{'credit':>7}{'exit':>7}{'P&L $':>8}  {'model $':>8}  how it ended"]
    for p in traded:
        pnl = f"{p.realized():+.0f}" if p in closed else "open"
        m = model.get(p.date)
        L.append(f"{p.date:<11}{p.side:<12}{p.short_strike:g}/{p.long_strike:g}".ljust(35)
                 + f"{abs(p.short_delta):>6.2f}{p.credit:>7.2f}"
                 + f"{(p.exit_debit if p.exit_debit is not None else float('nan')):>7.2f}{pnl:>8}  "
                 + f"{(f'{m:+.0f}' if m is not None else '-'):>8}  {p.exit_reason or p.status}")
    if not traded:
        L.append("(no trades)")

    # stats
    pnls = [p.realized() for p in closed]
    if pnls:
        wins, losses = [x for x in pnls if x > 0], [x for x in pnls if x <= 0]
        n, mean = len(pnls), sum(pnls) / len(pnls)
        sd = math.sqrt(sum((x - mean) ** 2 for x in pnls) / (n - 1)) if n > 1 else 0
        streak = best = 0
        for x in pnls:
            streak = streak + 1 if x < 0 else 0
            best = max(best, streak)
        eq = peak = dd = 0.0
        for x in pnls:
            eq += x
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        L += ["", "Results (closed trades)",
              f"  trades {n} | wins {len(wins)} ({len(wins) / n:.0%}) | losses {len(losses)}",
              f"  total ${sum(pnls):+,.0f} | per trade ${mean:+.1f} (+/- ${sd / math.sqrt(n) if n > 1 else 0:.0f} standard error)",
              f"  avg win ${sum(wins) / len(wins) if wins else 0:,.0f} | avg loss ${sum(losses) / len(losses) if losses else 0:,.0f} | worst ${min(pnls):,.0f}",
              f"  longest losing streak {best} | max drawdown ${dd:,.0f}"]
        for side in ("put_credit", "call_credit"):
            sp = [p.realized() for p in closed if p.side == side]
            if sp:
                L.append(f"  {side}: {len(sp)} trades, ${sum(sp):+,.0f}, {sum(x > 0 for x in sp) / len(sp):.0%} wins")
        if n < 30:
            L.append(f"  ! only {n} closed trades: too few to judge the strategy either way.")
        if cfg.mode == "signal":
            L.append("  ! signal mode assumes fills at the mid price; real fills are usually a bit worse.")

    # model comparison
    both = [(p.realized(), model[p.date]) for p in closed if model.get(p.date) is not None]
    L += ["", "Bot vs backtest model (same days)"]
    if both:
        diff = [a - m for a, m in both]
        L.append(f"  compared {len(both)} days: bot ${sum(a for a, _ in both):+,.0f} vs model ${sum(m for _, m in both):+,.0f}; "
                 f"average difference ${sum(diff) / len(diff):+.0f} per trade")
        same_dir = sum((a > 0) == (m > 0) for a, m in both)
        L.append(f"  same win/loss outcome on {same_dir} of {len(both)} days. A big, consistent gap means the model "
                 "is mispricing (skew, fills, volatility).")
    else:
        L.append("  no overlap: run `python -m spxbot.backtest --refresh` (needs internet; covers the last ~60 days) then re-run the report.")

    # skips
    if skipped:
        L += ["", "Days with no trade"]
        for reason, n in Counter(skipped.values()).most_common():
            L.append(f"  {n}x {reason}")
    cancelled = [p for p in positions if p.status == "cancelled"]
    if cancelled:
        L.append(f"  ({len(cancelled)} entry orders never filled)")
    return "\n".join(L)
