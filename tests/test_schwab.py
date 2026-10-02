"""Schwab market-data client against canned HTTP responses (no network)."""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from spxbot.config import Config, Schwab
from spxbot.schwab import SchwabClient, SchwabLoginNeeded, code_from_redirect

NY = ZoneInfo("America/New_York")


def ms(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=NY).timestamp() * 1000)


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Http:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params, headers))
        return Resp(self.routes[url.split("/marketdata/v1")[1]])

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data, headers))
        return Resp({"access_token": "A2", "refresh_token": data.get("refresh_token", "R1"), "expires_in": 1800})


def client(tmp_path, routes, tok=None):
    path = tmp_path / "tok.json"
    if tok is not None:
        path.write_text(json.dumps(tok))
    http = Http(routes)
    return SchwabClient(Schwab(app_key="k", app_secret="s", token_file=str(path)), session=http), http


FRESH = {"access_token": "A1", "access_expires": time.time() + 900, "refresh_token": "R1",
         "refresh_issued": time.time()}


def test_code_from_redirect_decodes():
    assert code_from_redirect("https://127.0.0.1/?code=C0.abc%40&session=x") == "C0.abc@"
    with pytest.raises(ValueError):
        code_from_redirect("https://127.0.0.1/")


def test_login_saves_token(tmp_path):
    c, http = client(tmp_path, {})
    c.login("https://127.0.0.1/?code=abc%40&session=x")
    saved = json.loads((tmp_path / "tok.json").read_text())
    assert saved["access_token"] == "A2" and saved["refresh_token"] == "R1"
    assert http.calls[0][2]["code"] == "abc@" and http.calls[0][2]["grant_type"] == "authorization_code"
    assert 6.9 < c.refresh_days_left() <= 7


def test_expired_access_token_is_refreshed(tmp_path):
    c, http = client(tmp_path, {"/quotes": {"SPY": {"quote": {"lastPrice": 650.5, "openPrice": 649, "closePrice": 648}}}},
                     {**FRESH, "access_expires": 0})
    q = c.get_quote("SPY")
    assert q.last == 650.5 and q.open == 649 and q.prev_close == 648
    assert http.calls[0][2]["grant_type"] == "refresh_token"
    assert http.calls[1][3]["Authorization"] == "Bearer A2"


def test_week_old_login_asks_to_log_in_again(tmp_path):
    c, _ = client(tmp_path, {}, {**FRESH, "access_expires": 0, "refresh_issued": time.time() - 8 * 86400})
    with pytest.raises(SchwabLoginNeeded, match="schwab-login"):
        c.get_quote("SPY")


def test_bars_daily_expirations(tmp_path):
    c, http = client(tmp_path, {
        "/pricehistory": {"candles": [
            {"datetime": ms("2026-09-30T08:00"), "open": 1, "high": 2, "low": 0.5, "close": 1.5},
            {"datetime": ms("2026-09-30T09:30"), "open": 650, "high": 651, "low": 649.5, "close": 650.7},
            {"datetime": ms("2026-09-30T17:00"), "open": 1, "high": 1, "low": 1, "close": 1}]},
        "/expirationchain": {"expirationList": [{"expirationDate": "2026-10-05"}, {"expirationDate": "2026-09-30"}]},
    }, FRESH)
    bars = c.get_bars("SPY", "2026-09-30", 5)
    assert [b.time for b in bars] == ["2026-09-30T08:00", "2026-09-30T09:30"]
    assert http.calls[0][2]["needExtendedHoursData"] == "true" and http.calls[0][2]["frequency"] == 5
    assert c.get_expirations("SPY") == ["2026-09-30", "2026-10-05"]


def test_chain_and_option_quotes(tmp_path):
    sym = "SPY   260930C00651000"
    c, _ = client(tmp_path, {
        "/chains": {"underlyingPrice": 650.4, "callExpDateMap": {"2026-09-30:0": {
            "651.0": [{"symbol": sym, "strikePrice": 651.0, "putCall": "CALL", "bid": 0.8, "ask": 0.84, "delta": 0.42}],
            "660.0": [{"symbol": "far", "strikePrice": 660.0, "putCall": "CALL", "bid": 0.01, "ask": 0.02,
                       "delta": -999.0}]}}},
        "/quotes": {sym: {"quote": {"bidPrice": 1.0, "askPrice": 1.04, "delta": 0.5},
                          "reference": {"strikePrice": 651.0, "contractType": "C"}}},
    }, FRESH)
    chain = c.get_chain("SPY", "2026-09-30", "SPY", "call", near=5)
    assert len(chain) == 1 and chain[0].symbol == sym and chain[0].delta == 0.42 and chain[0].right == "call"
    q = c.get_option_quotes([sym])[sym]
    assert q.mid == pytest.approx(1.02) and q.strike == 651


def test_schwab_is_for_spy03_only():
    cfg = Config(broker="schwab")
    with pytest.raises(ValueError):
        cfg.validate()
    Config(broker="schwab", model="spy03").validate()


def test_dotenv_is_loaded_without_overriding_the_shell(tmp_path, monkeypatch):
    import os
    from spxbot.__main__ import load_dotenv
    (tmp_path / ".env").write_text("# c\nSCHWAB_APP_KEY=AbC123 \nSCHWAB_APP_SECRET = 'xyz'\nNTFY_TOPIC=\nKEEP=fromfile\n")
    monkeypatch.delenv("SCHWAB_APP_KEY", raising=False)
    monkeypatch.delenv("SCHWAB_APP_SECRET", raising=False)
    monkeypatch.setenv("KEEP", "fromshell")
    load_dotenv(str(tmp_path / ".env"))
    assert os.environ["SCHWAB_APP_KEY"] == "AbC123" and os.environ["SCHWAB_APP_SECRET"] == "xyz"
    assert os.environ["KEEP"] == "fromshell"
