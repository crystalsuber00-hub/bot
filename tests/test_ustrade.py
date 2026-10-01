import base64
import json

import pytest

from polyscan.ustrade import CopyTrader, USClient, find_moneyline, plan_entry, stake_for, taker_fee


def event(slug="nfl-kc-lv-2026-10-04", sides=(("Chiefs", "Kansas City Chiefs", True), ("Raiders", "Las Vegas Raiders", False))):
    return {"slug": slug, "markets": [
        {"slug": f"asc-{slug}-neg-3pt5", "sportsMarketType": "football_team_full_game_spread", "marketSides": []},
        {"slug": f"aec-{slug}", "sportsMarketType": "football_team_full_game_winner", "orderPriceMinTickSize": 0.0025,
         "minimumTradeQty": 0.01, "marketSides": [
             {"description": d, "long": lg, "team": {"name": n, "safeName": d}} for d, n, lg in sides]}]}


class FakeUS:
    def __init__(self, ev=None, ask=0.55, bid=0.53, settle=None, authed=False, fill=True):
        self.ev, self.ask, self.bid, self.settle, self.authed, self.fill = ev or event(), ask, bid, settle, authed, fill
        self.orders = []

    def event(self, slug): return self.ev if self.ev and self.ev["slug"] == slug else None
    def bbo(self, m): return {"state": "MARKET_STATE_OPEN", "longQuote": {"value": str(self.ask)}, "bestBid": {"value": str(self.bid)}}
    def settlement(self, m): return self.settle
    def buying_power(self): return 100.0

    def order(self, body):
        self.orders.append(body)
        q = body["quantity"] if self.fill else 0
        return {"id": "1", "executions": [{"order": {"cumQuantity": q, "avgPx": {"value": body["price"]["value"]}}}]}


def buy(outcome="Chiefs", px=0.55, asset="A1", slug="nfl-kc-lv-2026-10-04", market=None, wallet="w1"):
    return {"side": "BUY", "asset": asset, "outcome": outcome, "price": px, "slug": slug, "market": market or slug,
            "title": "Chiefs vs. Raiders", "wallet": wallet}


def test_fee_matches_published_schedule():
    assert 100 * taker_fee(0.50) == pytest.approx(1.74, abs=0.01)   # docs: $1.74 per 100 contracts at $0.50
    assert 100 * taker_fee(0.10) == pytest.approx(0.63, abs=0.01)


def test_find_moneyline_matches_names_and_refuses_ambiguity():
    m, s, why = find_moneyline(event(), "Chiefs")
    assert m["slug"] == "aec-nfl-kc-lv-2026-10-04" and s["long"] and not why
    _, s, _ = find_moneyline(event(), "Raiders")
    assert not s["long"]
    ev = event("cfb-okst-wvir-2026-09-26", (("Cowboys", "Oklahoma State Cowboys", True), ("Mountaineers", "West Virginia Mountaineers", False)))
    assert find_moneyline(ev, "Oklahoma State")[1]["description"] == "Cowboys"
    ev = event("cfb-boise-wmich-2026-09-26", (("Broncos", "Boise State Broncos", True), ("Broncos", "Western Michigan Broncos", False)))
    assert find_moneyline(ev, "Broncos")[1] is None                 # two teams called Broncos: don't guess
    assert find_moneyline(ev, "Boise State")[1]["long"]
    assert find_moneyline(None, "Chiefs")[2] == "game not listed on Polymarket US"


def test_plan_entry_keeps_price_plus_fee_under_ceiling():
    p = plan_entry(0.55, 0.555, 0.0025, 0.01, 3.0)
    assert p["go"] and p["limit"] + taker_fee(p["limit"]) <= 0.58 + 1e-9
    assert p["cost"] <= 3.0 and p["qty"] > 5
    assert not plan_entry(0.55, 0.57, 0.0025, 0.01, 3.0)["go"]      # 0.57 + 1.7c fee is over 0.58
    assert not plan_entry(0.92, 0.92, 0.01, 1, 3.0)["go"]           # favorite
    assert not plan_entry(0.55, None, 0.01, 1, 3.0)["go"]
    assert not plan_entry(0.80, 0.80, 0.01, 5, 3.0)["go"]           # 5-contract minimum costs more than $3


def test_paper_buy_sell_and_settle(tmp_path):
    us = FakeUS()
    t = CopyTrader(us, str(tmp_path / "p.json"), 30, stake=3)
    note = t.on_alert(buy())
    assert note.startswith("PAPER: bought") and us.orders == []      # paper never sends orders
    assert "already holding" in t.on_alert(buy())
    assert "spread, total or prop" in t.on_alert(buy(market="nfl-kc-lv-2026-10-04-spread-away-3pt5", asset="A2"))
    assert "second side" in t.on_alert(buy("Raiders", asset="A3"))
    out = t.on_alert({**buy(), "side": "SELL"})
    assert "sold after the wallet sold" in out and not t.state["positions"]
    t.on_alert(buy(asset="A4"))
    us.settle = 1.0
    assert "won" in t.refresh()[0]
    assert json.loads((tmp_path / "p.json").read_text())["closed"][-1]["pnl"] > 0


