import base64
import hashlib
import hmac
import io
import json
import time
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults

import pytest
import requests

from shopbot import scheduler
from shopbot.config import Config, Pricing
from shopbot.copywriter import Copywriter, clean_title
from shopbot.mailer import Mailer
from shopbot.payments import flatten, verify_signature
from shopbot.pricing import profit, retail
from shopbot.shop import Shop
from shopbot.suppliers import CJSupplier, DemoSupplier, SupplierError, SupplierOrder
from shopbot.web import App, cart_cookie

WHSEC = "whsec_test"


class FakeStripe:
    def __init__(self):
        self.sessions, self.refunds = [], []

    def create_checkout(self, cfg, order, lines):
        self.sessions.append((order, lines))
        return {"id": f"cs_{order['public_id']}", "url": f"https://checkout.stripe.test/{order['public_id']}"}

    def refund(self, pi, reason="requested_by_customer"):
        self.refunds.append(pi)


class Supplier(DemoSupplier):
    """Demo catalogue with scriptable order behaviour."""

    def __init__(self):
        self.placed, self.paid, self.fail_with, self.status = [], [], None, {}

    def place_order(self, order_number, ship, items, logistic_name, from_country="CN"):
        if self.fail_with:
            raise self.fail_with
        self.placed.append((order_number, ship, items))
        return SupplierOrder(order_id=f"SO-{order_number}", status="placed", raw_status="CREATED", cost=10.0)

    def pay(self, order_id):
        self.paid.append(order_id)

    def order(self, order_id):
        return self.status.get(order_id, SupplierOrder(order_id, "processing", "PROCESSING"))


class Mail(Mailer):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.out = []

    def send(self, to, subject, text, html=None, unsubscribe_url=""):
        self.out.append((to, subject, text))
        return True


def make_shop(tmp_path, **over):
    cfg = Config(db_path=str(tmp_path / "shop.db"))
    cfg.store.base_url = "https://shop.test"
    cfg.payments.stripe_secret_key = "sk_test_x"
    cfg.payments.stripe_webhook_secret = WHSEC
    cfg.admin.password = "pw"
    cfg.research.keywords = ["phone stand", "dog brush"]
    cfg.copywriting.enabled = False
    for k, v in over.items():
        section, key = k.split("__")
        setattr(getattr(cfg, section), key, v)
    sup, stripe = Supplier(), FakeStripe()
    shop = Shop(cfg, supplier=sup, stripe=stripe, mailer=Mail(cfg), copywriter=Copywriter(enabled=False))
    return shop, sup, stripe


def call(app, method, path, data=None, cookies=None, headers=None, raw=None):
    env = {}
    setup_testing_defaults(env)
    path, _, qs = path.partition("?")
    body = raw if raw is not None else urlencode(data or {}).encode()
    env.update({"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": qs, "wsgi.input": io.BytesIO(body),
                "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/x-www-form-urlencoded"})
    if cookies:
        env["HTTP_COOKIE"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    env.update(headers or {})
    out = {}

    def start(status, hdrs):
        out["status"], out["headers"] = status, hdrs
    out["body"] = b"".join(app(env, start)).decode()
    out["code"] = int(out["status"][:3])
    return out


def signed(event: dict, secret=WHSEC, ts=None):
    body = json.dumps(event).encode()
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"HTTP_STRIPE_SIGNATURE": f"t={ts},v1={sig}"}


def paid_event(public_id, consent=True, amount=None):
    return {"type": "checkout.session.completed", "data": {"object": {
        "id": f"cs_{public_id}", "payment_status": "paid", "metadata": {"order_id": public_id},
        "amount_total": amount, "payment_intent": f"pi_{public_id}",
        "customer_details": {"email": "Jane@Example.com", "name": "Jane Doe", "phone": "+15555550100"},
        "collected_information": {"shipping_details": {"name": "Jane Doe", "address": {
            "line1": "1 Main St", "line2": "", "city": "Austin", "state": "TX", "postal_code": "78701", "country": "US"}}},
        "consent": {"promotions": "opt_in" if consent else "opt_out"}}}}


