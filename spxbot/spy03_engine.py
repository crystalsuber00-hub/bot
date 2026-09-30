"""Live SPY 0/3 signals: polls SPY, keeps the day's level map, watches for reactions, alerts entries/exits.

Signal-only. Nothing here places orders; positions are paper-tracked at the option mid.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .config import Config
from .models import Bar
from .notify import Notifier
from .spy03 import (AFTERNOON, MIDDLE, MORNING, Level, Signal, build_map, check_exit, find_reaction,
                    phase, pick_contract, pick_expiration, size, thesis_broken)

log = logging.getLogger("spxbot")

FOOTER = "Educational signal, not financial advice."


class Spy03State:
    """JSON file: {"signals": [...], "days": {date: {...}}}."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.data = {"signals": [], "days": {}}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        os.replace(tmp, self.path)

    def day(self, date: str) -> dict:
        return self.data["days"].setdefault(date, {"map": None, "thesis": None, "last_bar": "", "notes": [],
                                                   "checkpoints": [], "passed": []})

    def signals(self) -> list[Signal]:
        return [Signal(**s) for s in self.data["signals"]]

    def upsert(self, sig: Signal) -> None:
        self.data["signals"] = [s for s in self.data["signals"] if s["id"] != sig.id] + [sig.to_dict()]
        self.save()


