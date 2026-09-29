"""`spxbot --check`: verify connection, data, strike selection and alerts. Places no orders."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .strategy import build_setup, choose_side


def run_check(cfg, client, notifier, now: datetime | None = None) -> bool:
    now = now or datetime.now(ZoneInfo(cfg.schedule.timezone))
    ok = True
    q = client.get_quote(cfg.symbol)
    print(f"[ok] {cfg.symbol}: last {q.last:.2f}, open {q.open}, prev close {q.prev_close}")

    side, move, why = choose_side(q, cfg.strategy)
    if side is None:
        print(f"[--] no trade direction right now: {why}")
    else:
        print(f"[ok] move {move:+.2f}% since {cfg.strategy.reference} -> would sell a {side.replace('_', ' ')} spread")
        exp = (now.date() + timedelta(days=cfg.dte)).isoformat()
        chain = client.get_chain(cfg.symbol, exp, cfg.option_root, "put" if side == "put_credit" else "call")
        if not chain:
            print(f"[!!] no option chain for {exp} (weekend/holiday, or missing options data subscription)")
            ok = False
        else:
            setup, why = build_setup(side, move, chain, cfg.strategy)
            if setup is None:
                print(f"[--] {len(chain)} options loaded but no qualifying spread: {why}")
            else:
                print(f"[ok] {len(chain)} options loaded. Would sell {setup.short.strike:g} / buy {setup.long.strike:g} "
                      f"(delta {abs(setup.short.delta):.2f}) for about {setup.credit:.2f} credit")

    notifier.send("spxbot test alert: if you can read this, notifications work.", {"event": "test"})
    print("[ok] test alert sent (check your phone / channel)")
    print("No orders were placed." if ok else "Some checks failed - see [!!] above.")
    return ok
