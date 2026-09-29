"""Model-based backtest using free Yahoo data (SPX 5-min bars + VIX1D).

No historical option quotes are available for free, so option prices are
Black-Scholes estimates using that day's VIX1D as implied vol. It answers
"how often does SPX travel far enough to hit a 15-20 delta strike after the
9:40 entry", NOT "what would my real fills have been". Read the caveats.

    python -m spxbot.backtest --refresh
"""
from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from .config import Strategy
from .models import OptionQuote, Position, Quote
from .strategy import build_setup, check_exit, choose_side

TZ = ZoneInfo("America/New_York")
MINUTES_PER_YEAR = 252 * 390
DATA = Path("data")
UA = {"User-Agent": "Mozilla/5.0"}


# --- data ------------------------------------------------------------------
def _yahoo(symbol: str, interval: str, rng: str) -> dict:
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}",
                     params={"interval": interval, "range": rng}, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()["chart"]["result"][0]


def refresh_cache() -> None:
    """Merge fresh Yahoo data into data/*.csv (Yahoo only serves ~60 days of 5m, so this accumulates)."""
    DATA.mkdir(exist_ok=True)
    res = _yahoo("^GSPC", "5m", "60d")
    q = res["indicators"]["quote"][0]
    rows = {str(t): [t, q["open"][i], q["high"][i], q["low"][i], q["close"][i]]
            for i, t in enumerate(res["timestamp"]) if q["close"][i] is not None}
    _merge(DATA / "spx_5m.csv", ["ts", "open", "high", "low", "close"], rows)

    res = _yahoo("^VIX1D", "1d", "2y")
    q = res["indicators"]["quote"][0]
    rows = {}
    for i, t in enumerate(res["timestamp"]):
        d = datetime.fromtimestamp(t, TZ).date().isoformat()
        if q["open"][i] is not None:
            rows[d] = [d, q["open"][i], q["close"][i]]
    _merge(DATA / "vix1d.csv", ["date", "open", "close"], rows)


def _merge(path: Path, header: list[str], new: dict) -> None:
    old = {}
    if path.exists():
        old = {r[0]: r for r in csv.reader(path.open()) if r and r[0] != header[0]}
    old.update({k: [str(x) for x in v] for k, v in new.items()})
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(old[k] for k in sorted(old))


