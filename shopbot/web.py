"""The storefront as a plain WSGI app (runs under the built-in server, gunicorn, or waitress)."""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlparse

from . import templates as T
from .payments import PaymentError, verify_signature
from .research import product_bullets
from .shop import Shop

log = logging.getLogger("shopbot")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SECURITY_HEADERS = [("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "strict-origin-when-cross-origin"),
                    ("X-Frame-Options", "DENY")]


class Request:
    def __init__(self, environ):
        self.env = environ
        self.method = environ.get("REQUEST_METHOD", "GET")
        self.path = environ.get("PATH_INFO", "/") or "/"
        self.query = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        try:
            length = min(int(environ.get("CONTENT_LENGTH") or 0), 1_000_000)
        except ValueError:
            length = 0
        self.body = environ["wsgi.input"].read(length) if length else b""
        self.form = {}
        if "application/x-www-form-urlencoded" in environ.get("CONTENT_TYPE", ""):
            self.form = {k: v[0] for k, v in parse_qs(self.body.decode("utf-8", "replace")).items()}
        c = SimpleCookie()
        try:
            c.load(environ.get("HTTP_COOKIE", ""))
        except Exception:
            pass
        self.cookies = {k: m.value for k, m in c.items()}


class Response:
    def __init__(self, body: str | bytes = b"", status: str = "200 OK", ctype: str = "text/html; charset=utf-8"):
        self.status = status
        self.body = body.encode() if isinstance(body, str) else body
        self.headers = [("Content-Type", ctype)] + SECURITY_HEADERS

    def cookie(self, name: str, value: str, max_age: int = 60 * 60 * 24 * 30):
        self.headers.append(("Set-Cookie", f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"))
        return self


def redirect(url: str, status: str = "303 See Other") -> Response:
    r = Response(b"", status)
    r.headers.append(("Location", url))
    return r


def read_cart(req: Request) -> dict:
    raw = req.cookies.get("cart", "")
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode() + b"==").decode()) if raw else {}
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): int(v) for k, v in data.items() if str(k).isdigit() and isinstance(v, int) and 0 < v <= 10}


SOCIAL_HOSTS = {"facebook": "facebook", "fb": "facebook", "instagram": "instagram", "pinterest": "pinterest", "pin": "pinterest",
                "tiktok": "tiktok", "youtube": "youtube", "reddit": "reddit", "t": "twitter", "x": "twitter", "twitter": "twitter"}
SEARCH_HOSTS = {"google", "bing", "duckduckgo", "yahoo", "ecosia", "brave"}
NO_TRACK = ("/webhooks/", "/admin", "/feeds/", "/healthz", "/sitemap.xml", "/robots.txt", "/unsubscribe")


def traffic_source(req: Request, own_host: str) -> str:
    """'source/medium' for this visit: UTM tags first, then the referring site; '' for a direct visit."""
    q = req.query
    if q.get("utm_source"):
        src = f"{q['utm_source']}/{q.get('utm_medium') or 'referral'}"
    else:
        host = (urlparse(req.env.get("HTTP_REFERER", "")).hostname or "").lower().removeprefix("www.")
        if not host or host == own_host.removeprefix("www."):
            return ""
        names = set(host.split(".")[:-1])          # "lm.facebook.com" -> {"lm", "facebook"}
        if names & SEARCH_HOSTS:
            src = f"{sorted(names & SEARCH_HOSTS)[0]}/organic"
        elif names & SOCIAL_HOSTS.keys():
            src = f"{SOCIAL_HOSTS[sorted(names & SOCIAL_HOSTS.keys())[0]]}/social"
        else:
            src = f"{host}/referral"
    return re.sub(r"[^a-z0-9._/-]", "", src.lower())[:60]


def cart_cookie(cart: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(cart).encode()).decode().rstrip("=")