def buy(shop, app, qty=1, consent=True):
    """Shopper adds the first product to the cart, checks out, and Stripe confirms payment."""
    v = shop.db.one("SELECT * FROM variants WHERE active = 1 ORDER BY id")
    r = call(app, "POST", "/checkout", cookies={"cart": cart_cookie({str(v["id"]): qty})})
    assert r["code"] == 303
    order = shop.db.one("SELECT * FROM orders ORDER BY id DESC")
    body, hdr = signed(paid_event(order["public_id"], consent, int(round(order["subtotal"] * 100))))
    assert call(app, "POST", "/webhooks/stripe", raw=body, headers=hdr)["code"] == 200
    return shop.order(order["public_id"])


# --- pricing -----------------------------------------------------------------

def test_tiered_markup_and_profit_floor():
    p = Pricing()
    price, compare = retail(4.0, 2.0, p)          # landed $6 -> 3x = 18 -> 17.99
    assert price == 17.99 and compare == 0.0
    assert retail(25.0, 3.0, p)[0] == 58.99       # landed $28 -> 2.1x
    assert profit(price, 4.0, 2.0, p) >= p.min_profit
    cheap = retail(0.5, 0.5, p)[0]
    assert cheap == p.min_price                   # store minimum price
    p.min_profit, p.min_price = 30, 0
    assert profit(retail(10, 2, p)[0], 10, 2, p) >= 30


# --- Stripe ------------------------------------------------------------------

def test_stripe_signature():
    body, hdr = signed({"a": 1})
    assert verify_signature(body, hdr["HTTP_STRIPE_SIGNATURE"], WHSEC)
    assert not verify_signature(body, hdr["HTTP_STRIPE_SIGNATURE"], "whsec_other")
    assert not verify_signature(body + b" ", hdr["HTTP_STRIPE_SIGNATURE"], WHSEC)
    old_body, old = signed({"a": 1}, ts=int(time.time()) - 3600)
    assert not verify_signature(old_body, old["HTTP_STRIPE_SIGNATURE"], WHSEC)
    assert not verify_signature(body, "", WHSEC)


def test_flatten_matches_stripe_form_encoding():
    assert flatten({"line_items": [{"price_data": {"unit_amount": 1999}, "quantity": 2}], "x": {"enabled": True}}) == [
        ("line_items[0][price_data][unit_amount]", "1999"), ("line_items[0][quantity]", "2"), ("x[enabled]", "true")]


# --- research ----------------------------------------------------------------

def test_research_lists_scored_products_and_skips_known(tmp_path):
    shop, sup, _ = make_shop(tmp_path)
    added = shop.research()
    assert len(added) == shop.cfg.research.new_per_run
    prods = shop.db.q("SELECT * FROM products")
    assert all(p["active"] and p["slug"] and p["ship_cost"] > 0 for p in prods)
    scores = [p["score"] for p in prods]
    assert scores == sorted(scores, reverse=True)
    for v in shop.db.q("SELECT v.*, p.ship_cost FROM variants v JOIN products p ON p.id = v.product_id"):
        assert v["stock"] > 0 and profit(v["price"], v["cost"], v["ship_cost"], shop.cfg.pricing) >= shop.cfg.pricing.min_profit
    again = shop.research()
    pids = [r["supplier_pid"] for r in shop.db.q("SELECT supplier_pid FROM products")]
    assert len(pids) == len(set(pids)) == len(added) + len(again)


def test_research_respects_catalogue_cap_and_blocked_words(tmp_path):
    shop, _, _ = make_shop(tmp_path, research__max_products=3, research__blocked_words=["foldable"])
    shop.research()
    shop.research()
    titles = [p["title"].lower() for p in shop.db.q("SELECT title FROM products")]
    assert len(titles) == 3 and not any("foldable" in t for t in titles)


