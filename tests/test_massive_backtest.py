"""Massive historical backtest against a fake Massive API (no network)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from spxbot.backtest import bs
from spxbot.config import Stocks
from spxbot.massive_backtest import MassiveAPI, implied_vol, occ, parse_occ, replay

NY = ZoneInfo("America/New_York")
D = "2026-09-30"


def ts(hhmm):
    return int(datetime.fromisoformat(f"{D}T{hhmm}").replace(tzinfo=NY).timestamp() * 1000)


class Resp:
    def __init__(self, body, code=200):
        self.body, self.status_code, self.text = body, code, ""

    def json(self):
        return self.body

    def raise_for_status(self):
        pass


class FakeMassive:
    """SPY: dips to 650 support, then a decisive bounce at 09:45. One 0DTE call with real-looking prices."""

    def __init__(self):
        self.urls = []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        if "/range/5/minute/" in url:
            bars = [("09:30", 650.8, 651.0, 650.4, 650.5), ("09:35", 650.5, 650.6, 650.05, 650.15),
                    ("09:40", 650.15, 650.2, 649.7, 649.95), ("09:45", 649.95, 650.6, 649.9, 650.55)]
            bars += [(f"{10 + i // 12:02d}:{i % 12 * 5:02d}", 650.6, 652.8, 650.5, 652.6) for i in range(0, 24)]
            prior = [{"t": int(datetime(2026, 9, 29, 12, tzinfo=NY).timestamp() * 1000), "o": 649, "h": 652.5,
                      "l": 647, "c": 650.2}]
            return Resp({"results": prior + [{"t": ts(t), "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in bars]})
        if "/v3/reference/options/contracts" in url:
            return Resp({"results": [{"strike_price": k} for k in range(640, 661)]} if params["expiration_date"] == D
                        else {"results": []})
        if "/range/1/minute/" in url:
            k = parse_occ(url.split("/ticker/")[1].split("/")[0])[3]
            S = 650.55
            rows = []
            for m in range(9 * 60 + 30, 16 * 60):
                hh = f"{m // 60:02d}:{m % 60:02d}"
                spot = S if m < 10 * 60 else 652.6
                px, _ = bs(spot, k, (16 * 60 - m) / (252 * 390), 0.18, "call")
                rows.append({"t": ts(hh), "c": round(max(px, 0.01), 2)})
            return Resp({"results": rows})
        raise AssertionError(url)


def test_occ_round_trip():
    t = occ("SPY", "2026-09-18", "call", 650)
    assert t == "O:SPY260918C00650000" and parse_occ(t) == ("SPY", "2026-09-18", "call", 650.0)
    assert parse_occ("O:TSLA261002P00357500") == ("TSLA", "2026-10-02", "put", 357.5)


def test_implied_vol_recovers_the_input():
    px = bs(650, 652, 100 / (252 * 390), 0.2, "call")[0]
    assert implied_vol(px, 650, 652, 100 / (252 * 390), "call") == pytest.approx(0.2, abs=1e-3)


def test_replay_on_history_takes_and_closes_a_real_priced_trade(tmp_path):
    api = MassiveAPI("k", cache=tmp_path / "cache", session=FakeMassive())
    k = Stocks(watchlist=["SPY"], market_symbols=["SPY"], min_conviction=0, use_premarket=False)
    state = replay(api, k, D, D, str(tmp_path / "s.json"))
    [t] = [x for x in state.trades() if not x.shadow]
    assert t.symbol == "SPY" and t.right == "call" and t.expiration == D and t.entry * 100 < 150
    assert t.status == "closed" and t.exit_reason in ("target", "level_target") and t.pnl() > 0
    assert t.option.startswith("O:SPY260930C")
    calls = api.calls
    replay(api, k, D, D, str(tmp_path / "s2.json"))  # second run is served from the cache
    assert api.calls == calls
