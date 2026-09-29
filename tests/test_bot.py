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
    def get_chain(self, s, e, r=None): return self.ch
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
    e, f, sink = make(tmp_path, 6120, mode="trade")
    e.tick(WED(9, 35))
    assert not e.state.data["positions"]
    e.tick(WED(9, 41)); e.tick(WED(9, 42)); e.tick(WED(9, 43))
    assert len(e.state.data["positions"]) == 1
    assert [o[0] for o in f.orders] == ["open"]


def test_window_missed_is_skipped_and_weekend_ignored(tmp_path):
    e, *_ = make(tmp_path, 6120)
    e.tick(WED(10, 30))
    assert e.state.skipped("2026-09-30")
    e.tick(datetime(2026, 10, 3, 9, 41, tzinfo=TZ))  # Saturday
    assert not e.state.data["positions"]


def test_exit_at_50pct_profit(tmp_path):
    e, f, sink = make(tmp_path, 6120, mode="trade")
    e.tick(WED(9, 41))
    pos = e.state.position_for("2026-09-30")
    f.mark = pos.credit * 0.6  # only 40% captured
    e.tick(WED(11, 0))
    assert e.state.position_for("2026-09-30").status == "open"
    f.mark = pos.credit * 0.5  # 50% captured
    e.tick(WED(11, 30))
    closed = e.state.position_for("2026-09-30")
    assert closed.status == "closed" and "profit target" in closed.exit_reason
    assert [o[0] for o in f.orders] == ["open", "close"]


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