class Spy03Engine:
    def __init__(self, cfg: Config, client, notifier: Notifier, state: Spy03State):
        self.cfg, self.m, self.client, self.notify, self.state = cfg, cfg.spy03, client, notifier, state
        self.tz = ZoneInfo(cfg.schedule.timezone)
        self._bars: list[Bar] = []
        self._bars_key = ""

    def now(self) -> datetime:
        return datetime.now(self.tz)

    # --- data ----------------------------------------------------------------
    def _completed_bars(self, now: datetime) -> list[Bar]:
        """Today's completed bars (pre-market included). Refetched only when a new bar should have closed."""
        date = now.date().isoformat()
        step = self.m.bar_minutes
        boundary = now.replace(minute=now.minute - now.minute % step, second=0, microsecond=0)
        expect = (boundary - timedelta(minutes=step)).strftime("%Y-%m-%dT%H:%M")
        have = self._bars[-1].time if self._bars else ""
        if self._bars_key != date or have < expect:
            self._bars = self.client.get_bars(self.m.symbol, date, step)
            self._bars_key = date
        cut = boundary.strftime("%Y-%m-%dT%H:%M")
        return [b for b in self._bars if b.time < cut]

    @staticmethod
    def _rth(bars: list[Bar]) -> list[Bar]:
        return [b for b in bars if "09:30" <= b.time[11:] < "16:00"]

    # --- one tick --------------------------------------------------------------
    def tick(self, now: Optional[datetime] = None) -> None:
        now = now or self.now()
        date, hhmm = now.date().isoformat(), now.strftime("%H:%M")
        if now.weekday() >= 5 or not "09:30" <= hhmm < "16:05":
            return
        ph = phase(hhmm, self.m)
        if ph == "pre":
            return
        day = self.state.day(date)
        bars = self._completed_bars(now)
        rth = self._rth(bars)
        spy = self.client.get_quote(self.m.symbol).last

        if day["map"] is None and rth:
            self._build_map(date, day, bars, rth)
        levels = [Level(**z) for z in day["map"] or []]

        new_bar = bool(rth) and rth[-1].time > day["last_bar"]
        if new_bar:
            day["last_bar"] = rth[-1].time
        self._track_thesis(day, rth, ph)

        for sig in [s for s in self.state.signals() if s.status == "open"]:
            self._manage(sig, spy, rth[-1].close if new_bar else None, date, hhmm)

        self._checkpoints(day, ph, date)
        if new_bar and ph in (MORNING, AFTERNOON) and levels:
            self._look_for_entry(day, ph, date, now, rth, levels, spy)
        if hhmm >= "16:00" and "summary" not in day["checkpoints"]:
            day["checkpoints"].append("summary")
            self._summary(date)
        self.state.save()

    # --- the map ---------------------------------------------------------------
    def _build_map(self, date: str, day: dict, bars: list[Bar], rth: list[Bar]) -> None:
        start = (datetime.fromisoformat(date) - timedelta(days=10)).date().isoformat()
        daily = [b for b in self.client.get_daily(self.m.symbol, start, date) if b.time < date]
        prior = daily[-1] if daily else None
        pre = [b for b in bars if b.time[11:] < "09:30"]
        levels = build_map(prior, pre, rth[0].open, self.m)
        day["map"] = [z.to_dict() for z in levels]
        lines = "\n".join(f"  {z.price:.2f}  {z.label}" for z in reversed(levels))
        self.notify.send(f"SPY 0/3 map for {date}\n{lines}\nSPY opened {rth[0].open:.2f}. "
                         f"0DTE window {self.m.zero_dte_start}-{self.m.zero_dte_end}, "
                         f"3DTE window {self.m.three_dte_start}-{self.m.three_dte_end}.",
                         {"event": "map", "model": "spy03", "date": date, "levels": day["map"]})

    # --- thesis & clock checkpoints -----------------------------------------------
    def _track_thesis(self, day: dict, rth: list[Bar], ph: str) -> None:
        th = day["thesis"]
        if not th or th.get("broken"):
            return
        b = thesis_broken(th["direction"], th["invalidation"], [x for x in rth if x.time > th["bar"]])
        if b:
            th["broken"] = b.time
            if ph != MORNING:
                self.notify.send(f"SPY 0/3: morning {th['direction']} thesis BROKEN "
                                 f"(SPY closed {b.close:.2f} through {th['invalidation']:.2f} at {b.time[11:]}). "
                                 "No 3DTE continuation today.", {"event": "thesis_broken", "model": "spy03", **th})

    def _checkpoints(self, day: dict, ph: str, date: str) -> None:
        cps = day["checkpoints"]
        if ph == MIDDLE and MIDDLE not in cps:
            cps.append(MIDDLE)
            self.notify.send(f"SPY 0/3: {self.m.zero_dte_end} - middle window. No new trades until "
                             f"{self.m.three_dte_start}; let the market prove whether the morning story changed.\n"
                             f"Morning thesis: {self._thesis_text(day)}", {"event": "checkpoint", "model": "spy03",
                                                                          "window": MIDDLE})
        if ph == AFTERNOON and AFTERNOON not in cps:
            cps.append(AFTERNOON)
            blocked = self._afternoon_block(day, date)
            self.notify.send(f"SPY 0/3: {self.m.three_dte_start} - 3DTE window "
                             + (f"CLOSED today: {blocked}." if blocked else
                                f"open for {day['thesis']['direction']} continuation only. {self._thesis_text(day)}"),
                             {"event": "checkpoint", "model": "spy03", "window": AFTERNOON, "blocked": blocked})

    @staticmethod
    def _thesis_text(day: dict) -> str:
        th = day["thesis"]
        if not th:
            return "none (no clean morning reaction)"
        state = f"broken at {th['broken'][11:]}" if th.get("broken") else "intact"
        side = "above" if th["direction"] == "bullish" else "below"
        return f"{th['direction']} from {th['label']} {th['level']:.2f}, valid while SPY holds {side} " \
               f"{th['invalidation']:.2f} ({state})"

    def _afternoon_block(self, day: dict, date: str) -> Optional[str]:
        if any(s.date == date and s.window == MORNING and s.status == "closed" and s.pnl() < 0
               for s in self.state.signals()):
            return "the morning trade lost; a morning loss does not create an afternoon trade"
        th = day["thesis"]
        if self.m.require_morning_thesis and not th:
            return "no morning thesis to continue"
        if th and th.get("broken"):
            return "the morning thesis was invalidated"
        return None

    # --- entries -----------------------------------------------------------------
    def _blocked(self, date: str, ph: str) -> Optional[str]:
        m = self.m
        if date in [str(d) for d in m.skip_dates]:
            return "scheduled sit-out day (skip_dates)"
        sigs = self.state.signals()
        if any(s.status == "open" for s in sigs):
            return "a signal is already open"
        today = [s for s in sigs if s.date == date]
        realized = sum(s.pnl() for s in today if s.status == "closed")
        if m.max_daily_loss and realized <= -m.max_daily_loss:
            return f"daily loss cap reached ({realized:+.0f} $)"
        cap = m.max_morning_trades if ph == MORNING else m.max_afternoon_trades
        if sum(s.window == ph for s in today) >= cap:
            return f"max {cap} {ph.upper()} signal(s) already taken"
        return None

    def _pass(self, day: dict, text: str) -> None:
        """Tell the user about a setup the model walked away from (once per reason per day)."""
        if text in day["passed"]:
            return
        day["passed"].append(text)
        self.notify.send(f"SPY 0/3 PASS: {text}", {"event": "pass", "model": "spy03", "reason": text})

    def _look_for_entry(self, day: dict, ph: str, date: str, now: datetime, rth: list[Bar],
                        levels: list[Level], spy: float) -> None:
        m = self.m
        reaction, passed = find_reaction(rth, levels, m)
        if reaction is None:
            for p in passed:
                self._pass(day, p)
            return
        if ph == MORNING and not day["thesis"]:
            day["thesis"] = {"direction": reaction.direction, "level": reaction.level.price,
                             "label": reaction.level.label, "invalidation": reaction.invalidation,
                             "bar": reaction.bar.time, "broken": None}
        blocked = self._blocked(date, ph)
        if blocked:
            log.info("reaction at %.2f ignored: %s", reaction.level.price, blocked)
            return
        name = f"{reaction.direction} reaction at {reaction.level.label} {reaction.level.price:.2f}"
        if ph == AFTERNOON:
            why_not = self._afternoon_block(day, date)
            if why_not:
                return self._pass(day, f"{name}: {why_not}")
            if day["thesis"] and reaction.direction != day["thesis"]["direction"]:
                return self._pass(day, f"{name}: against the morning {day['thesis']['direction']} thesis")
        if abs(spy - reaction.level.price) > m.max_chase:
            return self._pass(day, f"{name}: SPY already at {spy:.2f}, {abs(spy - reaction.level.price):.2f} "
                                   "past the level (no chasing)")

        dte = 0 if ph == MORNING else m.three_dte_days
        exp = pick_expiration(self.client.get_expirations(m.symbol), now.date(), dte)
        if not exp:
            return self._pass(day, f"{name}: no {dte}DTE expiration listed")
        chain = self.client.get_chain(m.symbol, exp, m.symbol, reaction.right, near=5)
        opt, why = pick_contract(chain, reaction.right, spy, m)
        if opt is None:
            return self._pass(day, f"{name}: no contract passed the strike checks ({why})")
        qty, per = size(opt.mid, m)
        if qty < 1:
            return self._pass(day, f"{name}: {opt.strike:g}{reaction.right[0].upper()} stop risk ${per:.0f}/contract "
                                   f"exceeds the {m.risk_pct:.0%} risk budget (${m.account_size * m.risk_pct:.0f})")

        sig = Signal(id=f"{date}-{ph}-{now.strftime('%H%M')}", date=date, window=ph, direction=reaction.direction,
                     symbol=opt.symbol, right=reaction.right, strike=opt.strike, expiration=exp, quantity=qty,
                     entry=round(opt.mid, 2), entry_time=now.isoformat(timespec="seconds"), spy_entry=spy,
                     level=reaction.level.price, level_label=reaction.level.label,
                     invalidation=reaction.invalidation, target=reaction.target, why=reaction.why,
                     last_mid=round(opt.mid, 2))
        self.state.upsert(sig)
        up = "above" if sig.direction == "bullish" else "below"
        lo = "below" if sig.direction == "bullish" else "above"
        tp, sl = sig.entry * (1 + m.premium_target_pct), sig.entry * (1 - m.premium_stop_pct)
        self.notify.send(
            f"SPY 0/3 {dte}DTE {sig.right.upper()} signal ({sig.direction})\n"
            f"BUY {qty}x SPY {exp} {opt.strike:g}{sig.right[0].upper()} @ ~{sig.entry:.2f} "
            f"(bid {opt.bid:.2f} / ask {opt.ask:.2f}, delta {abs(opt.delta):.2f})\n"
            f"Why: {reaction.why}\n"
            f"Invalidation: SPY closes {lo} {sig.invalidation:.2f}\n"
            f"Exit: option {tp:.2f} (+{m.premium_target_pct:.0%}) / {sl:.2f} (-{m.premium_stop_pct:.0%}), "
            f"or SPY {sig.target:.2f} (next zone {up}, {reaction.reward_risk:.1f}:1)\n"
            f"Risk: ~${per * qty:.0f} ({qty} x ${per:.0f}) of the ${m.account_size * m.risk_pct:.0f} budget\n{FOOTER}",
            {"event": "entry", "model": "spy03", **sig.to_dict()})

    # --- exits -------------------------------------------------------------------
    def _manage(self, sig: Signal, spy: float, last_close: Optional[float], date: str, hhmm: str) -> None:
        if sig.window == MORNING and sig.date < date:
            return self._close(sig, 0.0 if sig.last_mid is None else sig.last_mid, date, hhmm,
                               "expired", "0DTE expired while the bot was not running (P&L uses last seen mid)")
        q = self.client.get_option_quotes([sig.symbol]).get(sig.symbol)
        if q is None or q.bid <= 0 and q.ask <= 0:
            log.warning("no quote for %s", sig.symbol)
            return
        mid = round(q.mid, 2)
        sig.last_mid = mid
        ex = check_exit(sig, mid, spy, last_close, date, hhmm, self.m)
        if ex:
            self._close(sig, mid, date, hhmm, *ex)
        else:
            self.state.upsert(sig)

    def _close(self, sig: Signal, price: float, date: str, hhmm: str, kind: str, text: str) -> None:
        sig.status, sig.exit, sig.exit_reason = "closed", price, kind
        sig.exit_time = f"{date}T{hhmm}"
        self.state.upsert(sig)
        pnl = sig.pnl()
        self.notify.send(f"SPY 0/3 EXIT {sig.quantity}x SPY {sig.expiration} {sig.strike:g}{sig.right[0].upper()} "
                         f"@ ~{price:.2f}\nReason: {text}\nP&L: {pnl:+.0f} $ "
                         f"({(price - sig.entry) / sig.entry:+.0%})\n{FOOTER}",
                         {"event": "exit", "model": "spy03", "pnl": pnl, **sig.to_dict()})

    def _summary(self, date: str) -> None:
        today = [s for s in self.state.signals() if s.date == date]
        day = self.state.day(date)
        if not today:
            text = "No trade today. \"No trade\" is a valid endpoint."
        else:
            rows = [f"  {s.window.upper()} {s.right} {s.strike:g}: "
                    + (f"{s.pnl():+.0f} $ ({s.exit_reason})" if s.status == "closed" else f"open, last {s.last_mid}")
                    for s in today]
            text = "\n".join(rows) + f"\nRealized: {sum(s.pnl() for s in today if s.status == 'closed'):+.0f} $"
        passed = len(day["passed"])
        self.notify.send(f"SPY 0/3 day summary {date}\n{text}" + (f"\nSetups passed on: {passed}" if passed else ""),
                         {"event": "summary", "model": "spy03", "date": date})

    # --- loop / check ---------------------------------------------------------------
    def run_forever(self, until: Optional[str] = None) -> None:
        self.notify.send(f"SPY 0/3 signals started ({self.cfg.broker})"
                         + (f"; stops at {until}." if until else "."), {"event": "started", "model": "spy03"})
        fails = 0
        while True:
            if until and self.now().strftime("%H:%M") >= until:
                self.notify.send("SPY 0/3 signals finished for the day.", {"event": "stopped", "model": "spy03"})
                return
            try:
                self.tick()
                fails = 0
            except Exception as e:
                fails += 1
                log.exception("tick failed")
                if fails == 3:
                    self.notify.send(f"SPY 0/3 can't reach the broker/data: {e!r}.", {"event": "unreachable"})
            getattr(self.client, "sleep", time.sleep)(self.cfg.poll_seconds)

    def check(self, now: Optional[datetime] = None) -> bool:
        """Verify data and alerts. Places no orders."""
        now = now or self.now()
        m, date = self.m, now.date().isoformat()
        q = self.client.get_quote(m.symbol)
        print(f"[ok] {m.symbol}: last {q.last:.2f}")
        bars = self.client.get_bars(m.symbol, date, m.bar_minutes)
        rth = self._rth(bars)
        print(f"[{'ok' if bars else '--'}] {len(bars)} {m.bar_minutes}-min bars today ({len(rth)} regular session)")
        start = (now - timedelta(days=10)).date().isoformat()
        daily = [b for b in self.client.get_daily(m.symbol, start, date) if b.time < date]
        print(f"[{'ok' if daily else '!!'}] prior day: {daily[-1] if daily else 'none'}")
        levels = build_map(daily[-1] if daily else None, [b for b in bars if b.time[11:] < "09:30"],
                           rth[0].open if rth else q.last, m)
        print("[ok] map: " + ", ".join(f"{z.price:.2f} {z.label}" for z in levels))
        exps = self.client.get_expirations(m.symbol)
        ok = True
        for dte in (0, m.three_dte_days):
            exp = pick_expiration(exps, now.date(), dte)
            if not exp:
                print(f"[--] no {dte}DTE expiration listed (weekend/holiday?)")
                continue
            chain = self.client.get_chain(m.symbol, exp, m.symbol, "call", near=5)
            opt, why = pick_contract(chain, "call", q.last, m)
            if opt is None:
                print(f"[--] {dte}DTE {exp}: {len(chain)} calls loaded, none passed the checks: {why}")
                ok = ok and bool(chain)
            else:
                qty, per = size(opt.mid, m)
                print(f"[ok] {dte}DTE {exp}: would use {opt.strike:g}C @ {opt.mid:.2f} (delta {abs(opt.delta):.2f}), "
                      f"{qty} contract(s) at ${per:.0f} stop risk each")
        self.notify.send("SPY 0/3 test alert: if you can read this, notifications work.", {"event": "test"})
        print("[ok] test alert sent")
        return ok