def test_sync_hides_sold_out_and_reprices(tmp_path, monkeypatch):
    shop, sup, _ = make_shop(tmp_path)
    shop.research()
    prod = shop.db.one("SELECT * FROM products ORDER BY id")
    real = sup.product

    def changed(pid):
        p = real(pid)
        if pid == prod["supplier_pid"]:
            for v in p.variants:
                v.stock = 0
        else:
            for v in p.variants:
                v.cost += 1
        return p
    monkeypatch.setattr(sup, "product", changed)
    stats = shop.sync()
    assert stats["hidden"] == 1 and stats["repriced"] > 0
    assert shop.db.one("SELECT active, inactive_reason FROM products WHERE id = ?", (prod["id"],)) == {"active": 0, "inactive_reason": "stock"}
    monkeypatch.setattr(sup, "product", real)
    assert shop.sync()["restored"] == 1


# --- storefront ----------------------------------------------------------------

def test_pages_render(tmp_path):
    shop, _, _ = make_shop(tmp_path)
    shop.research()
    app = App(shop)
    p = shop.db.one("SELECT * FROM products")
    for path in ["/", f"/p/{p['slug']}", f"/c/{p['keyword'].replace(' ', '-')}", "/cart", "/track", "/pages/returns",
                 "/sitemap.xml", "/feeds/google.xml", "/robots.txt", "/healthz"]:
        assert call(app, "GET", path)["code"] == 200, path
    assert call(app, "GET", "/p/nope")["code"] == 404
    assert call(app, "GET", "/checkout")["code"] == 405
    page = call(app, "GET", f"/p/{p['slug']}")["body"]
    assert '"@type": "Product"' in page and p["title"] in page
    feed = call(app, "GET", "/feeds/google.xml")["body"]
    assert feed.count("<item>") == shop.db.one("SELECT COUNT(*) AS n FROM variants WHERE active = 1")["n"]


def test_untrusted_text_is_escaped(tmp_path):
    shop, _, _ = make_shop(tmp_path)
    shop.research()
    shop.db.x("UPDATE products SET title = '<script>alert(1)</script>', description = '</p><img src=x onerror=alert(1)>'")
    p = shop.db.one("SELECT slug FROM products")
    body = call(App(shop), "GET", f"/p/{p['slug']}")["body"]
    assert "<script>alert" not in body and "<img src=x" not in body and "&lt;script&gt;" in body


def test_cart_prices_come_from_database_and_bad_cookies_are_ignored(tmp_path):
    shop, _, stripe = make_shop(tmp_path)
    shop.research()
    app = App(shop)
    v = shop.db.one("SELECT * FROM variants WHERE active = 1")
    r = call(app, "POST", "/cart/add", {"variant": v["id"], "qty": 3})
    cookie = next(h[1] for h in r["headers"] if h[0] == "Set-Cookie").split(";")[0].split("=", 1)[1]
    call(app, "POST", "/checkout", cookies={"cart": cookie})
    order, lines = stripe.sessions[-1]
    assert lines[0]["price"] == v["price"] and lines[0]["qty"] == 3 and order["subtotal"] == round(v["price"] * 3, 2)
    for bad in ["garbage", base64.urlsafe_b64encode(b'{"1": 999, "x": 1}').decode(), cart_cookie({"99999": 1})]:
        assert call(app, "GET", "/cart", cookies={"cart": bad})["code"] == 200
    assert shop.cart_lines({str(v["id"]): 500})[0]["qty"] == 10


# --- order lifecycle -----------------------------------------------------------------

