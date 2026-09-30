"""SPY 0/3 backtest: replays the live signal engine over past days (free Yahoo data, model option prices).

SPY 5-min bars (pre-market included) come from Yahoo, ~60 days per download; data/ accumulates on each
--refresh. Option prices are Black-Scholes estimates: VIX1D as implied vol for 0DTE, VIX9D for 3DTE,
plus a bid/ask spread and commissions. Exits are checked on 5-minute closes, not tick by tick.
It measures how often the rules fire and how the exits behave, NOT what real fills would have been.

    python -m spxbot.spy03_backtest --refresh
"""
from __future__ import annotations

import argparse
import csv
import tempfile
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .backtest import DATA, _merge, _yahoo, bs
from .config import Config, Spy03
from .models import Bar, OptionQuote, Quote
from .spy03_engine import Spy03Engine, Spy03State

NY = ZoneInfo("America/New_York")
MIN_PER_YEAR = 252 * 390


# --- data -------------------------------------------------------------------------
def refresh() -> None:
    DATA.mkdir(exist_ok=True)
    res = _yahoo_pp("SPY")
    q = res["indicators"]["quote"][0]
    rows = {str(t): [t, q["open"][i], q["high"][i], q["low"][i], q["close"][i]]
            for i, t in enumerate(res["timestamp"]) if q["close"][i] is not None}
    _merge(DATA / "spy_5m.csv", ["ts", "open", "high", "low", "close"], rows)
    for sym, name in (("^VIX1D", "vix1d"), ("^VIX9D", "vix9d")):
        res = _yahoo(sym, "1d", "2y")
        q = res["indicators"]["quote"][0]
        rows = {}
        for i, t in enumerate(res["timestamp"]):
            d = datetime.fromtimestamp(t, NY).date().isoformat()
            if q["open"][i] is not None:
                rows[d] = [d, q["open"][i], q["close"][i]]
        _merge(DATA / f"{name}.csv", ["date", "open", "close"], rows)


