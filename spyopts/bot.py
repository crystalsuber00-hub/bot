"""Paper iron-condor bot for SPY on Alpaca, following the spyopts backtest:
~30-day expiry, short strikes 3% out of the money, long wings 6% out, risk 10% of equity per trade.
One difference: the backtest held to expiry; the bot closes the day before expiry (from 14:00 ET),
because SPY options settle into 100 shares per contract, which a small account can't hold.

Run one tick:   python -m spyopts.bot          (for cron / GitHub Actions every 15 min)
Loop:           python -m spyopts.bot --loop
Check setup:    python -m spyopts.bot --check  (no orders)
Status:         python -m spyopts.bot --status
Env: ALPACA_KEY_ID, ALPACA_SECRET_KEY, optional NTFY_TOPIC, ALPACA_FEED (indicative|opra).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .alpaca import Alpaca, AlpacaError

ET = ZoneInfo("America/New_York")
STATE = Path("spy_bot_state.json")
UNDERLYING = "SPY"
TARGET_DTE, MIN_DTE, MAX_DTE = 30, 25, 45
SHORT_OTM, LONG_OTM = 0.03, 0.06
RISK_FRAC = 0.10
ENTRY_START, ENTRY_END = "10:00", "15:30"
CLOSE_FROM = "14:00"          # on the day before expiry (or expiry day, if still open)
REPRICE_AFTER_MIN = 10        # cancel and re-quote an unfilled order after this long
STEP = 0.05                   # price concession per re-quote
MAX_ENTRY_TRIES = 4           # per day; then no trade today
MIN_CREDIT = 0.20             # per share; below this the trade isn't worth the risk


def notify(msg: str) -> None:
    print(f"{datetime.now(ET):%Y-%m-%d %H:%M} {msg}", flush=True)
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=msg.encode(), timeout=10)
        except requests.RequestException:
            pass


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"position": None, "pending": None, "tries": {}, "trades": [], "halted": None, "notes": {}}


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=1))


def _mid(q: tuple[float, float]) -> float:
    return (q[0] + q[1]) / 2


def _hhmm(now: datetime) -> str:
    return now.strftime("%H:%M")


def _weekdays_between(a: date, b: date) -> int:
    """Weekdays after `a` up to and including `b` (market holidays not known)."""
    return sum(1 for i in range(1, (b - a).days + 1) if (a + timedelta(i)).weekday() < 5)


def _once_per_day(state: dict, key: str, today: str) -> bool:
    if state["notes"].get(key) == today:
        return False
    state["notes"][key] = today
    return True


def pick_condor(api: Alpaca, today: date) -> dict | None:
    spot = api.stock_price(UNDERLYING)
    cons = api.contracts(UNDERLYING, (today + timedelta(MIN_DTE)).isoformat(),
                         (today + timedelta(MAX_DTE)).isoformat(),
                         math.floor(spot * (1 - LONG_OTM - 0.02)), math.ceil(spot * (1 + LONG_OTM + 0.02)))
    if not cons:
        return None
    expiries = sorted({c["expiration_date"] for c in cons},
                      key=lambda e: abs((date.fromisoformat(e) - today).days - TARGET_DTE))
    exp = expiries[0]
    by = {"call": [], "put": []}
    for c in cons:
        if c["expiration_date"] == exp:
            by[c["type"]].append((float(c["strike_price"]), c["symbol"]))

    def nearest(kind: str, target: float) -> tuple[float, str]:
        return min(by[kind], key=lambda x: abs(x[0] - target))

    sc, lc = nearest("call", spot * (1 + SHORT_OTM)), nearest("call", spot * (1 + LONG_OTM))
    sp, lp = nearest("put", spot * (1 - SHORT_OTM)), nearest("put", spot * (1 - LONG_OTM))
    if not (lp[0] < sp[0] < spot < sc[0] < lc[0]):
        return None
    return {"expiry": exp, "spot": spot,
            "legs": {"short_call": sc[1], "long_call": lc[1], "short_put": sp[1], "long_put": lp[1]},
            "width": max(lc[0] - sc[0], sp[0] - lp[0])}


def _order_legs(legs: dict, opening: bool) -> list[dict]:
    out = []
    for role, sym in legs.items():
        short = role.startswith("short")
        side = ("sell" if short else "buy") if opening else ("buy" if short else "sell")
        intent = f"{side}_to_{'open' if opening else 'close'}"
        out.append({"symbol": sym, "ratio_qty": "1", "side": side, "position_intent": intent})
    return out


def _net_fill(order: dict) -> float:
    """Net per-share credit from leg fills (sells minus buys)."""
    total = 0.0
    for leg in order.get("legs") or []:
        px = float(leg.get("filled_avg_price") or 0)
        total += px if leg["side"] == "sell" else -px
    return total


def handle_pending(api: Alpaca, state: dict, now: datetime) -> str:
    p = state["pending"]
    o = api.order(p["id"])
    status, filled = o["status"], int(float(o.get("filled_qty") or 0))
    if status == "filled" or (status in ("canceled", "expired") and filled > 0):
        net = _net_fill(o)
        if p["kind"] == "open":
            state["position"] = {"legs": p["legs"], "qty": filled, "credit": round(net, 2),
                                 "expiry": p["expiry"], "entry_date": now.date().isoformat(),
                                 "width": p["width"]}
            notify(f"OPENED {filled}x SPY iron condor exp {p['expiry']} for ${net:.2f} credit "
                   f"(max loss ${(p['width'] - net) * 100 * filled:,.0f})")
        else:
            pos = state["position"]
            debit = -net
            pnl = (pos["credit"] - debit) * 100 * filled
            state["trades"].append({**pos, "exit_date": now.date().isoformat(), "debit": round(debit, 2),
                                    "pnl": round(pnl, 2)})
            pos["qty"] -= filled
            if pos["qty"] <= 0:
                state["position"] = None
            notify(f"CLOSED {filled}x SPY iron condor for ${debit:.2f} debit: P&L ${pnl:+,.2f}")
        state["pending"] = None
        return f"{p['kind']} filled"
    if status in ("canceled", "expired", "rejected"):
        state["pending"] = None
        if status == "rejected":
            notify(f"{p['kind']} order REJECTED: {o.get('reject_reason') or o}")
        return f"{p['kind']} order {status}"
    placed = datetime.fromisoformat(p["placed"])
    if now - placed >= timedelta(minutes=REPRICE_AFTER_MIN):
        api.cancel(p["id"])
        return f"{p['kind']} order unfilled after {REPRICE_AFTER_MIN} min: cancel requested, will re-quote"
    return f"{p['kind']} order working at {p['limit']:+.2f}"


def reconcile(api: Alpaca, state: dict) -> str | None:
    """Compare state with the broker. Returns a halt reason on mismatch."""
    held = {p["symbol"]: int(float(p["qty"])) for p in api.positions() if p.get("asset_class") == "us_option"
            and p["symbol"].startswith(UNDERLYING)}
    pos = state["position"]
    if not pos:
        return f"broker holds SPY options the bot didn't open: {held}" if held else None
    expected = {sym: (-pos["qty"] if role.startswith("short") else pos["qty"]) for role, sym in pos["legs"].items()}
    if held == expected:
        return None
    if not held:
        state["trades"].append({**pos, "exit_date": date.today().isoformat(), "pnl": None,
                                "note": "closed outside the bot (expired, assigned or manual); check Alpaca"})
        state["position"] = None
        notify("Position disappeared at the broker (expired/assigned/manual?). Recorded as closed; check Alpaca.")
        return None
    return f"broker positions {held} don't match bot state {expected}"


def open_trade(api: Alpaca, state: dict, now: datetime) -> str:
    today = now.date().isoformat()
    tries = state["tries"].get(f"open {today}", 0)
    if tries >= MAX_ENTRY_TRIES:
        return "entry not filled today; trying again next trading day"
    c = pick_condor(api, now.date())
    if not c:
        return "no suitable expiry/strikes found"
    q = api.quotes(list(c["legs"].values()))
    if any(q.get(s, (0, 0))[1] <= 0 for s in c["legs"].values()):
        return "missing option quotes; waiting"
    L = c["legs"]
    credit = _mid(q[L["short_call"]]) + _mid(q[L["short_put"]]) - _mid(q[L["long_call"]]) - _mid(q[L["long_put"]])
    limit = round(credit - STEP * tries, 2)
    if limit < MIN_CREDIT:
        return f"credit {limit:.2f} below minimum {MIN_CREDIT}; no trade"
    equity = float(api.account()["equity"])
    risk_per = (c["width"] - limit) * 100
    qty = int(RISK_FRAC * equity // risk_per)
    if qty < 1:
        if _once_per_day(state, "too_small", today):
            notify(f"NO TRADE: one condor risks ${risk_per:,.0f}, more than {RISK_FRAC:.0%} of equity "
                   f"${equity:,.0f}. The tested setup needs about ${risk_per / RISK_FRAC:,.0f}.")
        return "account too small for the tested setup"
    o = api.submit_mleg(_order_legs(L, opening=True), qty, -limit)
    state["tries"] = {f"open {today}": tries + 1}
    state["pending"] = {"id": o["id"], "kind": "open", "limit": -limit, "placed": now.isoformat(),
                        "legs": L, "expiry": c["expiry"], "width": c["width"]}
    return f"entry order: {qty}x exp {c['expiry']} at ${limit:.2f} credit (mid {credit:.2f}, SPY {c['spot']:.2f})"


def close_trade(api: Alpaca, state: dict, now: datetime) -> str:
    pos = state["position"]
    L = pos["legs"]
    key = f"close {now.date().isoformat()}"
    tries = state["tries"].get(key, 0)
    q = api.quotes(list(L.values()))
    if any(s not in q for s in L.values()):
        return "missing quotes for close; retrying"
    mid = _mid(q[L["short_call"]]) + _mid(q[L["short_put"]]) - _mid(q[L["long_call"]]) - _mid(q[L["long_put"]])
    natural = q[L["short_call"]][1] + q[L["short_put"]][1] - q[L["long_call"]][0] - q[L["long_put"]][0]
    debit = natural if tries >= 3 else min(natural, mid + STEP * tries)
    debit = max(0.01, round(debit, 2))
    o = api.submit_mleg(_order_legs(L, opening=False), pos["qty"], debit)
    state["tries"] = {key: tries + 1}
    state["pending"] = {"id": o["id"], "kind": "close", "limit": debit, "placed": now.isoformat()}
    return f"close order: {pos['qty']}x at ${debit:.2f} debit (mid {mid:.2f}, natural {natural:.2f})"


def tick(api: Alpaca, state: dict, now: datetime | None = None) -> str:
    now = now or datetime.now(ET)
    if state.get("halted"):
        return f"HALTED: {state['halted']} (fix at Alpaca, then run --clear-halt)"
    if not api.clock()["is_open"]:
        return "market closed"
    if state["pending"]:
        return handle_pending(api, state, now)
    problem = reconcile(api, state)
    if problem:
        state["halted"] = problem
        notify(f"HALTED, no more orders: {problem}")
        return "halted"
    pos = state["position"]
    if pos:
        exp = date.fromisoformat(pos["expiry"])
        days_left = _weekdays_between(now.date(), exp)
        if (days_left <= 1 and _hhmm(now) >= CLOSE_FROM) or days_left <= 0:
            if days_left <= 0 and _once_per_day(state, "expiry_open", now.date().isoformat()):
                notify("WARNING: condor still open on expiry day; bot keeps trying to close. Check Alpaca.")
            return close_trade(api, state, now)
        return f"holding {pos['qty']}x condor exp {pos['expiry']} ({days_left} trading days left), credit ${pos['credit']:.2f}"
    if ENTRY_START <= _hhmm(now) <= ENTRY_END:
        return open_trade(api, state, now)
    return "flat; outside entry hours"


def check(api: Alpaca) -> None:
    acct = api.account()
    lvl = acct.get("options_trading_level")
    print(f"account {acct.get('account_number')} equity ${float(acct['equity']):,.2f}, options level {lvl}")
    if lvl is not None and int(lvl) < 3:
        print("  !! iron condors need options level 3. Request it in the Alpaca dashboard.")
    print(f"market open: {api.clock()['is_open']}")
    c = pick_condor(api, datetime.now(ET).date())
    if not c:
        print("!! could not find a suitable expiry/strikes")
        return
    q = api.quotes(list(c["legs"].values()))
    print(f"SPY {c['spot']:.2f}; candidate condor exp {c['expiry']}, width ${c['width']:.0f}")
    for role, sym in c["legs"].items():
        print(f"  {role:10s} {sym}  bid/ask {q.get(sym)}")
    print("setup OK; no orders placed.")


def status(state: dict) -> None:
    print(json.dumps({k: state[k] for k in ("position", "pending", "halted")}, indent=1))
    done = [t for t in state["trades"] if t.get("pnl") is not None]
    if done:
        pnl = sum(t["pnl"] for t in done)
        wins = sum(t["pnl"] > 0 for t in done)
        print(f"{len(done)} closed trades, {wins} wins, total P&L ${pnl:+,.2f}")
        for t in done:
            print(f"  {t['entry_date']} -> {t['exit_date']}  {t['qty']}x  credit {t['credit']:.2f} "
                  f"debit {t['debit']:.2f}  P&L ${t['pnl']:+,.2f}")
    else:
        print("no closed trades yet")


def main() -> None:
    ap = argparse.ArgumentParser(prog="spyopts.bot", description="Paper SPY iron-condor bot for Alpaca")
    ap.add_argument("--loop", action="store_true", help="run every 5 minutes until stopped")
    ap.add_argument("--check", action="store_true", help="test keys, options level and quotes; no orders")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--clear-halt", action="store_true")
    a = ap.parse_args()
    state = load_state()
    if a.status:
        status(state)
        return
    if a.clear_halt:
        state["halted"] = None
        save_state(state)
        print("halt cleared")
        return
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise SystemExit("set ALPACA_KEY_ID and ALPACA_SECRET_KEY (paper keys)")
    api = Alpaca(key, secret, os.environ.get("ALPACA_FEED", "indicative"))
    if a.check:
        check(api)
        return
    while True:
        try:
            print(f"{datetime.now(ET):%Y-%m-%d %H:%M} {tick(api, state)}", flush=True)
        except (AlpacaError, requests.RequestException) as e:
            print(f"{datetime.now(ET):%Y-%m-%d %H:%M} error: {e}", flush=True)
        save_state(state)
        if not a.loop:
            return
        time.sleep(300)


if __name__ == "__main__":
    main()
