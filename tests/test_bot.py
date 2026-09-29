from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from spxbot.config import Config
from spxbot.engine import Engine
from spxbot.models import OptionQuote, Quote
from spxbot.notify import Notifier
from spxbot.state import State

TZ = ZoneInfo("America/New_York")
WED = lambda h, m: datetime(2026, 9, 30, h, m, tzinfo=TZ)


def chain():
    out = []
    for k in range(6000, 6200, 5):
        # puts: delta grows toward ATM(6100); calls mirrored
        dist = (6100 - k) / 100
        out.append(OptionQuote(f"SPXW{k}P", k, "put", 1.0 + max(0, (k - 6000)) / 50 - 0.1, 1.0 + max(0, (k - 6000)) / 50 + 0.1,
                               -max(0.01, 0.5 - dist * 0.5) if k <= 6100 else -0.6))
        out.append(OptionQuote(f"SPXW{k}C", k, "call", 1.0 + max(0, (6200 - k)) / 50 - 0.1, 1.0 + max(0, (6200 - k)) / 50 + 0.1,
                               max(0.01, 0.5 + dist * 0.5) if k >= 6100 else 0.6))
    return out


class Fake:
    def __init__(self, last, open_=6100.0):
        self.q, self.orders, self.mark = Quote(last, open_, open_), [], None
        self.ch = chain()

    def get_quote(self, s): return self.q
    def get_chain(self, s, e, r=None, right=None): return self.ch
    def get_option_quotes(self, syms):
        by = {o.symbol: o for o in self.ch}
        if self.mark is not None:  # force spread mid to self.mark
            a, b = by[syms[0]], by[syms[1]]
            return {a.symbol: OptionQuote(a.symbol, a.strike, a.right, self.mark, self.mark),
                    b.symbol: OptionQuote(b.symbol, b.strike, b.right, 0, 0)}
        return {s: by[s] for s in syms}
    def open_spread(self, *a): self.orders.append(("open", a)); return "1"
    def close_spread(self, *a): self.orders.append(("close", a)); return "2"


class Sink(Notifier):
    def __init__(self): self.msgs = []
    def send(self, text, payload=None): self.msgs.append((text, payload))


def make(tmp_path, last, mode="signal"):
    cfg = Config(mode=mode)
    cfg.strategy.min_credit = 0.1
    cfg.state_file = str(tmp_path / "s.json")
    f, sink = Fake(last), Sink()
    return Engine(cfg, f, sink, State(cfg.state_file)), f, sink


def test_up_day_sells_put_spread_in_delta_band(tmp_path):
    e, f, sink = make(tmp_path, 6120)
    e.tick(WED(9, 41))
    pos = e.state.position_for("2026-09-30")
    assert pos.side == "put_credit" and pos.long_strike == pos.short_strike - 10
    assert 0.15 <= abs(pos.short_delta) <= 0.20
    assert f.orders == []  # signal mode


def test_down_day_sells_call_spread(tmp_path):
    e, f, _ = make(tmp_path, 6080)
    e.tick(WED(9, 41))
    pos = e.state.position_for("2026-09-30")
    assert pos.side == "call_credit" and pos.long_strike == pos.short_strike + 10


def test_one_trade_per_day_and_no_early_entry(tmp_path):
    e, f, sink = make(tmp_path, 6120)
    e.tick(WED(9, 35))
    assert not e.state.data["positions"]
    e.tick(WED(9, 41)); e.tick(WED(9, 42)); e.tick(WED(9, 43))
    assert len(e.state.data["positions"]) == 1


def test_window_missed_is_skipped_and_weekend_ignored(tmp_path):
    e, *_ = make(tmp_path, 6120)
    e.tick(WED(10, 30))
    assert e.state.skipped("2026-09-30")
    e.tick(datetime(2026, 10, 3, 9, 41, tzinfo=TZ))  # Saturday
    assert not e.state.data["positions"]


def test_exit_at_50pct_profit(tmp_path):
    e, f, sink = make(tmp_path, 6120)
    e.tick(WED(9, 41))
    pos = e.state.position_for("2026-09-30")
    f.mark = pos.credit * 0.6  # only 40% captured
    e.tick(WED(11, 0))
    assert e.state.position_for("2026-09-30").status == "open"
    f.mark = pos.credit * 0.5  # 50% captured
    e.tick(WED(11, 30))
    closed = e.state.position_for("2026-09-30")
    assert closed.status == "closed" and "profit target" in closed.exit_reason


def test_invalid_config():
    cfg = Config()
    cfg.strategy.profit_target = 1.5
    with pytest.raises(ValueError):
        cfg.validate()


def test_ntfy_request(monkeypatch):
    from spxbot.config import Notify
    calls = []
    monkeypatch.setattr("spxbot.notify.requests.post", lambda url, **kw: calls.append((url, kw)))
    Notifier(Notify(console=False, ntfy_topic="t", ntfy_token="tok")).send("ENTRY x\nbody", {"event": "entry"})
    url, kw = calls[0]
    assert url == "https://ntfy.sh/t" and kw["data"] == b"body"
    assert kw["headers"]["Title"] == "ENTRY x" and kw["headers"]["Authorization"] == "Bearer tok"


def test_history_logged_and_chart_rendered(tmp_path):
    from spxbot.chart import make_chart
    e, f, _ = make(tmp_path, 6120)
    e.cfg.history_dir = str(tmp_path / "h")
    e.tick(WED(9, 41))
    pos = e.state.position_for("2026-09-30")
    for i, frac in enumerate((0.9, 0.8, 0.7)):
        f.mark = pos.credit * frac
        e.tick(WED(10, i))
    out = make_chart(e.cfg, e.state)
    assert out.endswith("2026-09-30.html")
    html = open(out).read()
    assert "<polyline" in html and "exit target" in html
    assert len(open(tmp_path / "h" / "2026-09-30.csv").read().splitlines()) == 4