def load_days() -> list[dict]:
    """[{date, vol, bars:[(start_dt, o, h, l, c)]}] for regular-session days with a VIX1D reading."""
    vix = {r["date"]: float(r["open"]) for r in csv.DictReader((DATA / "vix1d.csv").open())}
    by_day: dict[str, list] = {}
    for r in csv.DictReader((DATA / "spx_5m.csv").open()):
        dt = datetime.fromtimestamp(int(float(r["ts"])), TZ)
        if 9 * 60 + 30 <= dt.hour * 60 + dt.minute < 16 * 60:
            by_day.setdefault(dt.date().isoformat(), []).append(
                (dt, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    days = []
    for d, bars in sorted(by_day.items()):
        bars.sort()
        if d in vix and len(bars) >= 70 and bars[0][0].strftime("%H:%M") == "09:30":
            days.append({"date": d, "vol": vix[d] / 100, "bars": bars})
    return days


# --- pricing ---------------------------------------------------------------
def _n(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S: float, K: float, T: float, vol: float, right: str) -> tuple[float, float]:
    """(price, delta); r = q = 0 is fine intraday."""
    if T <= 0:
        v = max(S - K, 0) if right == "call" else max(K - S, 0)
        return v, 0.0
    sd = vol * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if right == "call":
        return S * _n(d1) - K * _n(d2), _n(d1)
    return K * _n(-d2) - S * _n(-d1), _n(d1) - 1


@dataclass
class Params:
    width: float = 10
    slippage: float = 0.10       # round-trip $/share paid to cross bid-ask (split entry/exit)
    commission: float = 0.65     # $/contract/leg
    put_iv_mult: float = 1.10    # crude skew: puts priced at 110% of VIX1D
    call_iv_mult: float = 0.95
    entry: str = "09:40"


def _chain(S: float, T: float, vol: float, p: Params) -> list[OptionQuote]:
    out = []
    base = round(S / 5) * 5
    for k in range(int(base - 400), int(base + 400) + 1, 5):
        for right, mult in (("put", p.put_iv_mult), ("call", p.call_iv_mult)):
            px, dl = bs(S, k, T, vol * mult, right)
            if px > 0.05:
                out.append(OptionQuote(f"{right}{k}", k, right, px, px, dl))
    return out


def _reprice(pos: Position, S: float, T: float, vol: float, p: Params) -> float:
    right = "put" if pos.side == "put_credit" else "call"
    mult = p.put_iv_mult if right == "put" else p.call_iv_mult
    a = bs(S, pos.short_strike, T, vol * mult, right)[0]
    b = bs(S, pos.long_strike, T, vol * mult, right)[0]
    return a - b


# --- simulation ------------------------------------------------------------
def simulate_day(day: dict, mode: str, st: Strategy, p: Params) -> dict | None:
    bars, vol = day["bars"], day["vol"]
    ei = next((i for i, b in enumerate(bars) if b[0].strftime("%H:%M") == p.entry), None)
    if ei is None:
        return None
    ref, S0 = bars[0][1], bars[ei][1]
    side, move, _ = choose_side(Quote(S0, ref), st)
    if mode == "with":
        pass
    elif mode == "against":
        side = None if side is None else ("call_credit" if side == "put_credit" else "put_credit")
    elif mode == "put_only":
        side = "put_credit"
    elif mode == "call_only":
        side = "call_credit"
    if side is None:
        return None
    move_pct = (S0 - ref) / ref * 100
    T0 = (16 * 60 - (bars[ei][0].hour * 60 + bars[ei][0].minute)) / MINUTES_PER_YEAR
    setup, why = build_setup(side, move_pct, _chain(S0, T0, vol, p), st)
    if setup is None:
        return {"date": day["date"], "side": side, "traded": False, "why": why}

    credit = setup.credit - p.slippage / 2
    pos = Position(id="x", date=day["date"], side=side, expiration=day["date"],
                   short_symbol="", long_symbol="", short_strike=setup.short.strike,
                   long_strike=setup.long.strike, credit=credit, quantity=1,
                   short_delta=setup.short.delta, spx_at_entry=S0, entry_time="")
    fees = 2 * p.commission
    for b in bars[ei:]:
        end = b[0] + timedelta(minutes=5)
        mins_left = 16 * 60 - (end.hour * 60 + end.minute)
        S = b[4]
        if mins_left <= 0:  # expired: cash settle at final close
            k1, k2 = pos.short_strike, pos.long_strike
            v = max(k1 - S, 0) - max(k2 - S, 0) if side == "put_credit" else max(S - k1, 0) - max(S - k2, 0)
            debit, reason = v, "expiry"
            break
        debit = _reprice(pos, S, mins_left / MINUTES_PER_YEAR, vol, p) + p.slippage / 2
        reason = check_exit(pos, debit, st, end.strftime("%H:%M"))
        if reason:
            fees += 2 * p.commission
            break
    pnl = (credit - debit) * 100 - fees
    return {"date": day["date"], "side": side, "traded": True, "pnl": pnl, "reason": reason,
            "credit": credit, "move_pct": move_pct}


def stats(trades: list[dict]) -> dict:
    t = [x for x in trades if x and x.get("traded")]
    if not t:
        return {}
    pnl = [x["pnl"] for x in t]
    wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x <= 0]
    eq = peak = dd = 0.0
    for x in pnl:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"n": len(t), "win%": len(wins) / len(t) * 100, "avg_win": sum(wins) / len(wins) if wins else 0,
            "avg_loss": sum(losses) / len(losses) if losses else 0, "total": sum(pnl),
            "per_trade": sum(pnl) / len(t), "worst": min(pnl), "maxdd": dd,
            "pf": sum(wins) / -sum(losses) if losses and sum(losses) else float("inf")}


def run(days: list[dict], p: Params, st: Strategy | None = None) -> None:
    base = st or Strategy(spread_width=p.width, min_credit=0.30)
    print(f"{len(days)} usable days: {days[0]['date']} .. {days[-1]['date']}\n")
    hdr = f"{'strategy':<26}{'n':>4}{'win%':>6}{'avg win':>9}{'avg loss':>10}{'total$':>9}{'$/trade':>9}{'worst':>8}{'maxDD':>8}"
    print(hdr + "\n" + "-" * len(hdr))
    for stop in (0, 2.0):
        for mode in ("with", "against", "put_only", "call_only"):
            s = Strategy(**{**base.__dict__, "stop_loss_multiple": stop})
            r = stats([simulate_day(d, mode, s, p) for d in days])
            if not r:
                continue
            name = f"{mode}, {'stop 2x' if stop else 'no stop'}"
            print(f"{name:<26}{r['n']:>4}{r['win%']:>6.0f}{r['avg_win']:>9.0f}{r['avg_loss']:>10.0f}"
                  f"{r['total']:>9.0f}{r['per_trade']:>9.1f}{r['worst']:>8.0f}{r['maxdd']:>8.0f}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="spxbot.backtest")
    ap.add_argument("--refresh", action="store_true", help="download/merge Yahoo data first")
    ap.add_argument("--width", type=float, default=10)
    ap.add_argument("--slippage", type=float, default=0.10)
    ap.add_argument("--put-iv-mult", type=float, default=1.10)
    ap.add_argument("--call-iv-mult", type=float, default=0.95)
    a = ap.parse_args()
    if a.refresh:
        refresh_cache()
    run(load_days(), Params(a.width, a.slippage, 0.65, a.put_iv_mult, a.call_iv_mult))


if __name__ == "__main__":
    main()
