"""SPY 0/3 model: pure rules plus the live signal engine against a fake data feed."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from spxbot.config import Config, Spy03
from spxbot.models import Bar, OptionQuote, Quote
from spxbot.spy03 import (Level, Signal, build_map, check_exit, find_reaction, phase, pick_contract,
                          pick_expiration, size)
from spxbot.spy03_engine import Spy03Engine, Spy03State

NY = ZoneInfo("America/New_York")
D = "2026-09-30"  # a Wednesday


def bar(t, o, h, l, c, d=D):
    return Bar(f"{d}T{t}", o, h, l, c)


def at(hhmm, d=D):
    h, m = map(int, hhmm.split(":"))
    y, mo, da = map(int, d.split("-"))
    return datetime(y, mo, da, h, m, 10, tzinfo=NY)


# --- the clock ------------------------------------------------------------------

@pytest.mark.parametrize("t,expect", [("09:29", "pre"), ("09:30", "0dte"), ("11:29", "0dte"), ("11:30", "middle"),
                                      ("12:59", "middle"), ("13:00", "3dte"), ("15:29", "3dte"), ("15:30", "after")])
def test_phase(t, expect):
    assert phase(t, Spy03()) == expect


# --- the map --------------------------------------------------------------------

def test_map_merges_close_levels_and_keeps_only_a_few():
    m = Spy03(manual_levels=[650], round_step=5, max_zones=4)
    prior = Bar("2026-09-29", 649, 652.5, 647, 650.2)
    pre = [bar("08:00", 650, 651.4, 649.6, 650.5)]
    zones = build_map(prior, pre, 650.8, m)
    assert len(zones) == 4
    manual = [z for z in zones if z.manual]
    assert len(manual) == 1 and manual[0].price == 650 and "prior close" in manual[0].label  # 650.2 merged in
    assert "$650 zone" in manual[0].label


# --- the reaction -----------------------------------------------------------------

M = Spy03(round_step=0)
LEVELS = [Level(650.0, "support", True), Level(652.5, "prior-day high")]
LEAD = [bar("09:30", 650.8, 651.0, 650.4, 650.5), bar("09:35", 650.5, 650.6, 650.05, 650.15),
        bar("09:40", 650.15, 650.2, 649.7, 649.95)]


def test_bullish_reaction_at_support():
    r, _ = find_reaction(LEAD + [bar("09:45", 649.95, 650.6, 649.9, 650.55)], LEVELS, M)
    assert r and r.direction == "bullish" and r.right == "call"
    assert r.invalidation == 649.55 and r.target == 652.5 and r.reward_risk == pytest.approx(1.95)


def test_bearish_rejection_at_resistance():
    bars = [bar("13:00", 652.0, 652.3, 651.9, 652.2), bar("13:05", 652.4, 652.55, 651.85, 651.95)]
    r, _ = find_reaction(bars, [Level(652.5, "resistance"), Level(649.6, "prior-day low")], M)
    assert r and r.direction == "bearish" and r.invalidation == pytest.approx(652.95)


def test_a_wick_is_not_a_reaction():
    r, passed = find_reaction(LEAD + [bar("09:45", 650.05, 650.6, 649.6, 650.15)], LEVELS, M)
    assert r is None and "decisive" in passed[0]


def test_chop_is_passed_on():
    chop = [bar("09:30", 650.2, 650.3, 649.8, 649.9), bar("09:35", 649.9, 650.3, 649.8, 650.2),
            bar("09:40", 650.2, 650.3, 649.8, 649.9), bar("09:45", 649.9, 650.6, 649.9, 650.5)]
    r, passed = find_reaction(chop, LEVELS, M)
    assert r is None and "chop" in passed[0]


def test_no_chasing_a_move_that_already_ran():
    r, passed = find_reaction(LEAD + [bar("09:45", 649.95, 651.0, 649.9, 650.9)], LEVELS, M)
    assert r is None and "no chasing" in passed[0]


def test_not_enough_room_to_next_zone():
    r, passed = find_reaction(LEAD + [bar("09:45", 649.95, 650.6, 649.9, 650.55)],
                              [Level(650.0, "support"), Level(651.0, "resistance")], M)
    assert r is None and "room" in passed[0]


# --- the contract & risk ---------------------------------------------------------

def test_pick_expiration():
    exps = ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"]
    assert pick_expiration(exps, date(2026, 9, 30), 0) == "2026-09-30"
    assert pick_expiration(exps, date(2026, 10, 1), 3) == "2026-10-06"  # Thu + 3 trading days = Tue
    assert pick_expiration(exps, date(2026, 10, 3), 0) is None


def test_pick_contract_skips_illiquid():
    chain = [OptionQuote("A", 650, "call", 1.00, 1.40, 0.52), OptionQuote("B", 651, "call", 0.80, 0.84, 0.42),
             OptionQuote("C", 650, "put", 1.0, 1.02, -0.48)]
    opt, _ = pick_contract(chain, "call", 650.1, M)
    assert opt.symbol == "B"


def test_size_follows_one_percent_rule():
    assert size(1.20, Spy03(account_size=10000, risk_pct=0.01)) == (4, pytest.approx(24))
    assert size(6.00, Spy03(account_size=10000, risk_pct=0.01))[0] == 0  # $120 stop risk > $100 budget


def _sig(**kw):
    base = dict(id="x", date=D, window="0dte", direction="bullish", symbol="S", right="call", strike=650,
                expiration=D, quantity=1, entry=1.00, entry_time="", spy_entry=650.5, level=650, level_label="",
                invalidation=649.55, target=652.5, why="")
    return Signal(**{**base, **kw})


def test_exit_rules():
    m = Spy03()
    assert check_exit(_sig(), 1.21, 650.8, None, D, "10:00", m)[0] == "target"
    assert check_exit(_sig(), 0.79, 650.2, None, D, "10:00", m)[0] == "stop"
    assert check_exit(_sig(), 0.95, 649.5, 649.50, D, "10:00", m)[0] == "invalidation"
    assert check_exit(_sig(), 1.10, 652.6, None, D, "10:00", m)[0] == "level_target"
    assert check_exit(_sig(), 1.10, 650.8, None, D, "12:00", m)[0] == "time"
    assert check_exit(_sig(), 1.10, 650.8, None, D, "11:59", m) is None
    three = _sig(window="3dte", expiration="2026-10-05")
    assert check_exit(three, 1.10, 650.8, None, D, "15:55", m)[0] == "time"
    assert check_exit(three, 1.10, 650.8, None, D, "15:55", Spy03(three_dte_exit_time="")) is None


# --- live engine ------------------------------------------------------------------

class Sink:
    def __init__(self):
        self.sent = []

    def send(self, text, payload=None):
        self.sent.append((text, payload or {}))

    def events(self):
        return [p.get("event") for _, p in self.sent]


class Feed:
    def __init__(self):
        self.last = 650.55
        self.bars = [bar("08:00", 650.3, 650.9, 650.1, 650.6)] + LEAD + [bar("09:45", 649.95, 650.6, 649.9, 650.55)]
        self.daily = [Bar("2026-09-29", 649, 652.5, 647, 650.2)]
        self.exps = [D, "2026-10-01", "2026-10-02", "2026-10-05"]
        self.quotes = {}

    def get_quote(self, symbol):
        return Quote(self.last)

    def get_bars(self, symbol, day, minutes):
        return [b for b in self.bars if b.time.startswith(day)]

    def get_daily(self, symbol, start, end):
        return self.daily

    def get_expirations(self, symbol):
        return self.exps

    def get_chain(self, symbol, exp, root, right, near=None):
        rows = [OptionQuote(f"SPY{exp}C650", 650, "call", 1.18, 1.22, 0.52),
                OptionQuote(f"SPY{exp}C651", 651, "call", 0.80, 0.84, 0.42),
                OptionQuote(f"SPY{exp}P650", 650, "put", 0.60, 0.64, -0.47)]
        for o in rows:
            self.quotes.setdefault(o.symbol, o)
        return [o for o in rows if o.right == right]

    def get_option_quotes(self, symbols):
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


def engine(tmp_path, **kw):
    cfg = Config(model="spy03")
    cfg.spy03 = Spy03(manual_levels=[650], round_step=0, use_premarket=False,
                      state_file=str(tmp_path / "spy03.json"), **kw)
    feed, sink = Feed(), Sink()
    return Spy03Engine(cfg, feed, sink, Spy03State(cfg.spy03.state_file)), feed, sink


def test_morning_signal_then_target_then_afternoon_checkpoint(tmp_path):
    e, feed, sink = engine(tmp_path)
    e.tick(at("09:25"))
    assert sink.sent == []
    e.tick(at("09:50"))
    assert sink.events() == ["map", "entry"]
    entry = sink.sent[1][1]
    assert entry["window"] == "0dte" and entry["right"] == "call" and entry["strike"] == 651
    assert entry["expiration"] == D and entry["invalidation"] == 649.55 and entry["target"] == 652.5
    assert "BUY 6x SPY 2026-09-30 651C @ ~0.82" in sink.sent[1][0]

    e.tick(at("09:51"))  # nothing new
    assert len(sink.sent) == 2

    feed.quotes[entry["symbol"]] = OptionQuote(entry["symbol"], 651, "call", 0.99, 1.01, 0.55)
    e.tick(at("10:00"))
    assert sink.events()[-1] == "exit" and sink.sent[-1][1]["exit_reason"] == "target"
    assert sink.sent[-1][1]["pnl"] == pytest.approx(108)

    e.tick(at("11:30"))
    assert sink.events()[-1] == "checkpoint" and "bullish" in sink.sent[-1][0]
    e.tick(at("13:00"))
    assert sink.sent[-1][1]["blocked"] is None and "continuation only" in sink.sent[-1][0]
    e.tick(at("16:00"))
    assert sink.events()[-1] == "summary" and "+108" in sink.sent[-1][0]


def test_morning_loss_closes_the_afternoon(tmp_path):
    e, feed, sink = engine(tmp_path)
    e.tick(at("09:50"))
    sym = sink.sent[1][1]["symbol"]
    feed.quotes[sym] = OptionQuote(sym, 651, "call", 0.60, 0.62, 0.35)
    e.tick(at("10:00"))
    assert sink.sent[-1][1]["exit_reason"] == "stop" and sink.sent[-1][1]["pnl"] < 0
    e.tick(at("13:00"))
    assert "morning loss does not create an afternoon trade" in sink.sent[-1][0]

    # a fresh bullish reaction in the afternoon is passed on, not traded
    feed.bars += [bar("13:00", 650.3, 650.35, 650.0, 650.05), bar("13:05", 650.05, 650.6, 650.0, 650.5)]
    feed.last = 650.5
    e.tick(at("13:10"))
    assert sink.events()[-1] == "pass" and "afternoon trade" in sink.sent[-1][0]
    assert sum(ev == "entry" for ev in sink.events()) == 1


def test_afternoon_3dte_continuation(tmp_path):
    e, feed, sink = engine(tmp_path)
    e.tick(at("09:50"))
    sym = sink.sent[1][1]["symbol"]
    feed.quotes[sym] = OptionQuote(sym, 651, "call", 1.0, 1.02, 0.55)
    e.tick(at("10:00"))  # winner
    feed.bars += [bar("13:00", 650.3, 650.35, 650.0, 650.05), bar("13:05", 650.05, 650.6, 650.0, 650.5)]
    feed.last = 650.5
    e.tick(at("13:10"))
    entry = sink.sent[-1][1]
    assert entry["event"] == "entry" and entry["window"] == "3dte" and entry["expiration"] == "2026-10-05"
    assert "3DTE CALL" in sink.sent[-1][0]


def test_broken_thesis_blocks_afternoon(tmp_path):
    e, feed, sink = engine(tmp_path)
    e.tick(at("09:50"))
    sym = sink.sent[1][1]["symbol"]
    feed.quotes[sym] = OptionQuote(sym, 651, "call", 1.0, 1.02, 0.55)
    e.tick(at("10:00"))
    feed.bars += [bar("10:00", 650.9, 651.0, 649.2, 649.3)]  # closes through 649.55
    e.tick(at("10:05"))
    e.tick(at("13:00"))
    assert "invalidated" in sink.sent[-1][0]


def test_config_rejects_bad_windows():
    cfg = Config(model="spy03")
    cfg.spy03.zero_dte_end = "13:30"
    with pytest.raises(ValueError):
        cfg.validate()