def test_default_time_exit_and_expiry_estimate_in_signal_mode(tmp_path):
    e, f, _ = make(tmp_path, 6120)
    e.tick(WED(9, 41))
    f.mark = e.state.position_for("2026-09-30").credit * 0.9  # not at target
    e.tick(WED(15, 45))
    p = e.state.position_for("2026-09-30")
    assert p.status == "closed" and "time exit" in p.exit_reason

    e2, f2, _ = make(tmp_path, 6120)
    e2.state = State(str(tmp_path / "s2.json"))
    e2.cfg.strategy.force_close_time = ""
    e2.tick(WED(9, 41))
    e2.tick(WED(16, 1))  # expired: settle from SPX last (6120, well above put strikes)
    p2 = e2.state.position_for("2026-09-30")
    assert p2.status == "closed" and p2.exit_debit == 0 and "expired" in p2.exit_reason


def test_backtest_pricing_and_day():
    from spxbot.backtest import Params, bs, simulate_day
    from spxbot.config import Strategy
    c, cd = bs(6000, 6000, 0.001, 0.15, "call")
    p, pd = bs(6000, 6000, 0.001, 0.15, "put")
    assert abs((c - p)) < 1e-6 and abs(cd - pd - 1) < 1e-9  # put-call parity at r=q=0, delta parity
    assert bs(6000, 5900, 0, 0.15, "put")[0] == 0

    from datetime import timedelta
    start = datetime(2026, 9, 30, 9, 30, tzinfo=TZ)
    bars = [(start + timedelta(minutes=5 * i), 6100.0 + (3.0 if i >= 2 else 0), 6103.5, 6099.5, 6103.0 if i >= 2 else 6100.0) for i in range(78)]
    day = {"date": "2026-09-30", "vol": 0.12, "bars": bars}
    r = simulate_day(day, "with", Strategy(min_credit=0.1), Params())
    assert r and r["traded"] and r["side"] == "put_credit" and r["pnl"] > 0  # flat tape: spread decays, target hit
    assert simulate_day(day, "against", Strategy(min_credit=0.1), Params())["side"] == "call_credit"


def test_check_command_places_no_orders(tmp_path, capsys):
    from spxbot.check import run_check
    cfg = Config(mode="trade")
    cfg.strategy.min_credit = 0.1
    f, sink = Fake(6120), Sink()
    assert run_check(cfg, f, sink, WED(9, 41)) is True
    out = capsys.readouterr().out
    assert "put credit spread" in out and "Would sell" in out and "No orders were placed" in out
    assert f.orders == [] and sink.msgs[0][1] == {"event": "test"}


def _closed(e, date, pnl_debit, credit=1.0):
    """Insert a closed one-contract position: debit > credit means a loss."""
    from spxbot.models import Position
    e.state.upsert(Position(id=date, date=date, side="put_credit", expiration=date, short_symbol="a", long_symbol="b",
                            short_strike=1, long_strike=0, credit=credit, quantity=1, short_delta=-0.17,
                            spx_at_entry=1, entry_time="", status="closed", exit_debit=pnl_debit,
                            exit_time=date + "T15:00:00"))


def test_loss_streak_pause_skips_then_resumes(tmp_path):
    e, f, sink = make(tmp_path, 6120)
    _closed(e, "2026-09-24", 3.0)   # Thu loss
    _closed(e, "2026-09-25", 3.0)   # Fri loss -> 2 in a row; pause Mon/Tue/Wed
    e.tick(WED(9, 41))              # Wed 9/30: still paused (Mon 9/28, Tue 9/29 skipped, Wed is 3rd)
    assert "loss-streak pause" in e.state.skipped("2026-09-30") and "2026-10-01" in e.state.skipped("2026-09-30")
    from datetime import datetime
    e.tick(datetime(2026, 10, 1, 9, 41, tzinfo=TZ))  # Thu: resumes
    assert e.state.position_for("2026-10-01")


def test_pause_not_triggered_after_a_win_or_when_off(tmp_path):
    e, f, _ = make(tmp_path, 6120)
    _closed(e, "2026-09-24", 3.0)
    _closed(e, "2026-09-29", 0.2)   # win breaks the streak
    e.tick(WED(9, 41))
    assert e.state.position_for("2026-09-30")
    (tmp_path / "off").mkdir()
    e2, f2, _ = make(tmp_path / "off", 6120)
    e2.cfg.strategy.pause_after_losses = 0
    _closed(e2, "2026-09-28", 3.0); _closed(e2, "2026-09-29", 3.0)
    e2.tick(WED(9, 41))
    assert e2.state.position_for("2026-09-30")


def test_skip_dates_and_volatility_filters(tmp_path):
    e, f, _ = make(tmp_path, 6120)
    e.cfg.strategy.skip_dates = ["2026-09-30"]
    e.tick(WED(9, 41))
    assert "sit-out" in e.state.skipped("2026-09-30")

    from spxbot.config import Strategy
    from spxbot.strategy import choose_side
    assert choose_side(Quote(6120, 6100, 6100), Strategy(max_move_pct=0.3))[0] is None   # +0.33% > 0.3%
    assert choose_side(Quote(6120, 6100, 6100), Strategy(max_move_pct=0.5))[0] == "put_credit"
    gap = Quote(6120, 6100, 6000)  # opened +1.67% above prior close
    assert choose_side(gap, Strategy(max_gap_pct=1.0))[0] is None
    assert choose_side(gap, Strategy())[0] == "put_credit"
