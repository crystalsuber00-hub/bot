from types import SimpleNamespace as NS

import pytest

pytest.importorskip("ib_async")

from spxbot.config import Ibkr
from spxbot.ibkr import IBKRClient, make_key, parse_key, tick_round


class FakeIB:
    def __init__(self):
        self.orders, self.cancelled, self.n = [], [], 1000

    def isConnected(self): return True
    def sleep(self, s): pass
    def qualifyContracts(self, *cs):
        for c in cs:
            self.n += 1
            c.conId = self.n
        return list(cs)
    def reqSecDefOptParams(self, *a):
        return [NS(tradingClass="SPXW", expirations={"20260930"}, strikes=[float(k) for k in range(5800, 6400, 5)])]
    def reqMktData(self, c, *a):
        if c.secType == "IND":
            return NS(marketPrice=lambda: 6100.0, open=6080.0, close=6050.0)
        d = 0.5 - abs(c.strike - 6100) / 300
        sign = -1 if c.right == "P" else 1
        return NS(contract=c, bid=1.0, ask=1.2, modelGreeks=NS(delta=sign * d))
    def cancelMktData(self, c): self.cancelled.append(c)
    def placeOrder(self, bag, order):
        self.orders.append((bag, order))
        return NS(order=NS(orderId=42))


def client(readonly=False):
    return IBKRClient(Ibkr(wait_seconds=0.1), readonly=readonly, ib=FakeIB())


def test_key_roundtrip_and_tick_round():
    k = make_key("SPXW", "2026-09-30", "put", 6000.0)
    assert k == "SPXW|20260930|P|6000" and parse_key(k) == ("SPXW", "20260930", "put", 6000.0)
    assert tick_round(-1.43) == -1.45 and tick_round(0.62) == 0.6


def test_quote_and_side_specific_chain():
    c = client()
    q = c.get_quote("SPX")
    assert (q.last, q.open, q.prev_close) == (6100.0, 6080.0, 6050.0)
    chain = c.get_chain("SPX", "2026-09-30", "SPXW", "put")
    assert chain and all(o.right == "put" and o.strike <= 6100 for o in chain)
    assert min(o.strike for o in chain) < 6100 * 0.97  # includes long-leg buffer
    assert len(c.ib.cancelled) == len(chain)  # market-data lines released


def test_place_combo_credit_is_negative_and_signal_mode_is_readonly():
    c = client()
    oid = c.open_spread("SPXW|20260930|P|6000", "SPXW|20260930|P|5990", 2, 1.43)
    bag, order = c.ib.orders[0]
    assert oid == "42" and order.action == "BUY" and order.lmtPrice == -1.45 and order.totalQuantity == 2
    assert [l.action for l in bag.comboLegs] == ["SELL", "BUY"]
    c.close_spread("SPXW|20260930|P|6000", "SPXW|20260930|P|5990", 2, 0.72)
    bag, order = c.ib.orders[1]
    assert order.lmtPrice == 0.70 and [l.action for l in bag.comboLegs] == ["BUY", "SELL"]
    with pytest.raises(RuntimeError):
        client(readonly=True).open_spread("a|20260930|P|1", "a|20260930|P|2", 1, 1.0)


def test_option_quotes_keep_subscription():
    c = client()
    k = "SPXW|20260930|P|6000"
    got = c.get_option_quotes([k])
    assert got[k].delta is not None and k in c._tickers and not c.ib.cancelled
