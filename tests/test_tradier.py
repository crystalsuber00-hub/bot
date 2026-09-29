from spxbot.config import Tradier
from spxbot.tradier import TradierClient


def client(monkeypatch, orders=None, positions=None):
    c = TradierClient(Tradier(token="x", account_id="A1"))
    calls = []
    def get(path, **kw):
        if "/orders/" in path:
            return orders
        return positions
    monkeypatch.setattr(c, "_get", get)
    monkeypatch.setattr(c, "_put", lambda p, d: calls.append(("PUT", p, d)) or {})
    monkeypatch.setattr(c, "_delete", lambda p: calls.append(("DELETE", p)) or {})
    return c, calls


def test_order_status_mapping(monkeypatch):
    c, _ = client(monkeypatch, {"order": {"status": "filled", "exec_quantity": 2, "avg_fill_price": -1.35}})
    st = c.order_status("9")
    assert (st.state, st.filled_qty, st.avg_price) == ("filled", 2, 1.35)
    c, _ = client(monkeypatch, {"order": {"status": "partially_filled", "exec_quantity": 1}})
    assert c.order_status("9").state == "working"
    c, _ = client(monkeypatch, {"order": {"status": "canceled", "exec_quantity": 1, "avg_fill_price": 1.2}})
    assert c.order_status("9").state == "cancelled" and c.order_status("9").filled_qty == 1
    c, _ = client(monkeypatch, {"order": {"status": "rejected"}})
    assert c.order_status("9").state == "rejected"


def test_cancel_replace_and_positions(monkeypatch):
    c, calls = client(monkeypatch, positions={"positions": {"position": [
        {"symbol": "SPXW260930P06000000", "quantity": -1}, {"symbol": "SPXW260930P05990000", "quantity": 1},
        {"symbol": "AAPL", "quantity": 100}]}})
    c.cancel_order("9")
    c.replace_order("9", 1.35, opening=True)
    assert calls[0] == ("DELETE", "/accounts/A1/orders/9")
    assert calls[1][2] == {"type": "credit", "duration": "day", "price": "1.35"}
    assert c.positions() == {"SPXW260930P06000000": -1, "SPXW260930P05990000": 1}
    c, _ = client(monkeypatch, positions={"positions": "null"})
    assert c.positions() == {}