def test_full_order_lifecycle(tmp_path):
    shop, sup, stripe = make_shop(tmp_path)
    shop.research()
    app = App(shop)
    order = buy(shop, app, qty=2)
    assert order["status"] == "paid" and order["email"] == "Jane@Example.com" and order["marketing_consent"] == 1
    assert json.loads(order["address"])["city"] == "Austin"
    assert [m[1] for m in shop.mailer.out] == [f"Order {order['public_id']} confirmed"]

    # Stripe retries webhooks: nothing happens twice
    body, hdr = signed(paid_event(order["public_id"]))
    call(app, "POST", "/webhooks/stripe", raw=body, headers=hdr)
    assert len(shop.mailer.out) == 1

    assert shop.fulfill() == 1
    o = shop.order(order["public_id"])
    assert o["status"] == "ordered" and o["supplier_order_id"] == f"SO-{o['public_id']}" and sup.paid == [o["supplier_order_id"]]
    number, ship_to, items = sup.placed[0]
    assert number == o["public_id"] and ship_to["postal_code"] == "78701" and ship_to["phone"] and items[0][1] == 2
    assert shop.fulfill() == 0   # not re-ordered

    sup.status[o["supplier_order_id"]] = SupplierOrder(o["supplier_order_id"], "shipped", "SHIPPED", "TRACK123", "", "USPS")
    shop.track()
    o = shop.order(o["public_id"])
    assert o["status"] == "shipped" and o["tracking_number"] == "TRACK123"
    assert "TRACK123" in shop.mailer.out[-1][2]
    shop.track()
    assert len(shop.mailer.out) == 2   # tracking email sent once

    sup.status[o["supplier_order_id"]] = SupplierOrder(o["supplier_order_id"], "delivered", "DELIVERED", "TRACK123")
    shop.track()
    assert shop.order(o["public_id"])["status"] == "delivered"
    shop.db.x("UPDATE orders SET delivered_at = ?", (time.time() - 6 * 86400,))
    assert shop.followups() == 1 and shop.followups() == 0
    assert shop.mailer.out[-1][1] == "How's your order?" and "Unsubscribe" in shop.mailer.out[-1][2]

    track = call(app, "GET", f"/track?order={o['public_id'].lower()}&email=jane%40example.com")["body"]
    assert "TRACK123" in track
    assert "TRACK123" not in call(app, "GET", f"/track?order={o['public_id']}&email=someone%40else.com")["body"]
    stats = shop.stats()
    assert stats["orders"] == 1 and 0 < stats["profit"] < stats["revenue"]


def test_no_followup_without_marketing_consent(tmp_path):
    shop, sup, _ = make_shop(tmp_path)
    shop.research()
    buy(shop, App(shop), consent=False)
    shop.db.x("UPDATE orders SET status = 'delivered', delivered_at = 0")
    assert shop.followups() == 0
    assert not shop.db.one("SELECT * FROM subscribers")


def test_webhook_rejects_bad_signature(tmp_path):
    shop, _, _ = make_shop(tmp_path)
    shop.research()
    app = App(shop)
    call(app, "POST", "/checkout", cookies={"cart": cart_cookie({"1": 1})})
    order = shop.db.one("SELECT * FROM orders")
    body, hdr = signed(paid_event(order["public_id"]), secret="whsec_forged")
    assert call(app, "POST", "/webhooks/stripe", raw=body, headers=hdr)["code"] == 400
    assert shop.order(order["public_id"])["status"] == "pending_payment"


def test_expensive_or_unprofitable_orders_go_to_review(tmp_path):
    shop, sup, _ = make_shop(tmp_path, fulfillment__max_auto_order_cost=5)
    shop.research()
    o = buy(shop, App(shop))
    assert shop.fulfill() == 0 and not sup.placed
    assert shop.order(o["public_id"])["status"] == "needs_review"

    shop2, sup2, _ = make_shop(tmp_path / "b")
    shop2.research()
    shop2.db.x("UPDATE variants SET cost = 100")   # supplier price jumped after the sale
    o2 = buy(shop2, App(shop2))
    shop2.fulfill()
    assert shop2.order(o2["public_id"])["status"] == "needs_review" and "lose money" in shop2.order(o2["public_id"])["last_error"]


def test_supplier_rejection_retries_then_review_and_admin_retry(tmp_path):
    shop, sup, _ = make_shop(tmp_path)
    shop.research()
    o = buy(shop, App(shop))
    sup.fail_with = SupplierError("address invalid")
    for _ in range(shop.cfg.fulfillment.max_attempts):
        shop.fulfill()
    assert shop.order(o["public_id"])["status"] == "needs_review"
    sup.fail_with = None
    shop.retry(o["public_id"])
    assert shop.fulfill() == 1 and shop.order(o["public_id"])["status"] == "ordered"


def test_connection_error_never_risks_a_double_order(tmp_path):
    shop, sup, _ = make_shop(tmp_path)
    shop.research()
    o = buy(shop, App(shop))
    sup.fail_with = requests.ConnectionError("timed out")
    shop.fulfill()
    assert shop.order(o["public_id"])["status"] == "needs_review"
    assert shop.fulfill() == 0


