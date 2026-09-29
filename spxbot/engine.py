from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .models import Position
from .notify import Notifier
from .state import State
from .strategy import build_setup, check_exit, choose_side

log = logging.getLogger("spxbot")


class Engine:
    def __init__(self, cfg: Config, client, notifier: Notifier, state: State):
        self.cfg, self.client, self.notify, self.state = cfg, client, notifier, state
        self.tz = ZoneInfo(cfg.schedule.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

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
        if now.weekday() >= 5:
            return
        date = now.date().isoformat()
        pos = self.state.position_for(date)
        if pos and pos.status == "open":
            self._manage(pos, now)
        elif not pos and not self.state.skipped(date):
            start, end = self._entry_window(now)
            if start <= now <= end:
                self._try_enter(date, now)
            elif now > end:
                self.state.mark_skipped(date, "entry window passed with no trade")

    def _try_enter(self, date: str, now: datetime) -> None:
        cfg, st = self.cfg, self.cfg.strategy
        quote = self.client.get_quote(cfg.symbol)
        side, move_pct, why = choose_side(quote, st)
        if side is None:
            return self._skip(date, why)

        expiration = (now.date() + timedelta(days=cfg.dte)).isoformat()
        chain = self.client.get_chain(cfg.symbol, expiration, cfg.option_root)
        if not chain:
            return self._skip(date, f"no option chain for {expiration}")
        setup, why = build_setup(side, move_pct, chain, st)
        if setup is None:
            return self._skip(date, why)

        pos = Position(
            id=f"{date}-{side}", date=date, side=side, expiration=expiration,
            short_symbol=setup.short.symbol, long_symbol=setup.long.symbol,
            short_strike=setup.short.strike, long_strike=setup.long.strike,
            credit=setup.credit, quantity=st.quantity, short_delta=setup.short.delta,
            spx_at_entry=quote.last, entry_time=now.isoformat(timespec="seconds"),
        )
        if cfg.mode == "trade":
            pos.entry_order_id = self.client.open_spread(
                pos.short_symbol, pos.long_symbol, pos.quantity, pos.credit)
        self.state.upsert(pos)

        kind = "PUT CREDIT SPREAD" if side == "put_credit" else "CALL CREDIT SPREAD"
        trend = "up" if move_pct > 0 else "down"
        self.notify.send(
            f"ENTRY {kind} SPX x{pos.quantity}\n"
            f"SPX {quote.last:.2f} ({move_pct:+.2f}% {trend} -> fade)\n"
            f"Sell {pos.short_strike:g} / Buy {pos.long_strike:g} exp {expiration}\n"
            f"Short delta {abs(pos.short_delta):.2f} | limit credit {pos.credit:.2f}\n"
            f"Take profit at {st.profit_target:.0%} (buy back <= {pos.credit * (1 - st.profit_target):.2f})"
            + ("" if cfg.mode == "trade" else "\n[signal only - no order placed]"),
            {"event": "entry", **pos.to_dict()},
        )

    def _skip(self, date: str, reason: str) -> None:
        self.state.mark_skipped(date, reason)
        self.notify.send(f"NO TRADE today: {reason}", {"event": "skip", "date": date, "reason": reason})

    def _manage(self, pos: Position, now: datetime) -> None:
        quotes = self.client.get_option_quotes([pos.short_symbol, pos.long_symbol])
        s, l = quotes.get(pos.short_symbol), quotes.get(pos.long_symbol)
        if not s or not l:
            return
        debit = round(s.mid - l.mid, 2)
        reason = check_exit(pos, debit, self.cfg.strategy, now.strftime("%H:%M"))
        if not reason:
            return
        if self.cfg.mode == "trade":
            pos.exit_order_id = self.client.close_spread(
                pos.short_symbol, pos.long_symbol, pos.quantity, debit)
        pos.status, pos.exit_debit, pos.exit_reason = "closed", debit, reason
        pos.exit_time = now.isoformat(timespec="seconds")
        self.state.upsert(pos)
        self.notify.send(
            f"EXIT SPX {pos.short_strike:g}/{pos.long_strike:g} {pos.side}\n"
            f"{reason}\nBuy back at {debit:.2f} (credit {pos.credit:.2f}) | P&L ${pos.pnl(debit):+,.0f}",
            {"event": "exit", **pos.to_dict()},
        )

    def run_forever(self) -> None:
        log.info("spxbot running in %s mode", self.cfg.mode)
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")
            time.sleep(self.cfg.poll_seconds)
