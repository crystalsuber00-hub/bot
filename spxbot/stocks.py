"""0DTE option signals on stocks: SPY 0/3 level -> reaction rules on any stock, one contract under $150, high conviction only.

Each morning every watchlist stock (any share price) gets its own map (prior-day high/low/close, pre-market
high/low). A decisive 5-min bar off / through a level in the entry window is a signal: bullish -> call,
bearish -> put, in the contract that expires TODAY. The strike is the one nearest the money whose single
contract costs less than `max_contract_cost` at that moment (going further out of the money if needed, but not
below `min_delta`). Stocks without an expiration today are skipped. Signal-only: alerts + paper tracking.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .config import Config, Spy03, Stocks
from .models import Bar
from .notify import Notifier
from .spy03 import Level, build_map, find_reaction, pick_contract, pick_expiration

log = logging.getLogger("spxbot")
FOOTER = "Educational signal, not financial advice."


def bar_range(bars: list[Bar], n: int = 24) -> float:
    """Average high-low of the last n regular-session bars (the stock's 'unit' of movement)."""
    recent = bars[-n:]
    return sum(b.high - b.low for b in recent) / len(recent) if recent else 0.0


def rules_for(k: Stocks, unit: float) -> Spy03:
    """SPY 0/3 reaction and contract rules scaled to this stock's bar range and the contract budget."""
    return replace(Spy03(), round_step=0, manual_levels=[], use_premarket=k.use_premarket, max_zones=6,
                   merge_distance=0.5 * unit, touch_tolerance=k.touch_atr * unit, confirm_distance=k.confirm_atr * unit,
                   max_chase=k.max_chase_atr * unit, invalidation_buffer=k.stop_atr * unit,
                   min_body_ratio=k.min_body_ratio, min_reward_risk=k.min_reward_risk,
                   default_reward_risk=k.default_reward_risk, max_level_crosses=3, chop_lookback=6,
                   min_delta=k.min_delta, max_delta=k.max_delta, max_spread_pct=k.max_spread_pct,
                   min_premium=k.min_premium,
                   max_premium=round(k.max_contract_cost / 100 - 0.01, 2))  # strictly under the cap


def conviction(r, rth: list[Bar], spot: float, market: dict[str, list[Bar]], k: Stocks,
               touch: float) -> list[tuple[str, bool]]:
    """The 7-point checklist behind a setup. `market` = regular-session bars of SPY/QQQ (the stock itself excluded)."""
    up = r.direction == "bullish"
    b, L, label = r.bar, r.level.price, r.level.label
    rng = b.high - b.low
    earlier = [x for x in rth if x.time < b.time]
    fresh = not any(x.low - touch <= L <= x.high + touch for x in earlier)
    opened = rth[0].open
    agree = [((m[-1].close > m[0].open) if up else (m[-1].close < m[0].open)) for m in market.values() if m]
    key = any(w in label for w in ("prior-day high", "prior-day low", "pre-market", "your level"))
    return [
        ("Key level (prior-day or pre-market high/low)", key),
        ("Two levels in the same zone", "+" in label),
        ("First test of the level today", fresh),
        (f"With the stock's move since the open ({'up' if up else 'down'})", (spot > opened) if up else (spot < opened)),
        ("Market agrees (" + "/".join(market) + ")", bool(agree) and all(agree)),
        (f"Strong candle (body >= {k.strong_body_ratio:.0%})", rng > 0 and abs(b.close - b.open) / rng >= k.strong_body_ratio),
        (f"Room to run >= {k.strong_reward_risk:g}:1", r.reward_risk >= k.strong_reward_risk),
    ]


@dataclass
class Trade:
    id: str
    date: str
    symbol: str          # the stock
    option: str          # broker option symbol
    right: str           # call | put
    strike: float
    expiration: str
    contracts: int
    entry: float         # option premium per share at the signal
    entry_time: str
    stock_entry: float
    stop: float          # stock price that proves the idea wrong
    target: float        # stock price at the next level
    level: float
    level_label: str
    why: str
    status: str = "open"
    exit: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def pnl(self) -> float:
        return 0.0 if self.exit is None else (self.exit - self.entry) * 100 * self.contracts


def check_exit(t: Trade, mid: float, stock: float, last_close: Optional[float], hhmm: str,
               k: Stocks) -> Optional[tuple[str, str]]:
    up = t.right == "call"
    if mid >= t.entry * (1 + k.premium_target_pct):
        return "target", f"option +{k.premium_target_pct:.0%} ({t.entry:.2f} -> {mid:.2f})"
    if mid <= t.entry * (1 - k.premium_stop_pct):
        return "stop", f"option -{k.premium_stop_pct:.0%} ({t.entry:.2f} -> {mid:.2f})"
    if last_close is not None and ((last_close < t.stop) if up else (last_close > t.stop)):
        return "invalidation", f"{t.symbol} closed {last_close:.2f} through {t.stop:.2f}"
    if (stock >= t.target) if up else (stock <= t.target):
        return "level_target", f"{t.symbol} reached {t.target:.2f}"
    if hhmm >= k.exit_time:
        return "time", f"time exit {k.exit_time} (0DTE)"
    return None


class StocksState:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data = {"trades": [], "days": {}}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        os.replace(tmp, self.path)

    def day(self, date: str) -> dict:
        return self.data["days"].setdefault(date, {"maps": {}, "last_bar": {}, "no_0dte": [], "summary": False})

    def trades(self) -> list[Trade]:
        return [Trade(**t) for t in self.data["trades"]]

    def upsert(self, t: Trade) -> None:
        self.data["trades"] = [x for x in self.data["trades"] if x["id"] != t.id] + [asdict(t)]
        self.save()


class StocksEngine:
    def __init__(self, cfg: Config, client, notifier: Notifier, state: StocksState):
        self.cfg, self.k, self.client, self.notify, self.state = cfg, cfg.stocks, client, notifier, state
        self.tz = ZoneInfo(cfg.schedule.timezone)
        self._bars: dict[str, tuple[str, list[Bar]]] = {}

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def _completed(self, symbol: str, now: datetime) -> list[Bar]:
        step = self.k.bar_minutes
        boundary = now.replace(minute=now.minute - now.minute % step, second=0, microsecond=0)
        cut = boundary.strftime("%Y-%m-%dT%H:%M")
        key, bars = self._bars.get(symbol, ("", []))
        if key != cut:
            bars = self.client.get_bars(symbol, now.date().isoformat(), step)
            self._bars[symbol] = (cut, bars)
        return [b for b in bars if b.time < cut]

    def tick(self, now: Optional[datetime] = None) -> None:
        now = now or self.now()
        date, hhmm = now.date().isoformat(), now.strftime("%H:%M")
        if now.weekday() >= 5 or not "09:30" <= hhmm < "16:05":
            return
        day = self.state.day(date)
        for t in [t for t in self.state.trades() if t.status == "open"]:
            self._manage(t, now, date, hhmm)
        if self.k.entry_start <= hhmm < self.k.entry_end:
            self._scan(day, now, date)
        if hhmm >= "16:00" and not day["summary"]:
            day["summary"] = True
            self._summary(date)
        self.state.save()

    # --- the morning map ----------------------------------------------------------
    def _prepare(self, day: dict, now: datetime, date: str) -> None:
        """Once a day: levels for every ticker, whether it has an option expiring today, one map alert."""
        rows = []
        start = (now - timedelta(days=10)).date().isoformat()
        for sym in self.k.watchlist:
            try:
                bars = self._completed(sym, now)
                rth = [b for b in bars if b.time[11:] >= "09:30"]
                if not rth:
                    rows.append(f"{sym}: no data yet")
                    continue
                daily = [b for b in self.client.get_daily(sym, start, date) if b.time < date]
                prior = daily[-1] if daily else None
                pre = [b for b in bars if b.time[11:] < "09:30"]
                levels = build_map(prior, pre, rth[0].open, rules_for(self.k, bar_range(rth)))
                day["maps"][sym] = [z.to_dict() for z in levels]
                if not pick_expiration(self.client.get_expirations(sym), now.date(), 0):
                    day["no_0dte"].append(sym)
                last = rth[-1].close
                chg = f"{(last - prior.close) / prior.close * 100:+.1f}%" if prior else ""
                tag = "" if sym not in day["no_0dte"] else "  [no 0DTE today, skipped]"
                lv = " | ".join(f"{z.price:.2f} {z.label}" for z in reversed(levels))
                rows.append(f"{sym} {last:.2f} {chg}{tag}\n    {lv}")
            except Exception as e:
                log.warning("%s: map failed (%s)", sym, e)
                rows.append(f"{sym}: data error")
        day["prepared"] = True
        k = self.k
        self.notify.send(f"0DTE stock map {date} (levels high to low)\n" + "\n".join(rows) +
                         f"\nSignals {k.entry_start}-{k.entry_end} ET, 1 contract under ${k.max_contract_cost:g}, "
                         f"max {k.max_trades_per_day} today, all out by {k.exit_time}.",
                         {"event": "map", "model": "stocks", "date": date, "maps": day["maps"],
                          "no_0dte": day["no_0dte"]})

    # --- entries -----------------------------------------------------------------
    def _scan(self, day: dict, now: datetime, date: str) -> None:
        k = self.k
        if not day.get("prepared"):
            self._prepare(day, now, date)
        trades = self.state.trades()
        if any(t.status == "open" for t in trades) or sum(t.date == date for t in trades) >= k.max_trades_per_day:
            return
        for sym in k.watchlist:
            if sym in day["no_0dte"]:
                continue
            try:
                bars = self._completed(sym, now)
            except Exception as e:
                log.warning("%s: no bars (%s)", sym, e)
                continue
            rth = [b for b in bars if b.time[11:] >= "09:30"]
            unit = bar_range(rth)
            if not rth or unit <= 0:
                continue
            rules = rules_for(k, unit)
            if sym not in day["maps"]:
                continue
            if day["last_bar"].get(sym) == rth[-1].time:
                continue
            day["last_bar"][sym] = rth[-1].time
            r, _ = find_reaction(rth, [Level(**z) for z in day["maps"][sym]], rules)
            if r is None:
                continue
            spot = self.client.get_quote(sym).last
            market = {m: [x for x in self._completed(m, now) if x.time[11:] >= "09:30"]
                      for m in k.market_symbols if m != sym}
            checks = conviction(r, rth, spot, market, k, rules.touch_tolerance)
            score = sum(ok for _, ok in checks)
            if score < k.min_conviction:
                log.info("%s: %s setup at %.2f scored %d/7, below %d; no alert", sym, r.direction,
                         r.level.price, score, k.min_conviction)
                continue
            exp = date
            chain = self.client.get_chain(sym, exp, sym, r.right, near=max(spot * 0.05, 5))
            opt, why = pick_contract(chain, r.right, spot, rules)  # priced now: one contract under the cap
            if opt is None:
                log.info("%s: %s setup, no 0DTE %s fits (%s)", sym, r.direction, r.right, why)
                continue
            self._enter(sym, r, opt, exp, spot, now, date, day, rth, checks)
            return

    def _enter(self, sym, r, opt, exp, spot, now, date, day, rth, checks) -> None:
        k = self.k
        t = Trade(id=f"{date}-{sym}-{now.strftime('%H%M')}", date=date, symbol=sym, option=opt.symbol,
                  right=r.right, strike=opt.strike, expiration=exp, contracts=k.contracts,
                  entry=round(opt.mid, 2), entry_time=now.isoformat(timespec="seconds"), stock_entry=spot,
                  stop=round(r.invalidation, 2), target=round(r.target, 2), level=r.level.price,
                  level_label=r.level.label, why=r.why.replace("SPY", sym),
                  extra={"bar": r.bar.time, "conviction": sum(ok for _, ok in checks)})
        self.state.upsert(t)
        c = t.contracts * 100
        cost = t.entry * c
        tp, sl = t.entry * (1 + k.premium_target_pct), t.entry * (1 - k.premium_stop_pct)
        up = t.right == "call"
        spread_pct = (opt.ask - opt.bid) / opt.mid * 100 if opt.mid else 0
        opened = rth[0].open
        rr = abs(t.target - spot) / abs(spot - t.stop) if spot != t.stop else 0
        n = sum(x.date == date for x in self.state.trades())
        levels = " | ".join(f"{z['price']:.2f} {z['label']}" for z in reversed(day["maps"][sym]))
        self.notify.send(
            f"0DTE {sym} {t.right.upper()} signal ({r.direction}) {now.strftime('%H:%M')} ET  [#{n} of max "
            f"{k.max_trades_per_day} today]\n"
            f"BUY {t.contracts}x {sym} {exp} ${t.strike:g} {t.right.upper()} @ ~{t.entry:.2f} = ${cost:.0f} "
            f"(under ${k.max_contract_cost:g})\n"
            f"Contract: bid {opt.bid:.2f} / ask {opt.ask:.2f} (spread {spread_pct:.0f}%), delta {abs(opt.delta):.2f}, "
            f"expires today 4:00 PM ET. Use a limit order near {t.entry:.2f}; don't chase above {opt.ask:.2f}.\n"
            f"Stock: {sym} {spot:.2f} ({(spot - opened) / opened * 100:+.2f}% vs open {opened:.2f})\n"
            f"Why: {t.why}\n"
            f"Conviction {sum(ok for _, ok in checks)}/7:\n"
            + "".join(f"  {'[x]' if ok else '[ ]'} {name}\n" for name, ok in checks) +
            f"Plan:\n"
            f"  Take profit: option {tp:.2f} (+{k.premium_target_pct:.0%}, +${(tp - t.entry) * c:.0f}) "
            f"or {sym} {'reaches' if up else 'falls to'} {t.target:.2f} (next level)\n"
            f"  Stop: option {sl:.2f} (-{k.premium_stop_pct:.0%}, -${(t.entry - sl) * c:.0f}) "
            f"or {sym} closes a 5-min bar {'below' if up else 'above'} {t.stop:.2f}\n"
            f"  Time exit: {k.exit_time} ET at the latest. Most you can lose: ${cost:.0f} if it expires worthless.\n"
            f"  Room vs risk on {sym}: {rr:.1f}:1\n"
            f"{sym} levels: {levels}\n{FOOTER}",
            {"event": "entry", "model": "stocks", **asdict(t)})
        self.notify.send(
            f"EXIT PLAN {sym} ${t.strike:g} {t.right.upper()} (bought ~{t.entry:.2f})\n"
            f"  TAKE PROFIT: sell at {tp:.2f} (+{k.premium_target_pct:.0%}, +${(tp - t.entry) * c:.0f})\n"
            f"  STOP: sell if it drops to {sl:.2f} (-{k.premium_stop_pct:.0%}, -${(t.entry - sl) * c:.0f})\n"
            f"  Also sell if {sym} closes a 5-min bar {'below' if up else 'above'} {t.stop:.2f}, "
            f"or at {k.exit_time} ET no matter what.\n"
            f"Robinhood: after it fills, place a Stop Limit sell: stop {sl:.2f}, limit {max(sl - 0.10, 0.01):.2f}. "
            f"Sell at {tp:.2f} yourself when the take-profit alert comes (Robinhood may not allow both orders at "
            f"once). Stops there use the option price only; watch for EXIT NOW on the {sym} price rule.",
            {"event": "exit_plan", "model": "stocks", "take_profit": round(tp, 2), "option_stop": round(sl, 2),
             "stock_stop": t.stop, "time_exit": k.exit_time, **asdict(t)})

    # --- exits -------------------------------------------------------------------
    def _manage(self, t: Trade, now: datetime, date: str, hhmm: str) -> None:
        if t.date < date:
            return self._close(t, 0.0, "expired", "0DTE expired while the bot was off; check your account", now)
        q = self.client.get_option_quotes([t.option]).get(t.option)
        if q is None or (q.bid <= 0 and q.ask <= 0):
            log.warning("no quote for %s", t.option)
            return
        bars = self._completed(t.symbol, now)
        new = [b for b in bars if b.time > t.extra.get("last_seen", t.extra.get("bar", ""))]
        if new:
            t.extra["last_seen"] = new[-1].time
        mid = round(q.mid, 2)
        ex = check_exit(t, mid, self.client.get_quote(t.symbol).last, new[-1].close if new else None, hhmm, self.k)
        if not ex:
            self._warn(t, mid)
        if ex:
            self._close(t, round(q.mid, 2), ex[0], ex[1], now)
        else:
            self.state.upsert(t)

    def _warn(self, t: Trade, mid: float) -> None:
        """One heads-up each when the option is most of the way to its take-profit or its stop."""
        k, c = self.k, t.contracts * 100
        tp, sl = t.entry * (1 + k.premium_target_pct), t.entry * (1 - k.premium_stop_pct)
        name = f"{t.symbol} ${t.strike:g} {t.right.upper()}"
        if mid >= t.entry * (1 + k.warn_pct * k.premium_target_pct) and not t.extra.get("warned_tp"):
            t.extra["warned_tp"] = True
            self.notify.send(f"ALMOST AT TAKE PROFIT: {name} now {mid:.2f} ({(mid - t.entry) / t.entry:+.0%}, "
                             f"{(mid - t.entry) * c:+.0f} $). Target {tp:.2f}. Get ready to sell.",
                             {"event": "near_target", "model": "stocks", "mid": mid, "take_profit": round(tp, 2)})
        if mid <= t.entry * (1 - k.warn_pct * k.premium_stop_pct) and not t.extra.get("warned_sl"):
            t.extra["warned_sl"] = True
            self.notify.send(f"NEAR STOP: {name} now {mid:.2f} ({(mid - t.entry) / t.entry:+.0%}, "
                             f"{(mid - t.entry) * c:+.0f} $). Stop {sl:.2f}. Be ready to sell.",
                             {"event": "near_stop", "model": "stocks", "mid": mid, "option_stop": round(sl, 2)})

    def _close(self, t: Trade, price: float, kind: str, text: str, now: datetime) -> None:
        t.status, t.exit, t.exit_reason = "closed", price, kind
        t.exit_time = now.isoformat(timespec="seconds")
        self.state.upsert(t)
        held = (now - datetime.fromisoformat(t.entry_time)).total_seconds() / 60
        head = {"target": "TAKE PROFIT HIT", "stop": "STOP HIT", "time": "TIME EXIT",
                "invalidation": "IDEA INVALIDATED", "level_target": "STOCK HIT TARGET"}.get(kind, "EXIT")
        self.notify.send(f"EXIT NOW - {head}\nSELL {t.contracts}x {t.symbol} {t.expiration} ${t.strike:g} {t.right.upper()} "
                         f"@ ~{price:.2f} (bought ~{t.entry:.2f})\nReason: {text}\n"
                         f"P&L: {t.pnl():+.0f} $ ({(price - t.entry) / t.entry if t.entry else 0:+.0%}), "
                         f"held {held:.0f} min. Use a limit order near {price:.2f}.\n{FOOTER}",
                         {"event": "exit", "model": "stocks", "pnl": t.pnl(), **asdict(t)})

    def _summary(self, date: str) -> None:
        today = [t for t in self.state.trades() if t.date == date]
        body = "\n".join(f"  {t.symbol} {t.strike:g}{t.right[0].upper()}: {t.pnl():+.0f} $ ({t.exit_reason or 'open'})"
                         for t in today) or "  no trade today"
        self.notify.send(f"0DTE stock signals {date}\n{body}", {"event": "summary", "model": "stocks", "date": date})

    def run_forever(self, until: Optional[str] = None) -> None:
        self.notify.send("0DTE stock signals started" + (f"; stops at {until}." if until else "."),
                         {"event": "started", "model": "stocks"})
        fails = 0
        while True:
            if until and self.now().strftime("%H:%M") >= until:
                self.notify.send("0DTE stock signals finished for the day.", {"event": "stopped", "model": "stocks"})
                return
            try:
                self.tick()
                fails = 0
            except Exception as e:
                fails += 1
                log.exception("tick failed")
                if fails == 3:
                    self.notify.send(f"0DTE stock signals can't reach the data: {e!r}.", {"event": "unreachable"})
            getattr(self.client, "sleep", time.sleep)(self.cfg.poll_seconds)
