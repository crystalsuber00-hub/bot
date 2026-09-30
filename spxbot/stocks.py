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
from .spy03 import Level, Reaction, build_map, find_reaction, pick_contract, pick_expiration

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

    @property
    def shadow(self) -> bool:
        """Tracked silently for --report (below the alert bar); never alerted."""
        return bool(self.extra.get("shadow"))


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
        expect = (boundary - timedelta(minutes=step)).strftime("%Y-%m-%dT%H:%M")  # the bar that just closed
        key, bars = self._bars.get(symbol, ("", []))
        if key != cut or not any(b.time == expect for b in bars):  # new bar due, or the feed hadn't caught up
            bars = self.client.get_bars(symbol, now.date().isoformat(), step)
            self._bars[symbol] = (cut, bars)
        return [b for b in bars if b.time < cut]

    def _stale(self, rth: list[Bar], now: datetime) -> bool:
        """True when the newest bar is old: market closed (holiday, early close) or the feed stopped."""
        if not rth:
            return True
        last = datetime.fromisoformat(rth[-1].time).replace(tzinfo=self.tz)
        return now - last > timedelta(minutes=self.k.bar_minutes + 10)

    def tick(self, now: Optional[datetime] = None) -> None:
        now = now or self.now()
        date, hhmm = now.date().isoformat(), now.strftime("%H:%M")
        if now.weekday() >= 5 or not "09:30" <= hhmm < "16:05":
            return
        day = self.state.day(date)
        for t in [t for t in self.state.trades() if t.status == "open"]:
            try:
                self._manage(t, now, date, hhmm)
            except Exception as e:  # keep managing the others; this one is retried next tick
                log.warning("%s: manage failed (%s)", t.id, e)
        k = self.k
        short = sum(t.date == date and not t.shadow for t in self.state.trades()) < k.min_signals_per_day
        if k.entry_start <= hhmm < k.entry_end or (short and hhmm < k.last_resort_end and hhmm >= k.entry_start):
            self._scan(day, now, date)
        if hhmm >= "16:00" and not day["summary"]:
            day["summary"] = True
            self._summary(date)
        self.state.save()

    # --- the morning map ----------------------------------------------------------
    def _map_for(self, sym: str, day: dict, now: datetime, date: str) -> Optional[str]:
        """Build one ticker's levels (and note whether it has a 0DTE today). Returns its map line, or None."""
        bars = self._completed(sym, now)
        rth = [b for b in bars if b.time[11:] >= "09:30"]
        if not rth:
            return None
        start = (now - timedelta(days=10)).date().isoformat()
        daily = [b for b in self.client.get_daily(sym, start, date) if b.time < date]
        prior = daily[-1] if daily else None
        pre = [b for b in bars if b.time[11:] < "09:30"]
        levels = build_map(prior, pre, rth[0].open, rules_for(self.k, bar_range(rth)))
        if not pick_expiration(self.client.get_expirations(sym), now.date(), 0) and sym not in day["no_0dte"]:
            day["no_0dte"].append(sym)
        day["maps"][sym] = [z.to_dict() for z in levels]
        last = rth[-1].close
        chg = f"{(last - prior.close) / prior.close * 100:+.1f}%" if prior else ""
        tag = "" if sym not in day["no_0dte"] else "  [no 0DTE today, skipped]"
        return f"{sym} {last:.2f} {chg}{tag}\n    " + " | ".join(f"{z.price:.2f} {z.label}" for z in reversed(levels))

    def _prepare(self, day: dict, now: datetime, date: str) -> None:
        """Levels for every ticker; one map alert once data is flowing. Tickers that fail are retried later."""
        rows = []
        for sym in self.k.watchlist:
            if sym in day["maps"]:
                continue
            try:
                row = self._map_for(sym, day, now, date)
            except Exception as e:
                log.warning("%s: map failed (%s)", sym, e)
                row = None
            if row:
                rows.append(row)
        if day.get("prepared") or not rows:
            return  # already announced, or no data at all yet (market holiday / feed not up): try again next tick
        day["prepared"] = True
        missing = [s for s in self.k.watchlist if s not in day["maps"]]
        k = self.k
        self.notify.send(f"0DTE stock map {date} (levels high to low)\n" + "\n".join(rows) +
                         (f"\nNo data yet (retrying): {', '.join(missing)}" if missing else "") +
                         f"\nSignals {k.entry_start}-{k.entry_end} ET, 1 contract under ${k.max_contract_cost:g}, "
                         f"high conviction = {k.min_conviction}/7+, at least {k.min_signals_per_day} a day, "
                         f"all out by {k.exit_time}.",
                         {"event": "map", "model": "stocks", "date": date, "maps": day["maps"],
                          "no_0dte": day["no_0dte"]})

    # --- entries -----------------------------------------------------------------
    def _scan(self, day: dict, now: datetime, date: str) -> None:
        k = self.k
        if not day.get("prepared") or len(day["maps"]) < len(k.watchlist):
            self._prepare(day, now, date)
        for sym in k.watchlist:
            trades = self.state.trades()
            n_today = sum(t.date == date and not t.shadow for t in trades)
            if k.max_trades_per_day and n_today >= k.max_trades_per_day:
                return
            if sym in day["no_0dte"] or any(t.symbol == sym and t.status == "open" and not t.shadow for t in trades):
                continue
            try:
                self._scan_one(sym, day, now, date, n_today)
            except Exception as e:  # one bad ticker must not stop the others
                log.warning("%s: scan failed (%s)", sym, e)
        hhmm = now.strftime("%H:%M")
        if (k.trend_fallback_time and hhmm >= k.trend_fallback_time and not day.get("trend_tried")
                and sum(t.date == date and not t.shadow for t in self.state.trades()) < k.min_signals_per_day):
            try:
                self._trend_fallback(day, now, date)
            except Exception as e:
                log.warning("trend fallback failed (%s)", e)

    def _scan_one(self, sym: str, day: dict, now: datetime, date: str, n_today: int) -> None:
        k = self.k
        if sym not in day["maps"]:
            return
        bars = self._completed(sym, now)
        rth = [b for b in bars if b.time[11:] >= "09:30"]
        unit = bar_range(rth)
        if self._stale(rth, now) or unit <= 0 or day["last_bar"].get(sym) == rth[-1].time:
            return
        day["last_bar"][sym] = rth[-1].time
        rules = rules_for(k, unit)
        if n_today < k.min_signals_per_day and now.strftime("%H:%M") >= k.fallback_any_time:
            rules = replace(rules, min_body_ratio=k.relaxed_body_ratio, min_reward_risk=k.relaxed_reward_risk,
                            max_chase=k.relaxed_chase_atr * unit, max_level_crosses=5)
        r, _ = find_reaction(rth, [Level(**z) for z in day["maps"][sym]], rules)
        if r is None:
            return
        spot = self.client.get_quote(sym).last
        if abs(spot - r.level.price) > rules.max_chase:
            log.info("%s: price %.2f already ran past %.2f (no chasing)", sym, spot, r.level.price)
            return
        market = {m: [x for x in self._completed(m, now) if x.time[11:] >= "09:30"]
                  for m in k.market_symbols if m != sym}
        checks = conviction(r, rth, spot, market, k, rules.touch_tolerance)
        score = sum(ok for _, ok in checks)
        need = self._needed(now.strftime("%H:%M"), n_today)
        shadow = score < need
        if shadow:
            log.info("%s: %s setup at %.2f scored %d/7, below %d; no alert", sym, r.direction,
                     r.level.price, score, need)
            busy = any(t.symbol == sym and t.status == "open" for t in self.state.trades())
            if not k.track_all_setups or busy:
                return
        self._contract_and_enter(sym, r, rules, spot, now, date, day, rth, checks, shadow=shadow)

    def _contract_and_enter(self, sym, r, rules, spot, now, date, day, rth, checks, label=None,
                            shadow=False) -> bool:
        chain = self.client.get_chain(sym, date, sym, r.right, near=max(spot * 0.05, 5))
        opt, why = pick_contract(chain, r.right, spot, rules)  # priced now: one contract under the cap
        if opt is None:
            log.info("%s: %s setup, no 0DTE %s fits (%s)", sym, r.direction, r.right, why)
            return False
        self._enter(sym, r, opt, date, spot, now, date, day, rth, checks, label, shadow)
        return True

    def _trend_fallback(self, day: dict, now: datetime, date: str) -> None:
        """Daily minimum still not met late in the day: the strongest trending ticker whose contract fits."""
        k = self.k
        cands = []
        for sym in k.watchlist:
            if sym in day["no_0dte"] or sym not in day["maps"]:
                continue
            try:
                rth = [b for b in self._completed(sym, now) if b.time[11:] >= "09:30"]
            except Exception as e:
                log.warning("%s: no bars (%s)", sym, e)
                continue
            unit = bar_range(rth)
            if len(rth) >= 7 and unit > 0 and not self._stale(rth, now):
                cands.append(((rth[-1].close - rth[0].open) / unit, sym, rth, unit))  # move in average-bar units
        day["trend_tried"] = True
        for strength, sym, rth, unit in sorted(cands, key=lambda c: -abs(c[0])):
            up = strength > 0
            last, recent = rth[-1], rth[-6:]
            stop = (min(b.low for b in recent) - 0.2 * unit) if up else (max(b.high for b in recent) + 0.2 * unit)
            risk = abs(last.close - stop)
            target = last.close + 2 * risk if up else last.close - 2 * risk
            r = Reaction("bullish" if up else "bearish", Level(round(rth[0].open, 2), "today's open"), last,
                         last.close, round(stop, 2), round(target, 2), 2.0,
                         f"{sym} is the day's strongest {'up' if up else 'down'} trend that fits the budget "
                         f"({(last.close - rth[0].open) / rth[0].open * 100:+.2f}% since the open); no level setup today")
            try:
                spot = self.client.get_quote(sym).last
                market = {m: [x for x in self._completed(m, now) if x.time[11:] >= "09:30"]
                          for m in k.market_symbols if m != sym}
                checks = conviction(r, rth, spot, market, k, 0.2 * unit)
                if self._contract_and_enter(sym, r, rules_for(k, unit), spot, now, date, day, rth, checks,
                                            label="LAST-RESORT TREND SIGNAL (no level setup today; take it small or skip)"):
                    return
            except Exception as e:
                log.warning("%s: trend fallback failed (%s)", sym, e)
        log.info("trend fallback: no ticker had a 0DTE contract under $%g", k.max_contract_cost)

    def _needed(self, hhmm: str, n_today: int) -> int:
        """Conviction required right now: the full bar, lowered late in the day until the daily minimum is met."""
        k = self.k
        if n_today >= k.min_signals_per_day or hhmm < k.fallback_time:
            return k.min_conviction
        return k.fallback_min_conviction if hhmm < k.fallback_any_time else 0

    def _enter(self, sym, r, opt, exp, spot, now, date, day, rth, checks, label=None, shadow=False) -> None:
        k = self.k
        t = Trade(id=f"{date}-{sym}-{now.strftime('%H%M')}", date=date, symbol=sym, option=opt.symbol,
                  right=r.right, strike=opt.strike, expiration=exp, contracts=k.contracts,
                  entry=round(opt.mid, 2), entry_time=now.isoformat(timespec="seconds"), stock_entry=spot,
                  stop=round(r.invalidation, 2), target=round(r.target, 2), level=r.level.price,
                  level_label=r.level.label, why=r.why.replace("SPY", sym),
                  extra={"bar": r.bar.time, "conviction": sum(ok for _, ok in checks), "label": label or "",
                         "entry_bid": opt.bid, "entry_ask": opt.ask, "shadow": shadow})
        if shadow:
            t.id += "-shadow"
        self.state.upsert(t)
        if shadow:
            return
        c = t.contracts * 100
        cost = t.entry * c
        tp, sl = t.entry * (1 + k.premium_target_pct), t.entry * (1 - k.premium_stop_pct)
        up = t.right == "call"
        spread_pct = (opt.ask - opt.bid) / opt.mid * 100 if opt.mid else 0
        opened = rth[0].open
        rr = abs(t.target - spot) / abs(spot - t.stop) if spot != t.stop else 0
        n = sum(x.date == date and not x.shadow for x in self.state.trades())
        score = sum(ok for _, ok in checks)
        grade = label or ("HIGH CONVICTION" if score >= k.min_conviction else
                          f"LOWER CONVICTION ({score}/7, sent to meet the {k.min_signals_per_day}-a-day minimum)")
        levels = " | ".join(f"{z['price']:.2f} {z['label']}" for z in reversed(day["maps"][sym]))
        self.notify.send(
            f"{grade}\n0DTE {sym} {t.right.upper()} signal ({r.direction}) {now.strftime('%H:%M')} ET  "
            f"[signal #{n} today]\n"
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
            f"  Room vs risk on {sym}: {r.reward_risk:.1f}:1 at the signal candle, {rr:.1f}:1 from the price now\n"
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
            if hhmm >= self.k.exit_time:
                last = t.extra.get("last_mid", t.entry)
                self._close(t, last, "time", f"time exit {self.k.exit_time} (no live quote; last seen {last:.2f})", now)
            return
        t.extra["last_mid"] = round(q.mid, 2)
        t.extra["last_bid"], t.extra["last_ask"] = q.bid, q.ask
        bars = self._completed(t.symbol, now)
        if hhmm >= "13:00" and self._stale([b for b in bars if b.time[11:] >= "09:30"], now):
            return self._close(t, t.extra["last_mid"], "time",
                               "market closed early today (no new prices); the contract has expired", now)
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
        if t.shadow:
            return
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
        t.extra["exit_bid"], t.extra["exit_ask"] = t.extra.get("last_bid"), t.extra.get("last_ask")
        t.exit_time = now.isoformat(timespec="seconds")
        self.state.upsert(t)
        if t.shadow:
            return
        held = (now - datetime.fromisoformat(t.entry_time)).total_seconds() / 60
        head = {"target": "TAKE PROFIT HIT", "stop": "STOP HIT", "time": "TIME EXIT",
                "invalidation": "IDEA INVALIDATED", "level_target": "STOCK HIT TARGET"}.get(kind, "EXIT")
        self.notify.send(f"EXIT NOW - {head}\nSELL {t.contracts}x {t.symbol} {t.expiration} ${t.strike:g} {t.right.upper()} "
                         f"@ ~{price:.2f} (bought ~{t.entry:.2f})\nReason: {text}\n"
                         f"P&L: {t.pnl():+.0f} $ ({(price - t.entry) / t.entry if t.entry else 0:+.0%}), "
                         f"held {held:.0f} min. Use a limit order near {price:.2f}.\n{FOOTER}",
                         {"event": "exit", "model": "stocks", "pnl": t.pnl(), **asdict(t)})

    def _summary(self, date: str) -> None:
        today = [t for t in self.state.trades() if t.date == date and not t.shadow]
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
                if type(e).__name__ == "SchwabLoginNeeded" and fails == 1:
                    self.notify.send(f"SCHWAB LOGIN NEEDED - no signals until you log in again.\n{e}",
                                     {"event": "login_needed"})
                elif fails == 3:
                    self.notify.send(f"0DTE stock signals can't reach the data: {e!r}.", {"event": "unreachable"})
            getattr(self.client, "sleep", time.sleep)(self.cfg.poll_seconds)