class App:
    def __init__(self, shop: Shop):
        self.shop = shop
        self.cfg = shop.cfg
        self.admin_secret = (self.cfg.admin.secret or secrets.token_hex(16)).encode()
        self.routes = [
            ("GET", r"/", self.home), ("GET", r"/p/([a-z0-9-]+)", self.product), ("GET", r"/c/([a-z0-9-]+)", self.collection),
            ("GET", r"/cart", self.cart), ("POST", r"/cart/add", self.cart_add), ("POST", r"/cart/update", self.cart_update),
            ("POST", r"/checkout", self.checkout), ("GET", r"/checkout/success", self.success),
            ("GET", r"/checkout/demo", self.demo_pay), ("POST", r"/checkout/demo", self.demo_pay),
            ("GET", r"/track", self.track), ("POST", r"/subscribe", self.subscribe), ("GET", r"/subscribe/confirm", self.confirm),
            ("GET", r"/unsubscribe", self.unsubscribe), ("POST", r"/unsubscribe", self.unsubscribe),
            ("GET", r"/pages/([a-z]+)", self.policy), ("GET", r"/sitemap.xml", self.sitemap), ("GET", r"/robots.txt", self.robots),
            ("GET", r"/feeds/google.xml", self.feed), ("POST", r"/webhooks/stripe", self.stripe_webhook),
            ("GET", r"/guides", self.guide_list), ("GET", r"/guides/([a-z0-9-]+)", self.guide),
            ("GET", r"/admin", self.admin), ("POST", r"/admin/action", self.admin_action), ("GET", r"/healthz", self.health),
        ]

    # --- WSGI ---------------------------------------------------------------
    def __call__(self, environ, start_response):
        req = Request(environ)
        try:
            resp = self.dispatch(req)
        except Exception:
            log.exception("error on %s %s", req.method, req.path)
            resp = Response(self.page(req, "Something went wrong", T.message("Something went wrong", "<p>Please try again in a moment.</p>")),
                            "500 Internal Server Error")
        if req.method == "GET" and not req.path.startswith(NO_TRACK):
            # remember the last non-direct channel for 30 days so the order can be credited to it
            src = traffic_source(req, urlparse(self.cfg.store.base_url).hostname or "")
            if src or "src" not in req.cookies:
                resp.cookie("src", src or "direct")
        start_response(resp.status, resp.headers)
        return [resp.body]

    def dispatch(self, req: Request) -> Response:
        for method, pattern, handler in self.routes:
            m = re.fullmatch(pattern, req.path)
            if m and method == req.method:
                return handler(req, *m.groups())
        if any(re.fullmatch(p, req.path) for _, p, _ in self.routes):
            return Response("Method not allowed", "405 Method Not Allowed", "text/plain")
        return self.not_found(req)

    def page(self, req: Request, title: str, body: str, **kw) -> str:
        has_guides = bool(self.shop.db.one("SELECT 1 FROM guides LIMIT 1"))
        return T.layout(self.cfg, title, body, cart_count=sum(read_cart(req).values()), collections=self.shop.collections(),
                        demo=self.shop.demo_checkout, guides=has_guides, **kw)

    def not_found(self, req: Request) -> Response:
        return Response(self.page(req, "Not found", T.message("Page not found", '<p><a href="/">Back to the shop</a></p>')), "404 Not Found")

    # --- storefront ---------------------------------------------------------
    def home(self, req):
        return Response(self.page(req, "", T.home(self.cfg, self.shop.products()), canonical=f"{self.cfg.store.base_url}/"))

    def collection(self, req, slug):
        kw = self.shop.collections().get(slug)
        if not kw:
            return self.not_found(req)
        products = self.shop.products("AND p.keyword = ?", (kw,))
        return Response(self.page(req, kw.title(), T.collection(kw, products), description=f"Shop {kw}: free tracked shipping.",
                                  canonical=f"{self.cfg.store.base_url}/c/{slug}"))

    def product(self, req, slug):
        p = self.shop.db.one("SELECT * FROM products WHERE slug = ? AND active = 1", (slug,))
        variants = self.shop.db.q("SELECT * FROM variants WHERE product_id = ? AND active = 1 ORDER BY price", (p["id"],)) if p else []
        if not p or not variants:
            return self.not_found(req)
        images = [i for i in json.loads(p["images"] or "[]") if i] or [p["image"]]
        sel = int(req.query["v"]) if req.query.get("v", "").isdigit() else None
        body = T.product_page(self.cfg, p, variants, images, product_bullets(p), sel)
        return Response(self.page(req, p["title"], body, description=p["seo_description"],
                                  head_extra=T.product_jsonld(self.cfg, p, variants) + f'<meta property="og:image" content="{T.esc(p["image"])}">'
                                  + T.view_event(self.cfg, p, variants[0]["price"]),
                                  canonical=f"{self.cfg.store.base_url}/p/{slug}"))

    def cart(self, req, error: str = ""):
        cart = read_cart(req)
        lines = self.shop.cart_lines(cart)
        resp = Response(self.page(req, "Cart", T.cart_page(lines, error)))
        if len(lines) != len(cart):   # drop items that sold out since they were added
            resp.cookie("cart", cart_cookie({str(l["variant_id"]): l["qty"] for l in lines}))
        return resp

    def _set_qty(self, req, add: bool):
        cart = read_cart(req)
        vid, qty = req.form.get("variant", ""), req.form.get("qty", "1")
        if vid.isdigit() and qty.lstrip("-").isdigit():
            n = (cart.get(vid, 0) if add else 0) + int(qty)
            if n > 0:
                cart[vid] = min(n, 10)
            else:
                cart.pop(vid, None)
        return redirect("/cart").cookie("cart", cart_cookie(cart))

    def cart_add(self, req):
        return self._set_qty(req, add=True)

    def cart_update(self, req):
        return self._set_qty(req, add=False)

    def checkout(self, req):
        lines = self.shop.cart_lines(read_cart(req))
        if not lines:
            return redirect("/cart")
        try:
            src = re.sub(r"[^a-z0-9._/-]", "", req.cookies.get("src", ""))[:60]
            return redirect(self.shop.start_checkout(lines, src))
        except PaymentError as e:
            log.error("checkout failed: %s", e)
            return self.cart(req, "Checkout is temporarily unavailable. Please try again shortly.")

    def success(self, req):
        order = self.shop.order(req.query.get("order", ""))
        if not order:
            return self.not_found(req)
        body = T.message("Thank you!", f"<p>Your order number is <b>{T.esc(order['public_id'])}</b>. A confirmation email is on its way, "
                                       "and we'll email your tracking number as soon as your order ships.</p>"
                                       '<p><a class="btn" href="/">Keep shopping</a></p>')
        return Response(self.page(req, "Thank you", body, head_extra=T.purchase_event(self.cfg, order))).cookie("cart", "", 0)

    def demo_pay(self, req):
        if not self.shop.demo_checkout:
            return self.not_found(req)
        public_id = req.form.get("order") or req.query.get("order", "")
        order = self.shop.order(public_id)
        if not order or order["status"] != "pending_payment":
            return self.not_found(req)
        items = self.shop.items(order["id"])
        if req.method == "POST":
            f = req.form
            if not EMAIL_RE.match(f.get("email", "")):
                return redirect(f"/checkout/demo?order={public_id}")
            self.shop.mark_paid(public_id, {
                "email": f["email"].strip(), "name": f.get("name", ""), "phone": f.get("phone", ""),
                "address": {k: f.get(k, "") for k in ("line1", "line2", "city", "state", "postal_code", "country")},
                "amount_paid": order["subtotal"], "payment_intent": "", "session_id": "",
                "marketing_consent": f.get("consent") == "1"})
            return redirect(f"/checkout/success?order={public_id}")
        return Response(self.page(req, "Checkout", T.demo_checkout(order, items, self.cfg.store.ship_to_countries)))

    def track(self, req):
        oid, email = req.query.get("order", "").strip().upper(), req.query.get("email", "").strip().lower()
        order = self.shop.order(oid) if oid and email else None
        if order and order["email"].lower() != email:
            order = None
        items = self.shop.items(order["id"]) if order else []
        return Response(self.page(req, "Track order", T.track_page(order, items, bool(oid), oid, email)))

    def subscribe(self, req):
        email = req.form.get("email", "").strip()
        if EMAIL_RE.match(email) and len(email) < 200:
            self.shop.subscribe(email)
        return Response(self.page(req, "Almost done", T.message("Check your inbox", "<p>We sent you a link to confirm your subscription.</p>")))

    def confirm(self, req):
        ok = self.shop.confirm(req.query.get("token", ""))
        msg = "<p>You're subscribed. We'll email you when new products arrive.</p>" if ok else "<p>That link isn't valid.</p>"
        return Response(self.page(req, "Subscription", T.message("Subscription", msg)))

    def unsubscribe(self, req):
        # POST is RFC 8058 one-click unsubscribe from the mail client
        self.shop.unsubscribe(req.query.get("token", "") or req.form.get("token", ""))
        if req.method == "POST":
            return Response("ok", ctype="text/plain")
        return Response(self.page(req, "Unsubscribed", T.message("You're unsubscribed", "<p>You won't get marketing emails from us anymore.</p>")))

    def guide_list(self, req):
        guides = self.shop.db.q("SELECT * FROM guides ORDER BY created_at DESC")
        return Response(self.page(req, "Buying guides", T.guide_list(guides), description=f"Buying guides from {self.cfg.store.name}",
                                  canonical=f"{self.cfg.store.base_url}/guides"))

    def guide(self, req, slug):
        g = self.shop.db.one("SELECT * FROM guides WHERE slug = ?", (slug,))
        if not g:
            return self.not_found(req)
        products = {p["id"]: p for p in self.shop.products()}
        return Response(self.page(req, g["title"], T.guide_page(g, json.loads(g["body"]), products), description=g["meta_description"],
                                  canonical=f"{self.cfg.store.base_url}/guides/{slug}"))

    def policy(self, req, name):
        page = T.policy(self.cfg, name)
        if not page:
            return self.not_found(req)
        return Response(self.page(req, page[0], T.message(page[0], page[1])))

    def sitemap(self, req):
        return Response(self.shop.sitemap(), ctype="application/xml")

    def robots(self, req):
        return Response(f"User-agent: *\nDisallow: /admin\nDisallow: /cart\nDisallow: /checkout\nSitemap: {self.cfg.store.base_url}/sitemap.xml\n",
                        ctype="text/plain")

    def feed(self, req):
        return Response(self.shop.google_feed(), ctype="application/xml")

    def health(self, req):
        return Response("ok", ctype="text/plain")

    # --- Stripe -------------------------------------------------------------
    def stripe_webhook(self, req):
        if not verify_signature(req.body, req.env.get("HTTP_STRIPE_SIGNATURE", ""), self.cfg.payments.stripe_webhook_secret):
            return Response("bad signature", "400 Bad Request", "text/plain")
        event = json.loads(req.body)
        self.shop.handle_stripe_event(event)
        return Response("ok", ctype="text/plain")

    # --- admin --------------------------------------------------------------
    def _authorized(self, req) -> bool:
        a = self.cfg.admin
        if not a.password:
            return False
        header = req.env.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return False
        return hmac.compare_digest(user.encode(), a.username.encode()) & hmac.compare_digest(pw.encode(), a.password.encode())

    def _deny(self) -> Response:
        if not self.cfg.admin.password:
            return Response("Admin is disabled: set ADMIN_PASSWORD.", "403 Forbidden", "text/plain")
        r = Response("Login required", "401 Unauthorized", "text/plain")
        r.headers.append(("WWW-Authenticate", 'Basic realm="admin"'))
        return r

    def _token(self) -> str:
        return hmac.new(self.admin_secret, b"admin-form", hashlib.sha256).hexdigest()[:32]

    def admin(self, req, flash: str = ""):
        if not self._authorized(req):
            return self._deny()
        db = self.shop.db
        orders = db.q("SELECT * FROM orders WHERE status != 'pending_payment' OR created_at > strftime('%s','now') - 86400 "
                      "ORDER BY CASE status WHEN 'needs_review' THEN 0 ELSE 1 END, id DESC LIMIT 100")
        products = db.q("SELECT p.*, MIN(v.price) AS price FROM products p LEFT JOIN variants v ON v.product_id = p.id "
                        "GROUP BY p.id ORDER BY p.active DESC, p.score DESC LIMIT 200")
        events = db.q("SELECT * FROM events ORDER BY id DESC LIMIT 60")
        for e in events:
            e["when"] = dt.datetime.fromtimestamp(e["ts"]).strftime("%b %d %H:%M")
        body = T.admin_page(self.cfg, self.shop.stats(), orders, products, events, self._token(), flash or req.query.get("msg", ""))
        r = Response(self.page(req, "Admin", body))
        r.headers.append(("Cache-Control", "no-store"))
        return r

    def admin_action(self, req):
        if not self._authorized(req):
            return self._deny()
        if not hmac.compare_digest(req.form.get("token", ""), self._token()):
            return Response("bad token", "400 Bad Request", "text/plain")
        action, ident = req.form.get("action", ""), req.form.get("id", "")
        shop, msg = self.shop, ""
        if action == "retry":
            shop.retry(ident)
            msg = f"Order {ident} queued again."
        elif action in ("hide", "show") and ident.isdigit():
            shop.db.x("UPDATE products SET active = ?, inactive_reason = ? WHERE id = ?",
                      (int(action == "show"), "" if action == "show" else "manual", int(ident)))
            msg = "Product updated."
        elif action in ("research", "sync", "fulfill", "track", "social", "report"):
            target = (lambda: shop.report(force=True)) if action == "report" else getattr(shop, action)
            threading.Thread(target=target, daemon=True, name=f"admin-{action}").start()
            msg = f"Started '{action}' in the background; refresh in a minute."
        return redirect(f"/admin?msg={quote(msg)}")