def test_supplier_cancellation_refunds_customer(tmp_path):
    shop, sup, stripe = make_shop(tmp_path)
    shop.research()
    o = buy(shop, App(shop))
    shop.fulfill()
    o = shop.order(o["public_id"])
    sup.status[o["supplier_order_id"]] = SupplierOrder(o["supplier_order_id"], "cancelled", "CANCELLED")
    shop.track()
    assert stripe.refunds == [o["payment_intent"]] and shop.order(o["public_id"])["status"] == "refunded"
    assert "refunded in full" in shop.mailer.out[-1][2]


def test_manual_mode_does_not_order(tmp_path):
    shop, sup, _ = make_shop(tmp_path, fulfillment__auto_order=False)
    shop.research()
    buy(shop, App(shop))
    assert shop.fulfill() == 0 and not sup.placed


# --- marketing ---------------------------------------------------------------------

def expired(public_id, consent):
    return {"type": "checkout.session.expired", "data": {"object": {
        "id": "cs_x", "metadata": {"order_id": public_id}, "customer_details": {"email": "shopper@example.com"},
        "after_expiration": {"recovery": {"url": "https://checkout.stripe.test/recover"}},
        "consent": {"promotions": "opt_in" if consent else "opt_out"}}}}


def test_abandoned_checkout_email_only_with_consent(tmp_path):
    shop, _, _ = make_shop(tmp_path)
    shop.research()
    app = App(shop)
    for consent in (False, True):
        call(app, "POST", "/checkout", cookies={"cart": cart_cookie({"1": 1})})
        pid = shop.db.one("SELECT public_id FROM orders ORDER BY id DESC")["public_id"]
        for _ in range(2):
            body, hdr = signed(expired(pid, consent))
            call(app, "POST", "/webhooks/stripe", raw=body, headers=hdr)
        assert shop.order(pid)["status"] == "abandoned"
    sent = [m for m in shop.mailer.out if m[1] == "You left something in your cart"]
    assert len(sent) == 1 and "recover" in sent[0][2] and "Unsubscribe" in sent[0][2]


def test_double_opt_in_newsletter_and_unsubscribe(tmp_path):
    shop, _, _ = make_shop(tmp_path)
    shop.research()
    app = App(shop)
    call(app, "POST", "/subscribe", {"email": "fan@example.com"})
    call(app, "POST", "/subscribe", {"email": "not-an-email"})
    assert shop.newsletter(force=True) == 0          # not confirmed yet
    token = shop.db.one("SELECT token FROM subscribers WHERE email = 'fan@example.com'")["token"]
    assert f"/subscribe/confirm?token={token}" in shop.mailer.out[-1][2]
    call(app, "GET", f"/subscribe/confirm?token={token}")
    assert shop.newsletter(force=True) == 1
    assert shop.newsletter(force=True) == 0          # once per week
    call(app, "POST", f"/unsubscribe?token={token}")
    shop.db.x("DELETE FROM emails")
    assert shop.newsletter(force=True) == 0


# --- admin -----------------------------------------------------------------------------

