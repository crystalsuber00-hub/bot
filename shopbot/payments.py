"""Stripe Checkout over plain HTTPS (no SDK needed)."""
from __future__ import annotations

import hashlib
import hmac
import time

import requests

API = "https://api.stripe.com/v1"


class PaymentError(Exception):
    pass


def flatten(value, prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's form encoding: {"a": {"b": [1]}} -> [("a[b][0]", "1")]."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            out += flatten(v, f"{prefix}[{k}]" if prefix else str(k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            out += flatten(v, f"{prefix}[{i}]")
    elif isinstance(value, bool):
        out.append((prefix, "true" if value else "false"))
    elif value is not None:
        out.append((prefix, str(value)))
    return out


def verify_signature(payload: bytes, header: str, secret: str, tolerance: int = 300, now: float | None = None) -> bool:
    """Check a Stripe-Signature header (t=...,v1=...) against the endpoint's signing secret."""
    if not secret or not header:
        return False
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    ts = next((v for k, v in parts if k == "t"), None)
    sigs = [v for k, v in parts if k == "v1"]
    if not ts or not sigs or not ts.isdigit():
        return False
    if abs((now or time.time()) - int(ts)) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)


def cents(x: float) -> int:
    return int(round(x * 100))


class Stripe:
    def __init__(self, secret_key: str):
        self.key = secret_key

    def _post(self, path: str, data: dict, idempotency_key: str = "") -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        r = requests.post(f"{API}/{path}", data=flatten(data), auth=(self.key, ""), headers=headers, timeout=30)
        body = r.json()
        if r.status_code >= 400:
            raise PaymentError(body.get("error", {}).get("message", f"HTTP {r.status_code}"))
        return body

    def create_checkout(self, cfg, order: dict, lines: list[dict]) -> dict:
        s, base = cfg.store, cfg.store.base_url
        data = {
            "mode": "payment",
            "client_reference_id": order["public_id"],
            "metadata": {"order_id": order["public_id"]},
            "payment_intent_data": {"metadata": {"order_id": order["public_id"]}},
            "success_url": f"{base}/checkout/success?order={order['public_id']}",
            "cancel_url": f"{base}/cart",
            "line_items": [{
                "quantity": l["qty"],
                "price_data": {
                    "currency": s.currency,
                    "unit_amount": cents(l["price"]),
                    "product_data": {"name": l["title"][:250], **({"images": [l["image"]]} if l.get("image") else {})},
                },
            } for l in lines],
            "shipping_address_collection": {"allowed_countries": s.ship_to_countries},
            "phone_number_collection": {"enabled": True},   # carriers need a phone number
            "shipping_options": [{"shipping_rate_data": {
                "type": "fixed_amount", "display_name": f"Free tracked shipping ({s.shipping_days})",
                "fixed_amount": {"amount": 0, "currency": s.currency}}}],
        }
        if cfg.marketing.welcome_code:
            data["allow_promotion_codes"] = True   # lets subscribers enter the welcome code
        if cfg.payments.automatic_tax:
            data["automatic_tax"] = {"enabled": True}
        if cfg.payments.recover_abandoned:
            data["consent_collection"] = {"promotions": "auto"}
            data["after_expiration"] = {"recovery": {"enabled": True}}
        return self._post("checkout/sessions", data, idempotency_key=f"checkout-{order['public_id']}")

    def refund(self, payment_intent: str, reason: str = "requested_by_customer") -> dict:
        return self._post("refunds", {"payment_intent": payment_intent, "reason": reason},
                          idempotency_key=f"refund-{payment_intent}")


def session_details(session: dict) -> dict:
    """Customer, shipping address and consent from a checkout.session object (old and new API versions)."""
    cust = session.get("customer_details") or {}
    ship = ((session.get("collected_information") or {}).get("shipping_details")
            or session.get("shipping_details") or {})
    addr = ship.get("address") or cust.get("address") or {}
    consent = (session.get("consent") or {}).get("promotions") == "opt_in"
    return {
        "email": cust.get("email") or "",
        "name": ship.get("name") or cust.get("name") or "",
        "phone": cust.get("phone") or "",
        "address": {k: addr.get(k) or "" for k in ("line1", "line2", "city", "state", "postal_code", "country")},
        "amount_paid": (session.get("amount_total") or 0) / 100,
        "payment_intent": session.get("payment_intent") or "",
        "session_id": session.get("id") or "",
        "marketing_consent": consent,
    }
