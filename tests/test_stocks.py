"""0DTE stock option signals: one contract under $200, chosen at the moment of the signal."""
from datetime import datetime
from zoneinfo import ZoneInfo

from spxbot.config import Config, Stocks
from spxbot.models import Bar, OptionQuote, Quote
from spxbot.stocks import StocksEngine, StocksState, Trade, check_exit, rules_for
from spxbot.spy03 import pick_contract

NY = ZoneInfo("America/New_York")
D = "2026-09-30"


def bar(t, o, h, l, c):
    return Bar(f"{D}T{t}", o, h, l, c)


def at(hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 9, 30, h, m, 10, tzinfo=NY)

BARS = [bar("09:30", 650.8, 651.0, 650.4, 650.5), bar("09:35", 650.5, 650.6, 650.05, 650.15),
        bar("09:40", 650.15, 650.2, 649.7, 649.95), bar("09:45", 649.95, 650.6, 649.9, 650.55)]


class Sink:
    def __init__(self):
        self.sent = []

    def send(self, text, payload=None):
        self.sent.append((text, payload or {}))

    def events(self):
        return [p.get("event") for _, p in self.sent]


class Feed:
    """Two tickers: TSLA (0DTE listed, at-the-money call too expensive) and XYZ (no 0DTE today)."""

    def __init__(self):
        self.quotes = {}
        self.chain = [OptionQuote("TSLA650C", 650, "call", 2.45, 2.55, 0.52),
                      OptionQuote("TSLA651C", 651, "call", 1.90, 2.00, 0.41),
                      OptionQuote("TSLA652C", 652, "call", 1.35, 1.45, 0.31)]

    def get_quote(self, s):
        return Quote(650.55)

    def get_bars(self, s, day, minutes):
        return list(BARS)

    def get_daily(self, s, start, end):
        return [Bar("2026-09-29", 649, 652.5, 647, 650.2)]

    def get_expirations(self, s):
        return [D, "2026-10-02"] if s == "TSLA" else ["2026-10-02"]

    def get_chain(self, s, exp, root, right, near=None):
        for o in self.chain:
            self.quotes.setdefault(o.symbol, o)
        return [o for o in self.chain if o.right == right]

    def get_option_quotes(self, syms):
        return {s: self.quotes[s] for s in syms if s in self.quotes}


def engine(tmp_path):
    cfg = Config(model="stocks")
    cfg.stocks = Stocks(watchlist=["XYZ", "TSLA"], use_premarket=False, state_file=str(tmp_path / "s.json"))
    feed, sink = Feed(), Sink()
    return StocksEngine(cfg, feed, sink, StocksState(cfg.stocks.state_file)), feed, sink


def test_contract_must_cost_under_200():
    rules = rules_for(Stocks(), 1.0)
    assert rules.max_premium == 1.99
    opt, _ = pick_contract(Feed().chain, "call", 650.55, rules)
    assert opt.symbol == "TSLA651C"  # 650C costs $250; 651C at $195 is the nearest strike under $200


def test_nothing_under_200_means_no_contract():
    chain = [OptionQuote("A", 650, "call", 2.45, 2.55, 0.52), OptionQuote("B", 651, "call", 2.00, 2.08, 0.45)]
    opt, _ = pick_contract(chain, "call", 650.5, rules_for(Stocks(), 1.0))
    assert opt is None


def test_signal_is_one_0dte_contract_under_200(tmp_path):
    e, feed, sink = engine(tmp_path)
    e.tick(at("09:50"))
    assert sink.events() == ["map", "entry"]
    assert "XYZ" in sink.sent[0][0] and "no 0DTE today" in sink.sent[0][0]
    t = sink.sent[1][1]
    assert t["symbol"] == "TSLA" and t["expiration"] == D and t["contracts"] == 1
    assert t["entry"] * 100 < 200 and t["right"] == "call"
    text = sink.sent[1][0]
    assert "BUY 1x TSLA 2026-09-30" in text and "Take profit" in text and "Stop" in text and "levels" in text


def test_exit_on_target_and_detailed_sell(tmp_path):
    e, feed, sink = engine(tmp_path)
    e.tick(at("09:50"))
    t = sink.sent[1][1]
    feed.quotes[t["option"]] = OptionQuote(t["option"], t["strike"], "call", t["entry"] * 1.25, t["entry"] * 1.27, 0.5)
    e.tick(at("09:55"))
    assert sink.events()[-1] == "exit" and sink.sent[-1][1]["exit_reason"] == "target"
    assert "held" in sink.sent[-1][0]


def test_check_exit_rules():
    k = Stocks()
    t = Trade(id="x", date=D, symbol="TSLA", option="o", right="put", strike=650, expiration=D, contracts=1,
              entry=1.50, entry_time="", stock_entry=650, stop=651, target=647, level=650.5, level_label="", why="")
    assert check_exit(t, 1.81, 649, None, "10:00", k)[0] == "target"
    assert check_exit(t, 1.19, 650, None, "10:00", k)[0] == "stop"
    assert check_exit(t, 1.40, 650, 651.2, "10:00", k)[0] == "invalidation"
    assert check_exit(t, 1.60, 646.9, None, "10:00", k)[0] == "level_target"
    assert check_exit(t, 1.60, 649, None, "15:50", k)[0] == "time"
    assert check_exit(t, 1.60, 649, None, "15:49", k) is None
