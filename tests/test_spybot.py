from datetime import datetime

from spyopts import bot

ET = bot.ET


class FakeAlpaca:
    def __init__(self, equity=100_000.0, spot=600.0):
        self.equity, self.spot = equity, spot
        self.orders, self.held, self.is_open = [], [], True
        self.status = {}

    def clock(self):
        return {"is_open": self.is_open}

    def account(self):
        return {"equity": str(self.equity)}

    def positions(self):
        return self.held

    def stock_price(self, _):
        return self.spot

    def contracts(self, und, gte, lte, lo, hi):
        out = []
        for exp in ("2026-10-30", "2026-11-20"):
            for k in range(lo, hi + 1):
                for t in ("call", "put"):
                    out.append({"expiration_date": exp, "type": t, "strike_price": str(k),
                                "symbol": f"SPY{exp[2:4]}{exp[5:7]}{exp[8:]}{t[0].upper()}{k * 1000:08d}"})
        return out

    def quotes(self, syms):
        # shorts (3% OTM) worth 2.00, longs (6% OTM) worth 0.50, spread 0.10
        out = {}
        for s in syms:
            k = int(s[-8:]) / 1000
            v = 2.0 if abs(k / self.spot - 1) < 0.045 else 0.5
            out[s] = (v - 0.05, v + 0.05)
        return out

    def submit_mleg(self, legs, qty, limit):
        oid = f"o{len(self.orders)}"
        self.orders.append({"id": oid, "legs": legs, "qty": qty, "limit": limit})
        return {"id": oid}

    def order(self, oid):
        o = next(x for x in self.orders if x["id"] == oid)
        st = self.status.get(oid, "new")
        legs = [{"side": l["side"], "filled_avg_price": (2.0 if "sell" in l["position_intent"] and "open" in l["position_intent"]
                                                         else 0.5 if "open" in l["position_intent"]
                                                         else 0.3 if l["side"] == "buy" and True else 0.1)}
                for l in o["legs"]]
        return {"status": st, "filled_qty": str(o["qty"] if st == "filled" else 0), "legs": legs}

    def cancel(self, oid):
        self.status[oid] = "canceled"


def at(h, m, d=1):
    return datetime(2026, 10, d, h, m, tzinfo=ET)


def fresh():
    return {"position": None, "pending": None, "tries": {}, "trades": [], "halted": None, "notes": {}}


def test_entry_places_credit_condor_sized_to_10pct():
    api, st = FakeAlpaca(), fresh()
    msg = bot.tick(api, st, at(10, 30))
    o = api.orders[0]
    assert "entry order" in msg and o["limit"] == -3.0  # credit 2*2.00 - 2*0.50 = 3.00, sent negative
    assert sorted(l["position_intent"] for l in o["legs"]) == ["buy_to_open"] * 2 + ["sell_to_open"] * 2
    assert {l["symbol"][9] for l in o["legs"]} == {"C", "P"}
    # width 18 (618-600... short 618, long 636) -> risk (18-3)*100 = 1500; 10% of 100k = 10000 -> 6 contracts
    assert o["qty"] == 6
    assert st["pending"]["expiry"] == "2026-10-30"


def test_small_account_skips_with_one_alert():
    api, st = FakeAlpaca(equity=2000), fresh()
    assert "too small" in bot.tick(api, st, at(10, 30))
    assert "too small" in bot.tick(api, st, at(10, 45))
    assert api.orders == []


def test_fill_records_position_then_closes_day_before_expiry():
    api, st = FakeAlpaca(), fresh()
    bot.tick(api, st, at(10, 30))
    api.status["o0"] = "filled"
    bot.tick(api, st, at(10, 45))
    pos = st["position"]
    assert pos and pos["credit"] == 3.0 and pos["qty"] == 6
    api.held = [{"symbol": s, "qty": str(-6 if r.startswith("short") else 6), "asset_class": "us_option"}
                for r, s in pos["legs"].items()]
    assert "holding" in bot.tick(api, st, at(11, 0, d=2))
    # 2026-10-29 is the trading day before the 10-30 expiry
    assert "holding" in bot.tick(api, st, at(13, 0, d=29))
    msg = bot.tick(api, st, at(14, 5, d=29))
    assert "close order" in msg and api.orders[-1]["limit"] > 0
    assert sorted(l["position_intent"] for l in api.orders[-1]["legs"]) == ["buy_to_close"] * 2 + ["sell_to_close"] * 2
    api.status["o1"] = "filled"
    bot.tick(api, st, at(14, 20, d=29))
    t = st["trades"][-1]
    assert st["position"] is None and t["debit"] == 0.4 and t["pnl"] == round((3.0 - 0.4) * 600, 2)


def test_unfilled_entry_is_cancelled_and_requoted_lower():
    api, st = FakeAlpaca(), fresh()
    bot.tick(api, st, at(10, 30))
    assert "working" in bot.tick(api, st, at(10, 35))
    assert "cancel" in bot.tick(api, st, at(10, 41))
    bot.tick(api, st, at(10, 45))          # sees cancel
    bot.tick(api, st, at(10, 50))          # re-quotes
    assert api.orders[-1]["limit"] == -2.95


def test_unexpected_broker_position_halts():
    api, st = FakeAlpaca(), fresh()
    api.held = [{"symbol": "SPY261030C00618000", "qty": "-1", "asset_class": "us_option"}]
    assert bot.tick(api, st, at(10, 30)) == "halted"
    assert "HALTED" in bot.tick(api, st, at(10, 45)) and api.orders == []


def test_weekdays_between_skips_weekend():
    from datetime import date
    assert bot._weekdays_between(date(2026, 10, 2), date(2026, 10, 5)) == 1  # Fri -> Mon