def test_guards_bankroll_daily_losses_drawdown_and_stop_file(tmp_path):
    us = FakeUS(ask=0.50)
    t = CopyTrader(us, str(tmp_path / "p.json"), 30, stake=3, max_losses_per_day=3, max_daily_loss_pct=1.0,
                    max_drawdown_pct=0.5, stop_file=str(tmp_path / "STOP"))
    for i in range(10):
        t.on_alert(buy(asset=f"B{i}", px=0.50))
    assert len(t.state["positions"]) == 10 and t.open_cost() <= 30
    assert "already in open bets" in t.on_alert(buy(asset="B10", px=0.50))
    us.settle = 0.0
    t.refresh()                                                      # 10 losses, about $30
    assert t.state["halted"] and "halted" in t.on_alert(buy(asset="C1", px=0.50))
    t.state["halted"] = ""
    assert "losses today" in t.on_alert(buy(asset="C2", px=0.50))
    t.state["closed"] = []
    (tmp_path / "STOP").write_text("")
    assert "stop file" in t.on_alert(buy(asset="C3", px=0.50))


def test_live_requires_keys(tmp_path):
    with pytest.raises(SystemExit):
        CopyTrader(FakeUS(authed=False), str(tmp_path / "l.json"), 30, live=True)


def test_live_order_shape_and_unfilled_order(tmp_path):
    us = FakeUS(authed=True)
    t = CopyTrader(us, str(tmp_path / "l.json"), 30, live=True, stake=3)
    note = t.on_alert(buy())
    o = us.orders[0]
    assert note.startswith("LIVE: bought")
    assert o["intent"] == "ORDER_INTENT_BUY_LONG" and o["type"] == "ORDER_TYPE_LIMIT"
    assert o["tif"] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and o["marketSlug"] == "aec-nfl-kc-lv-2026-10-04"
    assert float(o["price"]["value"]) + taker_fee(float(o["price"]["value"])) <= 0.58 + 1e-9
    t.on_alert({**buy(), "side": "SELL"})
    assert us.orders[1]["intent"] == "ORDER_INTENT_SELL_LONG" and float(us.orders[1]["price"]["value"]) == pytest.approx(0.51)
    us.fill = False
    assert "did not fill" in t.on_alert(buy(asset="Z"))
    assert "Z" not in t.state["positions"]


def test_signature_verifies_with_public_key(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
    sk = ed25519.Ed25519PrivateKey.generate()
    raw = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    c = USClient("kid", base64.b64encode(raw).decode())
    sent = {}

    class R:
        ok, content, status_code, text = True, b"{}", 200, "{}"
        def json(self): return {}

    def fake(method, url, headers=None, data=None, timeout=None):
        sent.update(method=method, url=url, headers=headers)
        return R()
    monkeypatch.setattr(c.s, "request", fake)
    c.private("GET", "/v1/account/balances")
    h = sent["headers"]
    msg = f"{h['X-PM-Timestamp']}GET/v1/account/balances".encode()
    sk.public_key().verify(base64.b64decode(h["X-PM-Signature"]), msg)   # raises if wrong
    assert h["X-PM-Access-Key"] == "kid" and sent["url"] == "https://api.polymarket.us/v1/account/balances"


def test_defaults_suit_a_100_dollar_account(tmp_path):
    t = CopyTrader(FakeUS(), str(tmp_path / "p.json"), 100)
    assert t.stake == 2.0 and t.max_day_loss == 10.0 and t.max_losses == 5 and t.max_dd == 0.30
    assert stake_for(100, stake=5) == 5 and stake_for(100, stake_pct=0.01) == 1
    with pytest.raises(SystemExit):
        stake_for(100, stake=150)


def test_daily_dollar_loss_stop(tmp_path):
    us = FakeUS(ask=0.50)
    t = CopyTrader(us, str(tmp_path / "p.json"), 100, stake=6)   # $6 bets: two losses pass the $10 daily stop
    t.on_alert(buy(asset="D1", px=0.50)); t.on_alert(buy(asset="D2", px=0.50))
    us.settle = 0.0
    t.refresh()
    assert t.realized() < -10
    assert "down $" in t.on_alert(buy(asset="D3", px=0.50))
    assert not t.state["halted"]                                  # $12 lost is under the $30 halt
