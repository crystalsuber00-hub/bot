"""The business logic: orders from cart to doorstep, plus the automated marketing."""
from __future__ import annotations

import datetime as dt
import json
import logging
import secrets
import time
from xml.sax.saxutils import escape as xml_escape

import requests

from . import emails
from .config import Config
from .copywriter import Copywriter
from .db import DB, jdump
from .mailer import Mailer, Notifier
from .payments import PaymentError, Stripe, session_details
from .research import run_research, slugify, sync_products
from .social import SocialPoster
from .suppliers import SupplierError, make_supplier

log = logging.getLogger("shopbot")

PRODUCT_LIST_SQL = """
SELECT p.*, MIN(v.price) AS price, MIN(v.compare_at) AS compare_at
FROM products p JOIN variants v ON v.product_id = p.id AND v.active = 1
WHERE p.active = 1 {where}
GROUP BY p.id {order}
"""


class Shop:
    def __init__(self, cfg: Config, supplier=None, stripe=None, mailer=None, copywriter=None):
        self.cfg = cfg
        self.db = DB(cfg.db_path)
        self.supplier = supplier or make_supplier(cfg)
        self.mailer = mailer or Mailer(cfg)
        self.notifier = Notifier(cfg, self.mailer)
        key = cfg.payments.stripe_secret_key
        if self.supplier.name == "demo" and "_live_" in key:
            raise RuntimeError("Refusing to start: the demo supplier sells made-up products, but a live Stripe key is set. "
                               "Set [supplier] name = \"cj\" in your config.")
        self.stripe = stripe or (Stripe(key) if key else None)
        self.copy = copywriter or Copywriter(cfg.copywriting.enabled, cfg.copywriting.model)

    @property
    def demo_checkout(self) -> bool:
        return self.cfg.store.demo and not self.cfg.payments.stripe_secret_key

    # --- catalogue ----------------------------------------------------------
    def products(self, where: str = "", args=(), order: str = "ORDER BY p.score DESC", limit: int = 0) -> list[dict]:
        sql = PRODUCT_LIST_SQL.format(where=where, order=order) + (f" LIMIT {int(limit)}" if limit else "")
        return self.db.q(sql, args)

    def research(self) -> list[int]:
        added = run_research(self.cfg, self.db, self.supplier, self.copy)
        if added:
            self.notifier.alert(f"{len(added)} new products listed", ", ".join(
                p["title"] for p in self.db.q(f"SELECT title FROM products WHERE id IN ({','.join('?' * len(added))})", added)))
        return added

    def sync(self) -> dict:
        stats = sync_products(self.cfg, self.db, self.supplier)
        log.info("sync: %s", stats)
        return stats

    # --- checkout -----------------------------------------------------------
    def cart_lines(self, cart: dict) -> list[dict]:
        """Validated cart lines priced from the database (never from the browser)."""
        lines = []
        for vid, qty in cart.items():
            try:
                vid, qty = int(vid), max(1, min(int(qty), 10))
            except (TypeError, ValueError):
                continue
            row = self.db.one(
                "SELECT v.id AS variant_id, v.supplier_vid, v.name AS variant, v.price, v.cost, v.image AS vimage, "
                "p.id AS product_id, p.title, p.slug, p.image, p.ship_cost FROM variants v JOIN products p ON p.id = v.product_id "
                "WHERE v.id = ? AND v.active = 1 AND p.active = 1", (vid,))
            if row:
                row["qty"] = qty
                row["image"] = row["vimage"] or row["image"]
                row["title"] = row["title"] + (f" ({row['variant']})" if row["variant"] else "")
                lines.append(row)
        return lines

    def create_order(self, lines: list[dict], source: str = "") -> dict:
        public_id = secrets.token_hex(4).upper()
        subtotal = round(sum(l["price"] * l["qty"] for l in lines), 2)
        oid = self.db.x("INSERT INTO orders (public_id, status, subtotal, source, created_at) VALUES (?, 'pending_payment', ?, ?, ?)",
                        (public_id, subtotal, source[:80], time.time()))
        for l in lines:
            self.db.x("INSERT INTO order_items (order_id, product_id, variant_id, supplier_vid, title, qty, price, cost, ship_cost) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (oid, l["product_id"], l["variant_id"], l["supplier_vid"], l["title"], l["qty"], l["price"], l["cost"], l["ship_cost"]))
        return self.db.one("SELECT * FROM orders WHERE id = ?", (oid,))

    def start_checkout(self, lines: list[dict], source: str = "") -> str:
        """Create the order and return the URL to send the shopper to. source = the marketing channel that brought them."""
        order = self.create_order(lines, source)
        if self.stripe:
            session = self.stripe.create_checkout(self.cfg, order, lines)
            self.db.x("UPDATE orders SET stripe_session_id = ? WHERE id = ?", (session["id"], order["id"]))
            return session["url"]
        if self.demo_checkout:
            return f"/checkout/demo?order={order['public_id']}"
        raise PaymentError("Payments are not configured (set STRIPE_SECRET_KEY)")

    def items(self, order_id: int) -> list[dict]:
        return self.db.q("SELECT * FROM order_items WHERE order_id = ?", (order_id,))

    def order(self, public_id: str) -> dict | None:
        return self.db.one("SELECT * FROM orders WHERE public_id = ?", (public_id,))

    def mark_paid(self, public_id: str, d: dict) -> bool:
        """Payment confirmed. Idempotent: Stripe retries webhooks, so a second call does nothing."""
        claimed = self.db.claim(
            "UPDATE orders SET status = 'paid', email = ?, name = ?, phone = ?, address = ?, amount_paid = ?, "
            "payment_intent = ?, marketing_consent = ?, paid_at = ? WHERE public_id = ? AND status IN ('pending_payment', 'abandoned')",
            (d["email"], d["name"], d["phone"], jdump(d["address"]), d["amount_paid"], d["payment_intent"],
             int(d["marketing_consent"]), time.time(), public_id))
        if not claimed:
            return False
        order = self.order(public_id)
        items = self.items(order["id"])
        self.db.log("order", f"Order {public_id} paid: ${order['amount_paid']:.2f} from {order['email']}")
        if d["marketing_consent"] and order["email"]:
            self.add_subscriber(order["email"], "checkout", confirmed=True)
        if self.db.mark_email("confirmation", public_id, order["email"]):
            self.mailer.send(order["email"], *emails.order_confirmation(self.cfg, order, items))
        self.notifier.alert(f"New order ${order['amount_paid']:.2f}", ", ".join(f"{i['qty']}x {i['title']}" for i in items)
                            + ("" if self.cfg.fulfillment.auto_order else "\nAuto-ordering is off: place it from /admin."))
        return True

    def handle_stripe_event(self, event: dict) -> None:
        kind, obj = event.get("type", ""), (event.get("data") or {}).get("object") or {}
        public_id = (obj.get("metadata") or {}).get("order_id") or obj.get("client_reference_id")
        if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            if obj.get("payment_status") == "paid" and public_id:
                self.mark_paid(public_id, session_details(obj))
        elif kind == "checkout.session.expired" and public_id:
            self.recover_abandoned(public_id, obj)
        elif kind == "charge.refunded":
            pi = obj.get("payment_intent")
            if pi and self.db.claim("UPDATE orders SET status = 'refunded' WHERE payment_intent = ? AND status != 'refunded'", (pi,)):
                self.db.log("order", f"Payment {pi} refunded")
        elif kind == "charge.dispute.created":
            self.notifier.alert("Chargeback opened", f"Payment {obj.get('payment_intent')}: respond in the Stripe dashboard", urgent=True)

    # --- fulfillment --------------------------------------------------------
    def fulfill(self) -> int:
        """Send paid orders to the supplier (and pay for them)."""
        f, placed = self.cfg.fulfillment, 0
        # an order stuck mid-placement means the process died while talking to the supplier: a human checks it
        for order in self.db.q("SELECT * FROM orders WHERE status = 'ordering' AND ordered_at < ?", (time.time() - 600,)):
            self._review(order, "interrupted while placing the supplier order: check the supplier dashboard before retrying")
        if not f.auto_order:
            return 0
        for order in self.db.q("SELECT * FROM orders WHERE status = 'paid' ORDER BY paid_at"):
            if not self.db.claim("UPDATE orders SET status = 'ordering', ordered_at = ? WHERE id = ? AND status = 'paid'",
                                 (time.time(), order["id"])):
                continue   # another worker got it
            placed += self._place(order)
        return placed

    def _review(self, order: dict, reason: str) -> None:
        self.db.x("UPDATE orders SET status = 'needs_review', last_error = ? WHERE id = ?", (reason, order["id"]))
        self.db.log("order", f"Order {order['public_id']} needs review: {reason}")
        self.notifier.alert(f"Order {order['public_id']} needs you", reason, urgent=True)

    def _place(self, order: dict) -> int:
        f = self.cfg.fulfillment
        items = self.items(order["id"])
        addr = json.loads(order["address"])
        lines = [(i["supplier_vid"], i["qty"]) for i in items]
        ship = self.supplier.shipping(lines, addr.get("country") or self.cfg.store.ship_to_countries[0], self.cfg.research.ship_from)
        if ship is None:
            self._review(order, "supplier has no shipping option to this address")
            return 0
        est_cost = round(sum(i["cost"] * i["qty"] for i in items) + ship.cost, 2)
        fees = order["amount_paid"] * self.cfg.pricing.card_fee_pct + self.cfg.pricing.card_fee_fixed
        if est_cost > f.max_auto_order_cost:
            self._review(order, f"supplier cost ${est_cost:.2f} is above max_auto_order_cost")
            return 0
        if est_cost + fees > order["amount_paid"]:
            self._review(order, f"would lose money: supplier ${est_cost:.2f} + fees ${fees:.2f} > paid ${order['amount_paid']:.2f}")
            return 0
        ship_to = dict(addr, name=order["name"], phone=order["phone"], email=order["email"])
        try:
            so = self.supplier.place_order(order["public_id"], ship_to, lines, ship.name, self.cfg.research.ship_from)
        except requests.RequestException as e:
            # we can't tell whether the supplier received it, so a human checks rather than risking a double order
            self._review(order, f"connection error while placing supplier order (check supplier dashboard): {e}")
            return 0
        except SupplierError as e:
            attempts = order["attempts"] + 1
            if attempts >= f.max_attempts:
                self.db.x("UPDATE orders SET attempts = ? WHERE id = ?", (attempts, order["id"]))
                self._review(order, f"supplier rejected the order {attempts} times: {e}")
            else:
                self.db.x("UPDATE orders SET status = 'paid', attempts = ?, last_error = ? WHERE id = ?", (attempts, str(e), order["id"]))
            return 0
        self.db.x("UPDATE orders SET status = 'ordered', supplier_order_id = ?, supplier_status = ?, supplier_cost = ?, "
                  "ordered_at = ?, last_error = '' WHERE id = ?",
                  (so.order_id, so.raw_status, so.cost or est_cost, time.time(), order["id"]))
        self.db.log("order", f"Order {order['public_id']} placed with supplier as {so.order_id} (${so.cost or est_cost:.2f})")
        if f.auto_pay:
            self._pay_supplier(self.order(order["public_id"]))
        return 1

    def _pay_supplier(self, order: dict) -> None:
        try:
            self.supplier.pay(order["supplier_order_id"])
            self.db.x("UPDATE orders SET supplier_status = 'PAID' WHERE id = ?", (order["id"],))
        except (SupplierError, requests.RequestException) as e:
            self._review(order, f"supplier order {order['supplier_order_id']} created but payment failed "
                                f"(top up your supplier wallet, then press Retry): {e}")

    def retry(self, public_id: str) -> None:
        """Admin action for an order in review."""
        order = self.order(public_id)
        if not order or order["status"] != "needs_review":
            return
        if order["supplier_order_id"]:
            self.db.x("UPDATE orders SET status = 'ordered', last_error = '' WHERE id = ?", (order["id"],))
            self._pay_supplier(self.order(public_id))
        else:
            self.db.x("UPDATE orders SET status = 'paid', attempts = 0, last_error = '' WHERE id = ?", (order["id"],))

    def track(self) -> int:
        """Pull supplier status: email tracking numbers, mark deliveries, refund cancellations."""
        changed = 0
        for order in self.db.q("SELECT * FROM orders WHERE status IN ('ordered', 'shipped') AND supplier_order_id != ''"):
            try:
                so = self.supplier.order(order["supplier_order_id"])
            except (SupplierError, requests.RequestException) as e:
                log.warning("tracking %s: %s", order["public_id"], e)
                continue
            now = time.time()
            if so.status == "cancelled":
                self._cancelled(order)
                changed += 1
                continue
            if so.tracking_number and not order["tracking_number"]:
                self.db.x("UPDATE orders SET status = 'shipped', tracking_number = ?, tracking_url = ?, carrier = ?, "
                          "shipped_at = ?, supplier_status = ? WHERE id = ?",
                          (so.tracking_number, so.tracking_url, so.carrier, now, so.raw_status, order["id"]))
                order = self.order(order["public_id"])
                if self.db.mark_email("shipped", order["public_id"], order["email"]):
                    self.mailer.send(order["email"], *emails.shipped(self.cfg, order))
                self.db.log("order", f"Order {order['public_id']} shipped: {so.tracking_number}")
                changed += 1
            if so.status == "delivered":
                self.db.x("UPDATE orders SET status = 'delivered', delivered_at = ?, supplier_status = ? WHERE id = ?",
                          (now, so.raw_status, order["id"]))
                self.db.log("order", f"Order {order['public_id']} delivered")
                changed += 1
            elif so.raw_status != order["supplier_status"]:
                self.db.x("UPDATE orders SET supplier_status = ? WHERE id = ?", (so.raw_status, order["id"]))
        return changed

    def _cancelled(self, order: dict) -> None:
        refunded = False
        if self.cfg.fulfillment.auto_refund_cancelled and self.stripe and order["payment_intent"]:
            try:
                self.stripe.refund(order["payment_intent"])
                refunded = True
            except (PaymentError, requests.RequestException) as e:
                self.notifier.alert(f"Refund failed for {order['public_id']}", str(e), urgent=True)
        self.db.x("UPDATE orders SET status = ? WHERE id = ?", ("refunded" if refunded else "cancelled", order["id"]))
        if self.db.mark_email("cancelled", order["public_id"], order["email"]):
            self.mailer.send(order["email"], *emails.cancelled(self.cfg, order, refunded))
        self.notifier.alert(f"Supplier cancelled order {order['public_id']}",
                            "Customer refunded automatically." if refunded else "Refund the customer manually.", urgent=not refunded)

    # --- marketing ----------------------------------------------------------
    def add_subscriber(self, email: str, source: str, confirmed: bool = False) -> dict:
        email = email.strip().lower()
        row = self.db.one("SELECT * FROM subscribers WHERE email = ?", (email,))
        if row is None:
            self.db.x("INSERT INTO subscribers (email, token, confirmed, source, created_at) VALUES (?, ?, ?, ?, ?)",
                      (email, secrets.token_urlsafe(16), int(confirmed), source, time.time()))
        elif confirmed:
            # consenting at checkout is an explicit opt-in, even after an earlier unsubscribe
            self.db.x("UPDATE subscribers SET confirmed = 1, unsubscribed_at = NULL WHERE email = ?", (email,))
        elif row["unsubscribed_at"]:
            # signing up again on the website: needs a fresh confirmation click
            self.db.x("UPDATE subscribers SET confirmed = 0, unsubscribed_at = NULL WHERE email = ?", (email,))
        return self.db.one("SELECT * FROM subscribers WHERE email = ?", (email,))

    def subscribe(self, email: str) -> None:
        """Double opt-in signup from the website."""
        sub = self.add_subscriber(email, "website")
        if not sub["confirmed"]:
            url = f"{self.cfg.store.base_url}/subscribe/confirm?token={sub['token']}"
            self.mailer.send(sub["email"], *emails.confirm_subscription(self.cfg, url))

    def confirm(self, token: str) -> bool:
        if not self.db.claim("UPDATE subscribers SET confirmed = 1, confirmed_at = ?, unsubscribed_at = NULL "
                             "WHERE token = ? AND confirmed = 0", (time.time(), token)):
            return bool(self.db.one("SELECT 1 FROM subscribers WHERE token = ? AND confirmed = 1", (token,)))
        sub = self.db.one("SELECT * FROM subscribers WHERE token = ?", (token,))
        if self.db.mark_email("welcome1", sub["email"], sub["email"]):
            unsub = f"{self.cfg.store.base_url}/unsubscribe?token={token}"
            self.mailer.send(sub["email"], *emails.welcome(self.cfg, self.products(limit=4), unsub), unsubscribe_url=unsub)
        return True

    def unsubscribe(self, token: str) -> bool:
        return self.db.claim("UPDATE subscribers SET unsubscribed_at = ? WHERE token = ? AND unsubscribed_at IS NULL",
                             (time.time(), token))

    def unsub_url(self, email: str) -> str:
        sub = self.add_subscriber(email, "checkout")
        return f"{self.cfg.store.base_url}/unsubscribe?token={sub['token']}"

    def _subscribed(self, email: str) -> bool:
        row = self.db.one("SELECT confirmed, unsubscribed_at FROM subscribers WHERE email = ?", (email.strip().lower(),))
        return bool(row and row["confirmed"] and not row["unsubscribed_at"])

    def recover_abandoned(self, public_id: str, session: dict) -> None:
        """Checkout expired unpaid: one reminder email, only if the shopper agreed to marketing emails at checkout."""
        if not self.db.claim("UPDATE orders SET status = 'abandoned' WHERE public_id = ? AND status = 'pending_payment'", (public_id,)):
            return
        email = (session.get("customer_details") or {}).get("email") or ""
        url = ((session.get("after_expiration") or {}).get("recovery") or {}).get("url") or ""
        consent = (session.get("consent") or {}).get("promotions") == "opt_in"
        if not (email and url and consent):
            return
        self.add_subscriber(email, "checkout", confirmed=True)
        if self._subscribed(email) and self.db.mark_email("abandoned", public_id, email):
            unsub = self.unsub_url(email)
            self.mailer.send(email, *emails.abandoned(self.cfg, url, unsub), unsubscribe_url=unsub)
            self.db.log("marketing", f"Abandoned-cart email sent for {public_id}")

    def followups(self) -> int:
        """Thank-you + recommendations a few days after delivery, for customers who opted in."""
        cutoff = time.time() - self.cfg.marketing.followup_days * 86400
        sent = 0
        for order in self.db.q("SELECT * FROM orders WHERE status = 'delivered' AND followup_sent_at IS NULL AND delivered_at <= ?", (cutoff,)):
            self.db.x("UPDATE orders SET followup_sent_at = ? WHERE id = ?", (time.time(), order["id"]))
            if not (order["marketing_consent"] and self._subscribed(order["email"])):
                continue
            bought = {i["product_id"] for i in self.items(order["id"])}
            recs = [p for p in self.products(limit=8) if p["id"] not in bought][:4]
            if self.db.mark_email("followup", order["public_id"], order["email"]):
                unsub = self.unsub_url(order["email"])
                self.mailer.send(order["email"], *emails.followup(self.cfg, order, recs, unsub), unsubscribe_url=unsub)
                sent += 1
        return sent

    def email_flows(self) -> int:
        """Welcome email #2 (best sellers) a few days after signup, and a win-back invite after a quiet spell."""
        m, now, sent = self.cfg.marketing, time.time(), 0
        for sub in self.db.q("SELECT * FROM subscribers WHERE confirmed = 1 AND unsubscribed_at IS NULL AND source = 'website' "
                             "AND confirmed_at IS NOT NULL AND confirmed_at <= ?", (now - m.welcome_second_email_days * 86400,)):
            if self.db.mark_email("welcome2", sub["email"], sub["email"]):
                unsub = f"{self.cfg.store.base_url}/unsubscribe?token={sub['token']}"
                self.mailer.send(sub["email"], *emails.welcome_best(self.cfg, self.best_sellers(4), unsub), unsubscribe_url=unsub)
                sent += 1
        if m.winback_days > 0:
            for o in self.db.q("SELECT * FROM orders WHERE status = 'delivered' AND marketing_consent = 1 AND delivered_at <= ? "
                               "AND delivered_at > ?", (now - m.winback_days * 86400, now - (m.winback_days + 30) * 86400)):
                # skip customers who have bought again since
                again = self.db.one("SELECT 1 FROM orders WHERE lower(email) = lower(?) AND paid_at > ?", (o["email"], o["paid_at"]))
                if again or not self._subscribed(o["email"]) or not self.db.mark_email("winback", o["email"].lower(), o["email"]):
                    continue
                unsub = self.unsub_url(o["email"])
                bought = {i["product_id"] for i in self.items(o["id"])}
                recs = [p for p in self.products(order="ORDER BY p.created_at DESC", limit=10) if p["id"] not in bought][:4]
                self.mailer.send(o["email"], *emails.winback(self.cfg, o, recs, unsub), unsubscribe_url=unsub)
                sent += 1
        if sent:
            self.db.log("marketing", f"Sent {sent} welcome/win-back emails")
        return sent

    def best_sellers(self, limit: int = 4, since: float = 0) -> list[dict]:
        """Live products ranked by units sold (then research score)."""
        return self.products(
            "", (),
            f"ORDER BY (SELECT COALESCE(SUM(oi.qty), 0) FROM order_items oi JOIN orders o ON o.id = oi.order_id "
            f"WHERE oi.product_id = p.id AND o.paid_at IS NOT NULL AND o.paid_at >= {float(since)}) DESC, p.score DESC", limit)

    def social(self) -> int:
        return SocialPoster(self.cfg, self.db, self.copy).run(self.products())

    def guides(self, force: bool = False) -> int:
        """One new buying guide a week, rotating through collections. Needs Claude; skipped otherwise."""
        if not self.cfg.marketing.guides or self.copy.client is None:
            return 0
        last = self.db.one("SELECT MAX(created_at) AS t FROM guides")["t"]
        if not force and last and time.time() - last < 7 * 86400:
            return 0
        counts = {r["keyword"]: r["n"] for r in self.db.q("SELECT keyword, COUNT(*) AS n FROM guides GROUP BY keyword")}
        for kw in sorted(self.collections().values(), key=lambda k: counts.get(k, 0)):
            products = self.products("AND p.keyword = ?", (kw,), limit=12)
            if len(products) < 3:
                continue
            g = self.copy.guide(kw, products)
            if not g:
                return 0
            slug, n = slugify(g["title"]), 2
            base = slug
            while self.db.one("SELECT 1 FROM guides WHERE slug = ?", (slug,)):
                slug, n = f"{base}-{n}", n + 1
            self.db.x("INSERT INTO guides (slug, keyword, title, meta_description, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (slug, kw, g["title"], g["meta_description"][:160], jdump(g["sections"]), time.time()))
            self.db.log("marketing", f"Published guide '{g['title']}'")
            return 1
        return 0

    def newsletter(self, force: bool = False) -> int:
        """Weekly new-arrivals email to confirmed subscribers (skipped when nothing new was listed)."""
        m = self.cfg.marketing
        now = dt.datetime.now(dt.timezone.utc)
        if not force and (not m.newsletter or now.weekday() != m.newsletter_weekday or now.hour < m.newsletter_hour_utc):
            return 0
        week = now.strftime("%G-W%V")
        new = self.products("AND p.created_at >= ?", (time.time() - 7 * 86400,), "ORDER BY p.score DESC", limit=6)
        if not new:
            return 0
        sent = 0
        for sub in self.db.q("SELECT * FROM subscribers WHERE confirmed = 1 AND unsubscribed_at IS NULL"):
            if self.db.mark_email("newsletter", f"{week}:{sub['email']}", sub["email"]):
                unsub = f"{self.cfg.store.base_url}/unsubscribe?token={sub['token']}"
                self.mailer.send(sub["email"], *emails.newsletter(self.cfg, new, unsub), unsubscribe_url=unsub)
                sent += 1
        if sent:
            self.db.log("marketing", f"Newsletter {week} sent to {sent} subscribers")
        return sent

    def google_feed(self) -> str:
        """Product feed for Google Merchant Center (free Shopping listings + Shopping ads). Meta and Pinterest catalogues accept it too."""
        s, base = self.cfg.store, self.cfg.store.base_url
        items = []
        for p in self.products():
            for v in self.db.q("SELECT * FROM variants WHERE product_id = ? AND active = 1", (p["id"],)):
                title = p["title"] + (f" - {v['name']}" if v["name"] else "")
                desc = p["seo_description"] or p["description"]
                items.append(f"""<item>
<g:id>{v['id']}</g:id><g:item_group_id>{p['id']}</g:item_group_id>
<title>{xml_escape(title[:150])}</title><description>{xml_escape(desc[:5000])}</description>
<link>{base}/p/{xml_escape(p['slug'])}?v={v['id']}&amp;utm_source=google&amp;utm_medium=shopping</link><g:image_link>{xml_escape(v['image'] or p['image'])}</g:image_link>
<g:availability>in_stock</g:availability><g:price>{v['price']:.2f} {s.currency.upper()}</g:price>
<g:condition>new</g:condition><g:identifier_exists>no</g:identifier_exists>
<g:product_type>{xml_escape(p['category'] or p['keyword'])}</g:product_type>
<g:shipping><g:country>{xml_escape(s.ship_to_countries[0])}</g:country><g:price>0.00 {s.currency.upper()}</g:price></g:shipping>
</item>""")
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0"><channel>'
                f"<title>{xml_escape(s.name)}</title><link>{base}</link><description>{xml_escape(s.tagline)}</description>\n"
                + "\n".join(items) + "\n</channel></rss>")

    def sitemap(self) -> str:
        base = self.cfg.store.base_url
        urls = [f"{base}/"] + [f"{base}/c/{k}" for k in self.collections()] + [f"{base}/p/{p['slug']}" for p in self.products()]
        urls += [f"{base}/pages/{p}" for p in ("shipping", "returns", "contact")]
        guides = self.db.q("SELECT slug FROM guides")
        urls += [f"{base}/guides"] * bool(guides) + [f"{base}/guides/{g['slug']}" for g in guides]
        body = "".join(f"<url><loc>{xml_escape(u)}</loc></url>" for u in urls)
        return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'

    def collections(self) -> dict[str, str]:
        """slug -> keyword for every keyword with live products."""
        return {slugify(r["keyword"]): r["keyword"] for r in self.db.q("SELECT DISTINCT keyword FROM products WHERE active = 1")}

    # --- reporting ----------------------------------------------------------
    LIVE = "('paid','ordering','ordered','shipped','delivered','needs_review')"

    def stats(self, since: float = 0, until: float = 1e12) -> dict:
        p = self.cfg.pricing
        window = "AND COALESCE(paid_at, created_at) >= ? AND COALESCE(paid_at, created_at) < ?"
        row = self.db.one(f"""SELECT COUNT(*) AS orders, COALESCE(SUM(amount_paid), 0) AS revenue,
            COALESCE(SUM(CASE WHEN supplier_cost > 0 THEN supplier_cost ELSE
                (SELECT SUM((cost + ship_cost) * qty) FROM order_items WHERE order_id = orders.id) END), 0) AS cogs,
            COALESCE(SUM(amount_paid * ? + ?), 0) AS fees
            FROM orders WHERE status IN {self.LIVE} {window}""", (p.card_fee_pct, p.card_fee_fixed, since, until))
        fees = row["fees"]
        by_source = self.db.q(f"""SELECT CASE source WHEN '' THEN 'unknown' ELSE source END AS source, COUNT(*) AS orders,
            ROUND(SUM(amount_paid), 2) AS revenue FROM orders WHERE status IN {self.LIVE} {window}
            GROUP BY 1 ORDER BY revenue DESC""", (since, until))
        by_status = {r["status"]: r["n"] for r in self.db.q("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")}
        return {
            "orders": row["orders"], "revenue": round(row["revenue"], 2),
            "profit": round(row["revenue"] - row["cogs"] - fees, 2),
            "by_status": by_status, "by_source": by_source,
            "products": self.db.one("SELECT COUNT(*) AS n FROM products WHERE active = 1")["n"],
            "subscribers": self.db.one("SELECT COUNT(*) AS n FROM subscribers WHERE confirmed = 1 AND unsubscribed_at IS NULL")["n"],
        }

    def report(self, force: bool = False) -> bool:
        """Weekly email to the owner: last 7 days vs the week before, what's selling, where buyers came from,
        what the bot posted, and this week's short-video ideas."""
        now = dt.datetime.now(dt.timezone.utc)
        week = now.strftime("%G-W%V")
        to = self.cfg.notify.owner_email
        if not force and (now.weekday() != self.cfg.marketing.report_weekday or now.hour < 13):
            return False
        if not to or not self.db.mark_email("report", week, to):
            return False
        t = time.time()
        cur, prev = self.stats(t - 7 * 86400, t), self.stats(t - 14 * 86400, t - 7 * 86400)
        top = self.best_sellers(3, since=t - 30 * 86400)
        posts = self.db.q("SELECT channel, SUM(status = 'posted') AS ok, SUM(status = 'error') AS failed FROM social_posts "
                          "WHERE posted_at >= ? GROUP BY channel", (t - 7 * 86400,))
        new_subs = self.db.one("SELECT COUNT(*) AS n FROM subscribers WHERE created_at >= ? AND confirmed = 1", (t - 7 * 86400,))["n"]
        ideas = self.copy.video_ideas(top) if top else []
        self.mailer.send(to, *emails.owner_report(self.cfg, week, cur, prev, top, posts, new_subs, ideas))
        self.db.log("report", f"Weekly report {week} sent")
        return True
