"""Trade-mode order tracking, reconciliation and safety switches, against a scripted fake broker."""
import pytest

from spxbot.config import Config
from spxbot.engine import Engine
from spxbot.models import OrderStatus
from spxbot.state import State

from test_bot import WED, Fake, Sink

D = "2026-09-30"


class Broker(Fake):
    def __init__(self, last):
        super().__init__(last)
        self.status, self.placed, self.cancelled, self.replaced, self.held = {}, [], [], [], {}
        self.n = 0

    def _new(self, kind, args):
        self.n += 1
        oid = f"{kind}{self.n}"
        self.placed.append((kind, args))
        self.status[oid] = OrderStatus("working")
        return oid

    def open_spread(self, *a): return self._new("open", a)
    def close_spread(self, *a): return self._new("close", a)
    def order_status(self, oid): return self.status[oid]
    def cancel_order(self, oid):
        self.cancelled.append(oid)
        if self.status[oid].state == "working":
            self.status[oid] = OrderStatus("cancelled")
    def replace_order(self, oid, price, opening): self.replaced.append((oid, price, opening))
    def positions(self): return dict(self.held)

    def fill(self, oid, price, qty=1, entry=True, short=None, long=None):
        self.status[oid] = OrderStatus("filled", qty, price)
        if short:
            sign = -1 if entry else 1
            self.held[short] = self.held.get(short, 0) + sign * qty
            self.held[long] = self.held.get(long, 0) - sign * qty
            self.held = {k: v for k, v in self.held.items() if v}


def make(tmp_path, last=6120, qty=1):
    tmp_path.mkdir(exist_ok=True)
    cfg = Config(mode="trade")
    cfg.strategy.min_credit = 0.1
    cfg.strategy.quantity = qty
    cfg.execution.fill_grace_seconds = 0
    cfg.execution.kill_file = str(tmp_path / "KILL")
    cfg.execution.max_daily_loss = 0
    cfg.state_file = str(tmp_path / "s.json")
    cfg.history_dir = str(tmp_path / "h")
    b, sink = Broker(last), Sink()
    return Engine(cfg, b, sink, State(cfg.state_file)), b, sink


def enter_and_fill(e, b, price=None, qty=1):
    e.tick(WED(9, 41))
    pos = e.state.position_for(D)
    b.fill(pos.entry_order_id, price or pos.entry_limit, qty, True, pos.short_symbol, pos.long_symbol)
    e.tick(WED(9, 42))
    return e.state.position_for(D)


def events(sink):
    return [m[1]["event"] for m in sink.msgs if m[1]]


def test_entry_not_recorded_as_open_until_filled_then_uses_actual_fill(tmp_path):
    e, b, sink = make(tmp_path)
    e.tick(WED(9, 41))
    pos = e.state.position_for(D)
    assert pos.status == "pending_entry" and events(sink) == ["entry_order"]
    est = pos.credit
    b.fill(pos.entry_order_id, est - 0.05, 1, True, pos.short_symbol, pos.long_symbol)
    e.tick(WED(9, 42))
    pos = e.state.position_for(D)
    assert pos.status == "open" and pos.credit == pytest.approx(est - 0.05)
    assert events(sink)[-1] == "entry"
    e.tick(WED(9, 43))
    assert len(b.placed) == 1  # one trade per day


def test_unfilled_entry_is_repriced_then_cancelled_at_window_end(tmp_path):
    e, b, sink = make(tmp_path)
    e.cfg.strategy.min_credit = 0.05
    e.tick(WED(9, 40))
    pos = e.state.position_for(D)
    e.tick(WED(9, 41)); e.tick(WED(9, 42)); e.tick(WED(9, 43))
    assert [round(r[1], 2) for r in b.replaced] == [round(pos.est_credit - 0.05 * i, 2) for i in (1, 2, 3)]
    assert all(r[2] is True for r in b.replaced)
    e.tick(WED(9, 44)); assert not b.cancelled  # nudges exhausted but still inside the window
    e.tick(WED(9, 46))   # past 9:45 -> cancel requested
    assert b.cancelled
    e.tick(WED(9, 47))   # cancellation confirmed
    assert e.state.position_for(D).status == "cancelled" and e.state.skipped(D)
    assert not e.state.active_positions() and len(b.placed) == 1
    assert events(sink)[-1] == "entry_cancelled"


def test_entry_concession_floor_respected(tmp_path):
    e, b, _ = make(tmp_path)
    e.cfg.execution.max_entry_concession = 0.05
    e.cfg.execution.max_nudges = 10
    e.tick(WED(9, 40)); e.tick(WED(9, 41)); e.tick(WED(9, 42)); e.tick(WED(9, 43))
    assert len(b.replaced) == 1


def test_partial_fill_kept_when_remainder_cancelled(tmp_path):
    e, b, _ = make(tmp_path, qty=2)
    e.tick(WED(9, 41))
    pos = e.state.position_for(D)
    e.tick(WED(9, 46))
    b.status[pos.entry_order_id] = OrderStatus("cancelled", 1, pos.entry_limit)
    b.held = {pos.short_symbol: -1, pos.long_symbol: 1}
    e.tick(WED(9, 47))
    p = e.state.position_for(D)
    assert p.status == "open" and p.quantity == 1


