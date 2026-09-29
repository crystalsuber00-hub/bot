from __future__ import annotations

import csv
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .models import Position
from .notify import Notifier
from .state import State
from .strategy import ExitSignal, build_setup, check_exit, choose_side, pause_reason

log = logging.getLogger("spxbot")

TICK = 0.05


def _round_tick(x: float) -> float:
    return round(round(x / TICK) * TICK, 2)


class Engine:
    """Polling state machine.

    Position lifecycle:  pending_entry -> open -> pending_exit -> closed
                              \\-> cancelled (entry never filled)
    In `signal` mode fills are simulated at the mid, so positions go straight to open/closed.
    In `trade` mode a position is only `open` once the broker confirms the fill.
    """

    def __init__(self, cfg: Config, client, notifier: Notifier, state: State):
        self.cfg, self.client, self.notify, self.state = cfg, client, notifier, state
        self.tz = ZoneInfo(cfg.schedule.timezone)
        self._warned_unmanaged = False

    @property
    def live(self) -> bool:
        return self.cfg.mode == "trade"

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def _age(self, iso: str, now: datetime) -> float:
        return (now - datetime.fromisoformat(iso)).total_seconds() if iso else float("inf")

    # --- windows -----------------------------------------------------------
    def _entry_window(self, now: datetime) -> tuple[datetime, datetime]:
        sch = self.cfg.schedule
        h, m = map(int, sch.market_open.split(":"))
        opened = now.replace(hour=h, minute=m, second=0, microsecond=0)
        start = opened + timedelta(minutes=sch.entry_delay_minutes)
        return start, start + timedelta(minutes=sch.entry_window_minutes)

    # --- one tick ----------------------------------------------------------
    def tick(self, now: datetime | None = None) -> None:
        now = now or self.now()
        active = self.state.active_positions()
        if now.weekday() >= 5 and not active:
            return
        date = now.date().isoformat()

        halt = self.state.halt_info()
        if halt and halt["kind"] == "frozen":
            if halt["alerted"] != date:
                halt["alerted"] = date
                self.state.save()
                self.notify.send(f"BOT FROZEN - manual action needed\n{halt['reason']}\n"
                                 "Fix the position at your broker, then run: spxbot --clear-halt",
                                 {"event": "frozen", "reason": halt["reason"]})
            return

        if self.live and not self._reconcile(now, active):
            return

        for pos in active:
            self._advance(pos, now)

        if now.weekday() >= 5:
            return
        if not self.state.position_for(date) and not self.state.skipped(date):
            start, end = self._entry_window(now)
            if start <= now <= end:
                blocked = self._entry_blocked(now)
                if blocked:
                    self._skip(date, blocked)
                else:
                    self._try_enter(date, now)
            elif now > end:
                self.state.mark_skipped(date, "entry window passed with no trade")

    # --- guards ------------------------------------------------------------
    def _kill_requested(self) -> bool:
        kf = self.cfg.execution.kill_file
        return bool(kf) and Path(kf).exists()

    def _entry_blocked(self, now: datetime) -> str | None:
        st = self.cfg.strategy
        if now.date().isoformat() in [str(d) for d in st.skip_dates]:
            return "scheduled sit-out day (skip_dates)"
        closed = sorted((p for p in self.state.all_positions() if p.status == "closed" and p.exit_debit is not None),
                        key=lambda p: (p.date, p.exit_time or ""))
        paused = pause_reason([(p.date, p.realized()) for p in closed], now.date(), st)
        if paused:
            return paused
        if self._kill_requested():
            return f"kill switch active ({self.cfg.execution.kill_file} file exists)"
        halt = self.state.halt_info()
        if halt:
            return f"entries halted: {halt['reason']}"
        return None

    # --- broker reconciliation (trade mode) ----------------------------------
    def _reconcile(self, now: datetime, active: list[Position]) -> bool:
        """Compare state with the broker's real positions. Returns False if the bot froze."""
        try:
            held = self.client.positions()
        except Exception:
            log.exception("could not read broker positions; skipping reconciliation this tick")
            return True
        grace = self.cfg.execution.fill_grace_seconds
        for pos in active:
            if pos.status not in ("open", "pending_exit") or self._age(pos.filled_at, now) < grace:
                continue
            s, l = held.get(pos.short_symbol, 0), held.get(pos.long_symbol, 0)
            if (s, l) == (-pos.quantity, pos.quantity):
                continue
            if s == 0 and l == 0:
                if pos.status == "pending_exit":
                    continue  # the exit fill may simply not be processed yet
                pos.status, pos.exit_reason = "closed", "closed outside the bot (not found at broker)"
                pos.exit_time = now.isoformat(timespec="seconds")
                self.state.upsert(pos)
                self.notify.send(f"POSITION GONE: {pos.short_strike:g}/{pos.long_strike:g} is no longer at your "
                                 "broker (closed manually?). Marked closed; P&L not tracked.",
                                 {"event": "external_close", **pos.to_dict()})
                continue
            reason = (f"position mismatch on {pos.short_strike:g}/{pos.long_strike:g}: bot expects "
                      f"short {-pos.quantity}/long {pos.quantity}, broker has short leg {s}, long leg {l}")
            self.state.halt("frozen", reason)
            self.notify.send(f"BOT FROZEN - {reason}", {"event": "frozen", "reason": reason})
            return False
        mine = {x for p in active for x in (p.short_symbol, p.long_symbol)}
        extra = {k: v for k, v in held.items() if v and k not in mine}
        if extra and not self._warned_unmanaged:
            self._warned_unmanaged = True
            self.notify.send(f"Note: broker holds {self.cfg.symbol} option legs the bot isn't managing: {extra}",
                             {"event": "unmanaged", "positions": extra})
        return True

    # --- dispatch ----------------------------------------------------------
    def _advance(self, pos: Position, now: datetime) -> None:
        if self._is_expired(pos, now):
            return self._expire(pos, now)
        if pos.status == "pending_entry":
            self._advance_entry(pos, now)
        elif pos.status == "open":
            self._advance_open(pos, now)
        elif pos.status == "pending_exit":
            self._advance_exit(pos, now)

    def _is_expired(self, pos: Position, now: datetime) -> bool:
        today = now.date().isoformat()
        return today > pos.expiration or (today == pos.expiration and now.strftime("%H:%M") >= "16:00")

    def _expire(self, pos: Position, now: datetime) -> None:
        if pos.status == "pending_entry":
            self._cancel_quietly(pos.entry_order_id)
            pos.status = "cancelled"
            self.state.upsert(pos)
            return
        self._cancel_quietly(pos.exit_order_id)
        debit = None
        try:
            spx = self.client.get_quote(self.cfg.symbol).last
            k1, k2 = pos.short_strike, pos.long_strike
            debit = (max(k1 - spx, 0) - max(k2 - spx, 0)) if pos.side == "put_credit" \
                else (max(spx - k1, 0) - max(spx - k2, 0))
        except Exception:
            log.exception("could not estimate settlement")
        pos.status, pos.exit_debit = "closed", debit
        pos.exit_reason = "expired (settlement estimated from SPX last; confirm at your broker)"
        pos.exit_time = now.isoformat(timespec="seconds")
        self.state.upsert(pos)
        pl = f" | est. P&L ${pos.pnl(debit):+,.0f}" if debit is not None else ""
        self.notify.send(f"EXPIRED SPX {pos.short_strike:g}/{pos.long_strike:g} {pos.side}{pl}",
                         {"event": "expired", **pos.to_dict()})
        self._check_total_loss()

    def _cancel_quietly(self, order_id) -> None:
        if self.live and order_id:
            try:
                self.client.cancel_order(order_id)
            except Exception:
                log.exception("cancel failed for order %s", order_id)

    # --- entry ---------------------------------------------------------------
    def _try_enter(self, date: str, now: datetime) -> None:
        cfg, st = self.cfg, self.cfg.strategy
        quote = self.client.get_quote(cfg.symbol)
        side, move_pct, why = choose_side(quote, st)
        if side is None:
            return self._skip(date, why)

        expiration = (now.date() + timedelta(days=cfg.dte)).isoformat()
        chain = self.client.get_chain(cfg.symbol, expiration, cfg.option_root,
                                      "put" if side == "put_credit" else "call")
        if not chain:
            return self._skip(date, f"no option chain for {expiration}")
        setup, why = build_setup(side, move_pct, chain, st)
        if setup is None:
            return self._skip(date, why)

        pos = Position(
            id=f"{date}-{side}", date=date, side=side, expiration=expiration,
            short_symbol=setup.short.symbol, long_symbol=setup.long.symbol,
            short_strike=setup.short.strike, long_strike=setup.long.strike,
            credit=setup.credit, est_credit=setup.credit, entry_limit=setup.credit,
            quantity=st.quantity, short_delta=setup.short.delta,
            spx_at_entry=quote.last, entry_time=now.isoformat(timespec="seconds"),
        )
        kind = "PUT CREDIT SPREAD" if side == "put_credit" else "CALL CREDIT SPREAD"
        trend = "up" if move_pct > 0 else "down"
        head = (f"SPX {quote.last:.2f} ({move_pct:+.2f}% {trend})\n"
                f"Sell {pos.short_strike:g} / Buy {pos.long_strike:g} exp {expiration}\n"
                f"Short delta {abs(pos.short_delta):.2f} | limit credit {pos.credit:.2f}\n"
                f"Take profit at {st.profit_target:.0%} (buy back <= {pos.credit * (1 - st.profit_target):.2f})")

        if not self.live:
            pos.status, pos.filled_at = "open", now.isoformat(timespec="seconds")
            self.state.upsert(pos)
            self.notify.send(f"ENTRY {kind} SPX x{pos.quantity}\n{head}\n[signal only - no order placed]",
                             {"event": "entry", **pos.to_dict()})
            return

        pos.status, pos.order_ts = "pending_entry", now.isoformat(timespec="seconds")
        self.state.upsert(pos)  # persist intent before the order goes out
        pos.entry_order_id = self.client.open_spread(pos.short_symbol, pos.long_symbol, pos.quantity, pos.credit)
        self.state.upsert(pos)
        _, end = self._entry_window(now)
        self.notify.send(f"ENTRY ORDER PLACED {kind} SPX x{pos.quantity}\n{head}\n"
                         f"Waiting for fill; will cancel at {end.strftime('%H:%M')} if unfilled.",
                         {"event": "entry_order", **pos.to_dict()})

    def _advance_entry(self, pos: Position, now: datetime) -> None:
        ex = self.cfg.execution
        if not pos.entry_order_id:  # crashed between saving intent and placing/recording the order
            held = self.client.positions()
            if held.get(pos.short_symbol, 0) == -pos.quantity and held.get(pos.long_symbol, 0) == pos.quantity:
                return self._entry_filled(pos, pos.quantity, None, now)
            return self._entry_dead(pos, "entry order was never recorded")

        st = self.client.order_status(pos.entry_order_id)
        if st.state == "filled":
            return self._entry_filled(pos, st.filled_qty or pos.quantity, st.avg_price, now)
        if st.state in ("cancelled", "rejected"):
            if st.filled_qty > 0:
                return self._entry_filled(pos, st.filled_qty, st.avg_price, now)
            return self._entry_dead(pos, f"entry order {st.state}")
        if st.state == "unknown":
            held = self.client.positions()
            if held.get(pos.short_symbol, 0) < 0:
                return self._entry_filled(pos, -held[pos.short_symbol], None, now)
            return self._entry_dead(pos, "entry order not found at broker")

        # still working
        _, end = self._entry_window(now)
        if pos.cancel_requested or now > end or pos.date != now.date().isoformat() or self._kill_requested():
            pos.cancel_requested = True
            self.state.upsert(pos)
            self.client.cancel_order(pos.entry_order_id)  # resolved on the next tick's status check
            return
        if pos.nudges < ex.max_nudges and self._age(pos.order_ts, now) >= ex.nudge_seconds:
            floor = max(self.cfg.strategy.min_credit, pos.est_credit - ex.max_entry_concession)
            new = _round_tick(pos.entry_limit - ex.nudge_step)
            if new >= floor - 1e-9:
                self.client.replace_order(pos.entry_order_id, new, opening=True)
                log.info("entry repriced %.2f -> %.2f", pos.entry_limit, new)
                pos.entry_limit, pos.nudges, pos.order_ts = new, pos.nudges + 1, now.isoformat(timespec="seconds")
                self.state.upsert(pos)

    def _entry_filled(self, pos: Position, qty: int, avg: float | None, now: datetime) -> None:
        pos.quantity, pos.status = qty, "open"
        pos.credit = round(avg if avg else pos.entry_limit, 2)
        pos.filled_at = now.isoformat(timespec="seconds")
        pos.cancel_requested = False
        self.state.upsert(pos)
        kind = "PUT CREDIT SPREAD" if pos.side == "put_credit" else "CALL CREDIT SPREAD"
        self.notify.send(
            f"ENTRY FILLED {kind} SPX x{pos.quantity}\n"
            f"Sold {pos.short_strike:g} / bought {pos.long_strike:g} exp {pos.expiration}\n"
            f"Filled credit {pos.credit:.2f} (signal mid {pos.est_credit:.2f})\n"
            f"Take profit when buy-back <= {pos.credit * (1 - self.cfg.strategy.profit_target):.2f}",
            {"event": "entry", **pos.to_dict()})

    def _entry_dead(self, pos: Position, reason: str) -> None:
        pos.status = "cancelled"
        self.state.upsert(pos)
        self.state.mark_skipped(pos.date, reason)
        self.notify.send(f"NO TRADE today: {reason} (nothing filled, no position)",
                         {"event": "entry_cancelled", "date": pos.date, "reason": reason})

    def _skip(self, date: str, reason: str) -> None:
        self.state.mark_skipped(date, reason)
        self.notify.send(f"NO TRADE today: {reason}", {"event": "skip", "date": date, "reason": reason})

    # --- managing an open position ---------------------------------------------
    def _advance_open(self, pos: Position, now: datetime) -> None:
        quotes = self.client.get_option_quotes([pos.short_symbol, pos.long_symbol])
        s, l = quotes.get(pos.short_symbol), quotes.get(pos.long_symbol)
        if not s or not l:
            return
        mid = round(s.mid - l.mid, 2)
        self._log_history(pos, now, s.mid, l.mid, mid)

        sig: ExitSignal | None = None
        ex = self.cfg.execution
        day_pnl = self.state.realized_on(pos.date) + pos.pnl(mid)
        if self._kill_requested():
            sig = ExitSignal("kill", f"kill switch ({ex.kill_file} file)")
        elif ex.max_daily_loss and day_pnl <= -ex.max_daily_loss:
            sig = ExitSignal("kill", f"max daily loss hit (${day_pnl:,.0f} <= -${ex.max_daily_loss:,.0f})")
        else:
            sig = check_exit(pos, mid, self.cfg.strategy, now.strftime("%H:%M"))
        if not sig:
            return

        pos.exit_kind, pos.exit_reason = sig.kind, sig.text
        if not self.live:
            return self._close(pos, mid, now)
        self._place_exit(pos, sig, s, l, mid, now)

    def _natural(self, s, l) -> float:
        nat = s.ask - l.bid if s.ask and l.bid is not None else s.mid - l.mid
        return round(max(nat, TICK), 2)

    def _cap(self, price: float) -> float:
        return min(_round_tick(max(price, TICK)), self.cfg.strategy.spread_width)

    def _place_exit(self, pos: Position, sig: ExitSignal, s, l, mid: float, now: datetime) -> None:
        ex = self.cfg.execution
        if pos.exit_attempts >= ex.max_exit_attempts:
            reason = f"exit order failed {pos.exit_attempts} times on {pos.short_strike:g}/{pos.long_strike:g}"
            self.state.halt("frozen", reason)
            self.notify.send(f"BOT FROZEN - {reason}. Close it manually.", {"event": "frozen", "reason": reason})
            return
        price = self._cap(self._natural(s, l) if sig.kind != "profit" else mid)
        pos.exit_attempts += 1
        pos.status, pos.exit_limit, pos.nudges = "pending_exit", price, 0
        pos.order_ts = now.isoformat(timespec="seconds")
        self.state.upsert(pos)
        pos.exit_order_id = self.client.close_spread(pos.short_symbol, pos.long_symbol, pos.quantity, price)
        self.state.upsert(pos)
        self.notify.send(f"EXIT ORDER PLACED SPX {pos.short_strike:g}/{pos.long_strike:g} {pos.side}\n"
                         f"{sig.text}\nBuy back limit {price:.2f} (credit {pos.credit:.2f}), will reprice if unfilled",
                         {"event": "exit_order", **pos.to_dict()})

    def _advance_exit(self, pos: Position, now: datetime) -> None:
        ex = self.cfg.execution
        if not pos.exit_order_id:
            pos.status = "open"
            self.state.upsert(pos)
            return
        st = self.client.order_status(pos.exit_order_id)
        if st.state == "filled":
            return self._close(pos, st.avg_price if st.avg_price else pos.exit_limit, now)
        if st.state in ("cancelled", "rejected"):
            pos.status, pos.exit_order_id = "open", None
            self.state.upsert(pos)
            self.notify.send(f"Exit order {st.state} for {pos.short_strike:g}/{pos.long_strike:g}; will retry.",
                             {"event": "exit_retry", **pos.to_dict()})
            return
        if st.state == "unknown":
            held = self.client.positions()
            if not held.get(pos.short_symbol, 0) and not held.get(pos.long_symbol, 0):
                return self._close(pos, pos.exit_limit, now)
            pos.status, pos.exit_order_id = "open", None
            self.state.upsert(pos)
            return

        if self._age(pos.order_ts, now) < ex.nudge_seconds:
            return
        quotes = self.client.get_option_quotes([pos.short_symbol, pos.long_symbol])
        s, l = quotes.get(pos.short_symbol), quotes.get(pos.long_symbol)
        if not s or not l:
            return
        natural = self._cap(self._natural(s, l))
        if pos.exit_kind == "profit" and pos.nudges < ex.max_nudges:
            new = min(_round_tick(pos.exit_limit + ex.nudge_step), natural)
        else:
            new = natural  # urgent, or patience exhausted: pay up to get out
        if new > pos.exit_limit + 1e-9:
            self.client.replace_order(pos.exit_order_id, new, opening=False)
            log.info("exit repriced %.2f -> %.2f", pos.exit_limit, new)
            pos.exit_limit = new
        pos.nudges += 1
        pos.order_ts = now.isoformat(timespec="seconds")
        self.state.upsert(pos)

    def _close(self, pos: Position, debit: float, now: datetime) -> None:
        pos.status, pos.exit_debit = "closed", round(debit, 2)
        pos.exit_time = now.isoformat(timespec="seconds")
        self.state.upsert(pos)
        self.notify.send(
            f"EXIT {'FILLED ' if self.live else ''}SPX {pos.short_strike:g}/{pos.long_strike:g} {pos.side}\n"
            f"{pos.exit_reason}\nBought back at {pos.exit_debit:.2f} (credit {pos.credit:.2f}) | "
            f"P&L ${pos.pnl(pos.exit_debit):+,.0f}",
            {"event": "exit", **pos.to_dict()})
        self._check_total_loss()

    def _check_total_loss(self) -> None:
        cap = self.cfg.execution.max_total_loss
        total = self.state.realized_total()
        if cap and total <= -cap and not self.state.halt_info():
            reason = f"cumulative realized loss ${-total:,.0f} reached limit ${cap:,.0f}"
            self.state.halt("entries", reason)
            self.notify.send(f"NEW ENTRIES HALTED: {reason}", {"event": "halt", "reason": reason})

    # --- price history ---------------------------------------------------------
    def _log_history(self, pos: Position, now: datetime, short_mid: float, long_mid: float, debit: float) -> None:
        """Append one row per poll so the spread can be charted smoothly."""
        try:
            spx = self.client.get_quote(self.cfg.symbol).last
            path = Path(self.cfg.history_dir) / f"{pos.date}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with path.open("a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "spx", "short_mid", "long_mid", "spread_mid"])
                w.writerow([now.strftime("%H:%M:%S"), spx, round(short_mid, 2), round(long_mid, 2), debit])
        except Exception:
            log.exception("history logging failed")

    def run_forever(self, until: str | None = None) -> None:
        """Poll until killed, or until `until` (HH:MM in the schedule timezone) so a scheduler can start it daily."""
        log.info("spxbot running in %s mode", self.cfg.mode)
        self.notify.send(f"spxbot started ({self.cfg.mode} mode, {self.cfg.broker}). "
                         f"Watching for the {self.cfg.schedule.market_open} open"
                         + (f"; stops at {until}." if until else "."), {"event": "started"})
        fails = 0
        while True:
            if until and self.now().strftime("%H:%M") >= until:
                self.notify.send("spxbot finished for the day.", {"event": "stopped"})
                return
            try:
                self.tick()
                fails = 0
            except Exception as e:
                fails += 1
                log.exception("tick failed")
                if fails == 3:  # ~90s of failures: tell the user, once per outage
                    self.notify.send(f"spxbot can't reach the broker/data: {e!r}. "
                                     "Is IB Gateway running and logged in?", {"event": "unreachable"})
            # IB clients must keep their event loop pumped while idle
            getattr(self.client, "sleep", time.sleep)(self.cfg.poll_seconds)