def auth(user, pw):
    return {"HTTP_AUTHORIZATION": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_admin_requires_password_and_form_token(tmp_path):
    shop, _, _ = make_shop(tmp_path)
    shop.research()
    app = App(shop)
    assert call(app, "GET", "/admin")["code"] == 401
    assert call(app, "GET", "/admin", headers=auth("admin", "wrong"))["code"] == 401
    page = call(app, "GET", "/admin", headers=auth("admin", "pw"))
    assert page["code"] == 200 and "Dashboard" in page["body"]
    pid = shop.db.one("SELECT id FROM products")["id"]
    assert call(app, "POST", "/admin/action", {"action": "hide", "id": pid}, headers=auth("admin", "pw"))["code"] == 400
    r = call(app, "POST", "/admin/action", {"action": "hide", "id": pid, "token": app._token()}, headers=auth("admin", "pw"))
    assert r["code"] == 303 and shop.db.one("SELECT active FROM products WHERE id = ?", (pid,))["active"] == 0
    assert shop.sync()["restored"] == 0      # manual hides survive the stock sync

    shop.cfg.admin.password = ""
    assert call(app, "GET", "/admin", headers=auth("admin", ""))["code"] == 403


def test_demo_checkout_is_refused_when_stripe_is_configured(tmp_path):
    shop, _, _ = make_shop(tmp_path, store__demo=True)
    shop.research()
    app = App(shop)
    call(app, "POST", "/checkout", cookies={"cart": cart_cookie({"1": 1})})
    pid = shop.db.one("SELECT public_id FROM orders")["public_id"]
    assert call(app, "POST", f"/checkout/demo?order={pid}", {"order": pid, "email": "a@b.co"})["code"] == 404
    assert shop.order(pid)["status"] == "pending_payment"


def test_demo_store_end_to_end(tmp_path):
    cfg = Config(db_path=str(tmp_path / "d.db"))
    cfg.store.demo, cfg.copywriting.enabled = True, False
    cfg.email.outbox_dir = str(tmp_path / "outbox")
    shop = Shop(cfg)
    shop.research()
    app = App(shop)
    r = call(app, "POST", "/checkout", cookies={"cart": cart_cookie({"1": 1})})
    loc = dict(r["headers"])["Location"]
    pid = loc.split("=")[1]
    form = {"order": pid, "name": "A B", "email": "a@b.co", "phone": "1", "line1": "x", "city": "y", "state": "z",
            "postal_code": "1", "country": "US"}
    assert call(app, "POST", loc, form)["code"] == 303
    assert shop.fulfill() == 1
    assert list((tmp_path / "outbox").glob("*.eml"))


# --- scheduler, copy, CJ client ------------------------------------------------------------

def test_scheduler_runs_each_job_once_per_interval(tmp_path):
    shop, _, _ = make_shop(tmp_path)
    first = scheduler.run_due(shop, now=1_000_000)
    assert set(first) == {"fulfill", "track", "followups", "newsletter", "sync", "research"}
    assert scheduler.run_due(shop, now=1_000_010) == []
    assert scheduler.run_due(shop, now=1_000_000 + 301) == ["fulfill"]


def test_title_cleanup():
    assert clean_title("2026 New Hot Sale Dog Brush Dog Brush Pet Grooming Free Shipping") == "Dog Brush Pet Grooming"
    assert len(clean_title("word " * 40)) <= 70


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


def test_cj_client_parses_api(tmp_path, monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        return FakeResp({"result": True, "data": {"accessToken": "tok"}})

    def fake_request(method, url, params=None, json=None, timeout=None, headers=None):
        calls.append((method, url.rsplit("/v1/", 1)[1], params, json, headers))
        path = url.rsplit("/v1/", 1)[1]
        data = {
            "product/listV2": {"content": [{"productList": [{"id": "P1", "nameEn": "Dog Brush", "sellPrice": "2.10 -- 3.50", "bigImage": "i", "listedNum": 40}]}]},
            "product/query": {"pid": "P1", "productNameEn": "Dog Brush", "sellPrice": "3.5", "productImageSet": ["a", "b"],
                              "description": "<p>Soft <b>bristles</b></p>", "variants": [
                                  {"vid": "V1", "variantKey": "Blue", "variantSellPrice": 3.5, "inventories": [{"countryCode": "CN", "totalInventory": 12}]}]},
            "logistic/freightCalculate": [{"logisticName": "Slow", "logisticPrice": 2.0, "logisticAging": "15-30"},
                                          {"logisticName": "CJPacket", "logisticPrice": 3.0, "logisticAging": "7-12"},
                                          {"logisticName": "Cheap", "logisticPrice": 2.0, "logisticAging": "10-20"}],
            "shopping/order/createOrderV2": {"orderId": "CJ9", "orderStatus": "CREATED", "orderAmount": 5.5},
            "shopping/pay/payBalance": None,
            "shopping/order/getOrderDetail": {"orderStatus": "SHIPPED", "trackNumber": "YT1", "logisticName": "CJPacket"},
        }[path]
        return FakeResp({"result": True, "code": 200, "data": data})

    monkeypatch.setattr("shopbot.suppliers.requests.post", fake_post)
    monkeypatch.setattr("shopbot.suppliers.requests.request", fake_request)
    monkeypatch.setattr("shopbot.suppliers.time.sleep", lambda s: None)
    cj = CJSupplier("key", "https://x/api2.0/v1", str(tmp_path / "tok.json"), sandbox=True)
    found = cj.search("dog brush")
    assert found[0].pid == "P1" and found[0].cost == 3.5 and found[0].listed_num == 40
    p = cj.product("P1")
    assert p.variants[0].vid == "V1" and p.variants[0].stock == 12 and p.description == "Soft bristles"
    ship = cj.shipping([("V1", 1)], "US")
    assert ship.name == "Cheap" and ship.days_max == 20     # cheapest, then fastest
    so = cj.place_order("ORD1", {"country": "US", "name": "J", "line1": "1 Main", "city": "Austin", "state": "TX",
                                 "postal_code": "78701", "phone": "1"}, [("V1", 2)], "Cheap")
    body = calls[-1][3]
    assert so.order_id == "CJ9" and body["isSandbox"] == 1 and body["payType"] == 3 and body["products"] == [{"vid": "V1", "quantity": 2}]
    assert calls[-1][4]["CJ-Access-Token"] == "tok"
    cj.pay("CJ9")
    st = cj.order("CJ9")
    assert st.status == "shipped" and st.tracking_number == "YT1"
    assert json.loads((tmp_path / "tok.json").read_text())["token"] == "tok"


def test_cj_errors_raise(tmp_path, monkeypatch):
    monkeypatch.setattr("shopbot.suppliers.requests.post", lambda *a, **k: FakeResp({"result": True, "data": {"accessToken": "t"}}))
    monkeypatch.setattr("shopbot.suppliers.requests.request",
                        lambda *a, **k: FakeResp({"result": False, "code": 1600100, "message": "Insufficient balance"}))
    monkeypatch.setattr("shopbot.suppliers.time.sleep", lambda s: None)
    cj = CJSupplier("key", "https://x/api2.0/v1", str(tmp_path / "t.json"))
    with pytest.raises(SupplierError, match="Insufficient balance"):
        cj.pay("CJ9")


def test_copywriter_request_shape_and_refusal_fallback():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen, reply = {}, {"stop_reason": "end_turn"}

    def handler(req):
        seen["body"], seen["beta"] = json.loads(req.content), req.headers.get("anthropic-beta")
        text = json.dumps({"title": "Soft Dog Brush", "description": "A.\n\nB.", "bullets": ["Soft bristles", ""], "seo_description": "x"})
        return httpx2.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                          "content": [{"type": "text", "text": text}], "stop_reason": reply["stop_reason"],
                                          "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
    cw = Copywriter(enabled=False)
    cw.client = anthropic.Anthropic(api_key="test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    out = cw.write("2026 Hot Sale Dog Brush", "Soft", "dog brush")
    assert out["title"] == "Soft Dog Brush" and out["bullets"] == ["Soft bristles"]
    assert seen["body"]["output_config"]["format"]["type"] == "json_schema" and seen["body"]["fallbacks"] == "default"
    assert seen["beta"] == "server-side-fallback-2026-07-01"
    reply["stop_reason"] = "refusal"
    assert cw.write("2026 Hot Sale Dog Brush", "Soft", "dog brush")["title"] == "Dog Brush"


def test_refuses_live_payments_with_fake_products(tmp_path):
    cfg = Config(db_path=str(tmp_path / "x.db"))
    cfg.payments.stripe_secret_key = "sk_live_abc"
    with pytest.raises(RuntimeError, match="demo supplier"):
        Shop(cfg)


def test_interrupted_placement_goes_to_review(tmp_path):
    shop, sup, _ = make_shop(tmp_path)
    shop.research()
    o = buy(shop, App(shop))
    shop.db.x("UPDATE orders SET status = 'ordering', ordered_at = ? WHERE id = ?", (time.time() - 3600, o["id"]))
    assert shop.fulfill() == 0 and not sup.placed
    assert shop.order(o["public_id"])["status"] == "needs_review"