def test_exit_only_closes_after_fill_and_reprices_toward_market(tmp_path):
    e, b, sink = make(tmp_path)
    pos = enter_and_fill(e, b)
    b.mark = pos.credit * 0.5
    e.tick(WED(11, 0))
    pos = e.state.position_for(D)
    assert pos.status == "pending_exit" and b.placed[-1][0] == "close"
    assert events(sink)[-1] == "exit_order"
    e.tick(WED(11, 1))  # still working, nudge due
    b.mark = pos.credit * 0.9  # market moved against us
    e.tick(WED(11, 2))
    assert b.replaced and b.replaced[-1][2] is False
    assert e.state.position_for(D).status == "pending_exit"
    oid = e.state.position_for(D).exit_order_id
    b.fill(oid, 0.70, 1, False, pos.short_symbol, pos.long_symbol)
    e.tick(WED(11, 3))
    done = e.state.position_for(D)
    assert done.status == "closed" and done.exit_debit == 0.70
    assert events(sink)[-1] == "exit"


def test_rejected_exit_retries_then_freezes(tmp_path):
    e, b, sink = make(tmp_path)
    e.cfg.execution.max_exit_attempts = 2
    pos = enter_and_fill(e, b)
    b.mark = pos.credit * 0.4
    for i in range(2):
        e.tick(WED(11, 2 * i))
        b.status[e.state.position_for(D).exit_order_id] = OrderStatus("rejected")
        e.tick(WED(11, 2 * i + 1))
        assert e.state.position_for(D).status == "open"
    e.tick(WED(11, 10))
    assert e.state.halt_info()["kind"] == "frozen"
    n = len(b.placed)
    e.tick(WED(11, 11))
    assert len(b.placed) == n  # frozen: no more orders


def test_time_exit_is_urgent_and_priced_at_natural(tmp_path):
    e, b, _ = make(tmp_path)
    pos = enter_and_fill(e, b)
    b.mark = pos.credit * 0.9
    e.tick(WED(15, 45))
    p = e.state.position_for(D)
    assert p.status == "pending_exit" and p.exit_kind == "time"
    assert p.exit_limit == pytest.approx(round(pos.credit * 0.9 / 0.05) * 0.05, abs=0.011)


def test_max_daily_loss_flattens(tmp_path):
    e, b, sink = make(tmp_path)
    e.cfg.execution.max_daily_loss = 300
    pos = enter_and_fill(e, b)
    b.mark = pos.credit + 3.5  # -$350
    e.tick(WED(10, 30))
    p = e.state.position_for(D)
    assert p.status == "pending_exit" and p.exit_kind == "kill" and "max daily loss" in p.exit_reason


def test_kill_file_blocks_entry_and_flattens(tmp_path):
    e, b, _ = make(tmp_path)
    (tmp_path / "KILL").write_text("")
    e.tick(WED(9, 41))
    assert not b.placed and "kill switch" in e.state.skipped(D)

    e2, b2, _ = make(tmp_path / "sub")
    e2.cfg.execution.kill_file = str(tmp_path / "KILL2")
    pos = enter_and_fill(e2, b2)
    b2.mark = pos.credit * 0.9
    (tmp_path / "KILL2").write_text("")
    e2.tick(WED(10, 0))
    assert e2.state.position_for(D).exit_kind == "kill"


def test_reconcile_detects_manual_close_and_mismatch(tmp_path):
    e, b, sink = make(tmp_path)
    pos = enter_and_fill(e, b)
    b.held = {}  # user closed it at the broker
    e.tick(WED(10, 0))
    p = e.state.position_for(D)
    assert p.status == "closed" and "outside the bot" in p.exit_reason and "external_close" in events(sink)

    e2, b2, sink2 = make(tmp_path / "m")
    e2.state = State(str(tmp_path / "m" / "s.json"))
    pos = enter_and_fill(e2, b2)
    b2.held[pos.long_symbol] = 3  # wrong quantity
    e2.tick(WED(10, 0))
    assert e2.state.halt_info()["kind"] == "frozen" and "frozen" in events(sink2)
    n = len(b2.placed)
    b2.mark = pos.credit * 0.3
    e2.tick(WED(10, 5))
    assert len(b2.placed) == n


def test_total_loss_halts_new_entries(tmp_path):
    e, b, sink = make(tmp_path)
    e.cfg.execution.max_total_loss = 500
    pos = enter_and_fill(e, b)
    b.mark = pos.credit + 6
    e.tick(WED(15, 45))  # time exit
    b.fill(e.state.position_for(D).exit_order_id, pos.credit + 6, 1, False, pos.short_symbol, pos.long_symbol)
    e.tick(WED(15, 46))
    assert e.state.halt_info()["kind"] == "entries" and "halt" in events(sink)
    # next day: no entry
    from datetime import datetime
    from zoneinfo import ZoneInfo
    e.tick(datetime(2026, 10, 1, 9, 41, tzinfo=ZoneInfo("America/New_York")))
    assert "halted" in e.state.skipped("2026-10-01")