def _yahoo_pp(symbol: str) -> dict:
    import requests
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                     params={"interval": "5m", "range": "60d", "includePrePost": "true"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    return r.json()["chart"]["result"][0]


def _vol(name: str) -> dict[str, float]:
    p = DATA / f"{name}.csv"
    return {r["date"]: float(r["open"]) / 100 for r in csv.DictReader(p.open())} if p.exists() else {}


def load() -> tuple[dict[str, list[Bar]], dict[str, float], dict[str, float]]:
    days: dict[str, list[Bar]] = {}
    for r in csv.DictReader((DATA / "spy_5m.csv").open()):
        t = datetime.fromtimestamp(int(float(r["ts"])), NY).strftime("%Y-%m-%dT%H:%M")
        if "04:00" <= t[11:] < "16:00":
            days.setdefault(t[:10], []).append(
                Bar(t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    for d in days:
        days[d].sort(key=lambda b: b.time)
    full = {d: b for d, b in days.items() if sum("09:30" <= x.time[11:] for x in b) >= 75}
    return full, _vol("vix1d"), _vol("vix9d")


# --- a fake broker that serves history ------------------------------------------------
class HistoryFeed:
    def __init__(self, days, vix1d, vix9d, spread=0.02, put_skew=1.10, call_skew=0.95):
        self.days, self.vix1d, self.vix9d = days, vix1d, vix9d
        self.spread, self.skew = spread, {"put": put_skew, "call": call_skew}
        self.now: datetime | None = None

    def _spot(self) -> float:
        cut = self.now.strftime("%Y-%m-%dT%H:%M")
        bars = [b for b in self.days[self.now.date().isoformat()] if b.time < cut]
        return bars[-1].close

    def _years(self, exp: str) -> float:
        today, end = self.now.date(), date.fromisoformat(exp)
        mins = max(0, 16 * 60 - (self.now.hour * 60 + self.now.minute))
        d = today
        while d < end:
            d += timedelta(days=1)
            mins += 390 * (d.weekday() < 5)
        return mins / MIN_PER_YEAR

    def _price(self, exp: str, right: str, strike: float) -> OptionQuote:
        today = self.now.date().isoformat()
        iv = (self.vix1d if exp == today else self.vix9d).get(today) or self.vix9d.get(today) or 0.15
        px, delta = bs(self._spot(), strike, self._years(exp), iv * self.skew[right], right)
        half = self.spread / 2
        return OptionQuote(f"{exp}|{right}|{strike:g}", strike, right, max(px - half, 0.0), px + half, delta)

    # broker interface used by the engine
    def get_quote(self, symbol):
        return Quote(self._spot())

    def get_bars(self, symbol, day, minutes):
        return list(self.days.get(day, []))

    def get_daily(self, symbol, start, end):
        out = []
        for d in sorted(self.days):
            if start <= d <= end:
                rth = [b for b in self.days[d] if "09:30" <= b.time[11:]]
                out.append(Bar(d, rth[0].open, max(b.high for b in rth), min(b.low for b in rth), rth[-1].close))
        return out

    def get_expirations(self, symbol):
        d, out = self.now.date(), []
        for _ in range(15):
            if d.weekday() < 5:
                out.append(d.isoformat())
            d += timedelta(days=1)
        return out

    def get_chain(self, symbol, exp, root=None, right=None, near=None):
        s = round(self._spot())
        return [self._price(exp, r, k) for k in range(s - 5, s + 6) for r in ("call", "put") if right in (None, r)]

    def get_option_quotes(self, symbols):
        out = {}
        for sym in symbols:
            exp, right, k = sym.split("|")
            if exp >= self.now.date().isoformat():
                out[sym] = self._price(exp, right, float(k))
        return out


class Quiet:
    def send(self, text, payload=None):
        pass


# --- replay -----------------------------------------------------------------------------
def _flipped(find):
    """Same moments, opposite direction: a baseline for whether the reaction picks direction at all."""
    def wrapper(bars, levels, m):
        r, passed = find(bars, levels, m)
        if r is not None:
            e = r.entry_ref
            r = replace(r, direction="bearish" if r.direction == "bullish" else "bullish",
                        invalidation=round(2 * e - r.invalidation, 2), target=round(2 * e - r.target, 2))
        return r, passed
    return wrapper


def replay(days, vix1d, vix9d, m: Spy03, commission=0.65, spread=0.02, flip=False) -> list[dict]:
    from . import spy03_engine
    original = spy03_engine.find_reaction
    if flip:
        spy03_engine.find_reaction = _flipped(original)
    try:
        return _replay(days, vix1d, vix9d, m, commission, spread)
    finally:
        spy03_engine.find_reaction = original


def _replay(days, vix1d, vix9d, m, commission, spread) -> list[dict]:
    feed = HistoryFeed(days, vix1d, vix9d, spread)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(model="spy03")
        cfg.spy03 = replace(m, state_file=str(Path(tmp) / "s.json"))
        eng = Spy03Engine(cfg, feed, Quiet(), Spy03State(cfg.spy03.state_file))
        ordered = sorted(days)
        for d in ordered[1:]:  # first day only provides the prior-day levels
            if d not in vix1d and d not in vix9d:
                continue
            t = datetime.fromisoformat(f"{d}T09:30").replace(tzinfo=NY)
            while t <= t.replace(hour=16, minute=0):
                feed.now = t + timedelta(seconds=10)
                eng.tick(feed.now)
                t += timedelta(minutes=5)
        sigs = eng.state.signals()
    out = []
    for s in sigs:
        if s.status != "closed":
            continue
        # engine paper-tracks at mid; charge half the spread each way + commissions
        cost = s.quantity * (spread * 100 + 2 * commission)
        out.append({"date": s.date, "window": s.window, "dir": s.direction, "qty": s.quantity,
                    "entry": s.entry, "exit": s.exit, "reason": s.exit_reason, "pnl": s.pnl() - cost,
                    "ret": (s.exit - s.entry) / s.entry})
    return out


def summarize(name: str, trades: list[dict], ndays: int) -> str:
    if not trades:
        return f"{name:<30}   0 trades"
    pnl = [t["pnl"] for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    eq = peak = dd = 0.0
    for x in pnl:
        eq += x
        peak, dd = max(peak, eq), max(dd, peak - eq)
    pf = sum(wins) / -sum(losses) if losses and sum(losses) else float("inf")
    return (f"{name:<30}{len(trades):>4}{len(trades) / ndays:>6.2f}{len(wins) / len(trades) * 100:>6.0f}"
            f"{(sum(wins) / len(wins) if wins else 0):>8.0f}{(sum(losses) / len(losses) if losses else 0):>9.0f}"
            f"{sum(pnl):>9.0f}{min(pnl):>8.0f}{dd:>8.0f}{pf:>6.2f}")


def run(m: Spy03 | None = None, show_trades: bool = True) -> None:
    days, v1, v9 = load()
    m = m or Spy03()
    ndays = len(sorted(days)[1:])
    print(f"{ndays} trading days: {sorted(days)[1]} .. {max(days)}  (model prices, not real fills)\n")
    hdr = f"{'variant':<30}{'n':>4}{'/day':>6}{'win%':>6}{'avg win':>8}{'avg loss':>9}{'total$':>9}{'worst':>8}{'maxDD':>8}{'PF':>6}"
    print(hdr + "\n" + "-" * len(hdr))
    variants = {
        "SPY 0/3 as configured": m,
        "0DTE morning only": replace(m, max_afternoon_trades=0),
        "3DTE afternoon only": replace(m, max_morning_trades=0),
        "no 20% target (ride to level)": replace(m, premium_target_pct=5.0),
        "30% target / 20% stop": replace(m, premium_target_pct=0.30),
        "looser: chase 1.00, R:R 1.0": replace(m, max_chase=1.0, min_reward_risk=1.0),
    }
    base = None
    for name, v in variants.items():
        t = replay(days, v1, v9, v)
        base = base if base is not None else t
        print(summarize(name, t, ndays))
    print(summarize("baseline: same entries, flipped", replay(days, v1, v9, m, flip=True), ndays))
    if show_trades and base:
        print("\nTrades, as configured:")
        for t in base:
            print(f"  {t['date']} {t['window']:<4} {t['dir']:<8} x{t['qty']:<2} {t['entry']:>5.2f} -> {t['exit']:>5.2f}"
                  f" ({t['ret']:+.0%}) {t['reason']:<13} {t['pnl']:+7.0f} $")
        reasons = {}
        for t in base:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        print("Exit reasons: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])))


def main() -> None:
    ap = argparse.ArgumentParser(prog="spxbot.spy03_backtest")
    ap.add_argument("--refresh", action="store_true", help="download/merge Yahoo data first")
    ap.add_argument("-c", "--config", help="use the [spy03] settings from this config file")
    a = ap.parse_args()
    if a.refresh:
        refresh()
    m = None
    if a.config:
        from .config import load_config
        m = load_config(a.config).spy03
    run(m)


if __name__ == "__main__":
    main()
