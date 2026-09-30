"""End to end: StocksEngine driving the real SchwabClient parsing against canned Schwab API responses."""
import json
from datetime import datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from spxbot.config import Config, Schwab, Stocks
from spxbot.schwab import SchwabClient
from spxbot.stocks import StocksEngine, StocksState

NY = ZoneInfo("America/New_York")
D = "2026-09-30"
BARS = [("09:30", 650.8, 651.0, 650.4, 650.5), ("09:35", 650.5, 650.6, 650.05, 650.15),
        ("09:40", 650.15, 650.2, 649.7, 649.95), ("09:45", 649.95, 650.6, 649.9, 650.55),
        ("09:50", 650.55, 650.7, 650.5, 650.6)]  # 09:50 is still forming at 09:51
OCC = "TSLA  260930C00652000"


def ms(hhmm, day=D):
    return int(datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=NY).timestamp() * 1000)


class Resp:
    def __init__(self, body):
        self.body, self.status_code, self.text = body, 200, ""

    def json(self):
        return self.body

    def raise_for_status(self):
        pass


class SchwabAPI:
    def __init__(self):
        self.option_mid = 1.40
        self.quote_queries = []

    def post(self, url, data=None, headers=None, timeout=None):
        return Resp({"access_token": "A", "refresh_token": "R", "expires_in": 1800})

    def get(self, url, params=None, headers=None, timeout=None):
        path = url.split("/marketdata/v1")[1]
        if isinstance(params, str):
            self.quote_queries.append(params)
            params = {k: v[0] for k, v in parse_qs(params).items()}
        if path == "/pricehistory" and params["frequencyType"] == "minute":
            return Resp({"candles": [{"datetime": ms(t), "open": o, "high": h, "low": l, "close": c}
                                     for t, o, h, l, c in BARS]})
        if path == "/pricehistory":
            return Resp({"candles": [{"datetime": ms("01:00", "2026-09-29"), "open": 649, "high": 652.5,
                                      "low": 647, "close": 650.2}]})
        if path == "/expirationchain":
            return Resp({"expirationList": [{"expirationDate": D}, {"expirationDate": "2026-10-02"}]})
        if path == "/chains":
            return Resp({"underlyingPrice": 650.55, "callExpDateMap": {f"{D}:0": {
                "650.0": [{"symbol": "TSLA  260930C00650000", "strikePrice": 650.0, "putCall": "CALL",
                           "bid": 2.45, "ask": 2.55, "delta": 0.52}],
                "652.0": [{"symbol": OCC, "strikePrice": 652.0, "putCall": "CALL", "bid": 1.38, "ask": 1.42,
                           "delta": 0.31}]}}})
        if path == "/quotes":
            out = {}
            for s in params["symbols"].split(","):
                if s == OCC:
                    m = self.option_mid
                    out[s] = {"quote": {"bidPrice": m - 0.01, "askPrice": m + 0.01, "delta": 0.4},
                              "reference": {"strikePrice": 652.0, "contractType": "C"}}
                else:
                    out[s] = {"quote": {"lastPrice": 650.55}}
            return Resp(out)
        raise AssertionError(path)


class Sink:
    def __init__(self):
        self.sent = []

    def send(self, text, payload=None):
        self.sent.append((text, payload or {}))


def test_full_day_through_schwab_client(tmp_path):
    (tmp_path / "tok.json").write_text(json.dumps({"access_token": "A", "access_expires": 4e9,
                                                   "refresh_token": "R", "refresh_issued": 4e9}))
    cfg = Config(model="stocks", broker="schwab")
    cfg.stocks = Stocks(watchlist=["TSLA"], use_premarket=False, min_conviction=0,
                        state_file=str(tmp_path / "s.json"))
    cfg.schwab = Schwab(app_key="k", app_secret="s", token_file=str(tmp_path / "tok.json"))
    api, sink = SchwabAPI(), Sink()
    eng = StocksEngine(cfg, SchwabClient(cfg.schwab, session=api), sink, StocksState(cfg.stocks.state_file))

    eng.tick(datetime(2026, 9, 30, 9, 51, 5, tzinfo=NY))
    events = [p.get("event") for _, p in sink.sent]
    assert events == ["map", "entry", "exit_plan"]
    entry = sink.sent[1][1]
    assert entry["option"] == OCC and entry["strike"] == 652 and entry["entry"] == 1.40 and entry["expiration"] == D

    api.option_mid = 2.12  # +51%
    eng.tick(datetime(2026, 9, 30, 9, 52, 5, tzinfo=NY))
    assert sink.sent[-1][1]["event"] == "exit" and sink.sent[-1][1]["exit_reason"] == "target"
    # the option symbol's spaces must go out as %20, never '+'
    assert any("TSLA%20%20260930C00652000" in q for q in api.quote_queries)
    assert not any("+" in q for q in api.quote_queries)
