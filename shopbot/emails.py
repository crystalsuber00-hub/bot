"""Customer email content. Each function returns (subject, text, html)."""
from __future__ import annotations

from html import escape
from urllib.parse import quote


def _wrap(cfg, inner: str, footer_extra: str = "") -> str:
    s = cfg.store
    return f"""<!doctype html><html><body style="margin:0;background:#f6f6f4;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1c1c1a">
<div style="max-width:560px;margin:0 auto;padding:24px">
<div style="font-size:20px;font-weight:700;margin-bottom:16px">{escape(s.name)}</div>
<div style="background:#fff;border-radius:10px;padding:24px;line-height:1.5">{inner}</div>
<div style="font-size:12px;color:#6b6b66;margin-top:16px;line-height:1.5">{footer_extra}
{escape(s.name)} · {escape(s.business_address)} · <a href="mailto:{escape(s.contact_email)}">{escape(s.contact_email)}</a></div>
</div></body></html>"""


def _btn(url: str, label: str) -> str:
    return (f'<p><a href="{escape(url)}" style="display:inline-block;background:#1c1c1a;color:#fff;padding:12px 20px;'
            f'border-radius:8px;text-decoration:none">{escape(label)}</a></p>')


def _items(items: list[dict]) -> tuple[str, str]:
    text = "\n".join(f"  {i['qty']} x {i['title']}  ${i['price'] * i['qty']:.2f}" for i in items)
    rows = "".join(f"<tr><td>{i['qty']} × {escape(i['title'])}</td><td align=right>${i['price'] * i['qty']:.2f}</td></tr>" for i in items)
    return text, f'<table width="100%" cellpadding="4">{rows}</table>'


def order_confirmation(cfg, order: dict, items: list[dict]):
    url = f"{cfg.store.base_url}/track?order={order['public_id']}&email={quote(order['email'])}"
    t_items, h_items = _items(items)
    subject = f"Order {order['public_id']} confirmed"
    text = (f"Hi {order['name'] or 'there'},\n\nThanks for your order! We're getting it ready.\n\n{t_items}\n"
            f"  Total paid: ${order['amount_paid']:.2f}\n\nShipping takes {cfg.store.shipping_days}. "
            f"We'll email you the tracking number as soon as it ships.\nTrack your order: {url}\n\n"
            f"Questions? Just reply to this email.\n{cfg.store.name}")
    html = _wrap(cfg, f"<h2 style='margin-top:0'>Thanks for your order!</h2><p>Order <b>{order['public_id']}</b> is confirmed "
                      f"and being prepared.</p>{h_items}<p><b>Total paid: ${order['amount_paid']:.2f}</b></p>"
                      f"<p>Shipping takes {escape(cfg.store.shipping_days)}. We'll email your tracking number when it ships.</p>"
                      + _btn(url, "Track your order"))
    return subject, text, html


def shipped(cfg, order: dict):
    url = order["tracking_url"] or f"https://t.17track.net/en#nums={order['tracking_number']}"
    subject = f"Your order {order['public_id']} has shipped"
    text = (f"Hi {order['name'] or 'there'},\n\nGood news: your order is on its way.\n\n"
            f"Carrier: {order['carrier'] or 'see tracking link'}\nTracking number: {order['tracking_number']}\n"
            f"Track it: {url}\n\nTracking can take 2-3 days to show the first scan.\n{cfg.store.name}")
    html = _wrap(cfg, f"<h2 style='margin-top:0'>Your order has shipped</h2><p>Tracking number: <b>{escape(order['tracking_number'])}</b>"
                      f"{' via ' + escape(order['carrier']) if order['carrier'] else ''}</p>"
                      f"<p>Tracking can take 2–3 days to show the first scan.</p>" + _btn(url, "Track package"))
    return subject, text, html


def cancelled(cfg, order: dict, refunded: bool):
    subject = f"Order {order['public_id']} was cancelled"
    money = ("You've been refunded in full; it can take 5-10 days to appear on your statement." if refunded
             else "We'll be in touch about your refund shortly.")
    text = (f"Hi {order['name'] or 'there'},\n\nWe're sorry: our supplier couldn't fulfil your order {order['public_id']}. "
            f"{money}\n\nReply to this email with any questions.\n{cfg.store.name}")
    html = _wrap(cfg, f"<h2 style='margin-top:0'>Order cancelled</h2><p>We're sorry: we couldn't fulfil order "
                      f"<b>{order['public_id']}</b>. {escape(money)}</p><p>Reply to this email with any questions.</p>")
    return subject, text, html


def _product_cards(cfg, products: list[dict]) -> tuple[str, str]:
    base = cfg.store.base_url
    text = "\n".join(f"- {p['title']} (${p['price']:.2f}): {base}/p/{p['slug']}" for p in products)
    cards = "".join(
        f'<td width="50%" valign="top" style="padding:6px"><a href="{base}/p/{escape(p["slug"])}" style="color:#1c1c1a;text-decoration:none">'
        f'<img src="{escape(p["image"])}" width="100%" style="border-radius:8px" alt=""><br>{escape(p["title"])}<br><b>${p["price"]:.2f}</b></a></td>'
        + ("</tr><tr>" if i % 2 else "") for i, p in enumerate(products))
    return text, f'<table width="100%"><tr>{cards}</tr></table>'


def _unsub(url: str) -> tuple[str, str]:
    return f"\n\nUnsubscribe: {url}", f'You get this because you opted in. <a href="{escape(url)}">Unsubscribe</a>.<br>'


def followup(cfg, order: dict, products: list[dict], unsub_url: str):
    t_cards, h_cards = _product_cards(cfg, products)
    t_un, h_un = _unsub(unsub_url)
    subject = "How's your order?"
    text = (f"Hi {order['name'] or 'there'},\n\nYour order {order['public_id']} should have arrived. If anything isn't right, "
            f"just reply and we'll fix it.\n\nYou might also like:\n{t_cards}{t_un}\n{cfg.store.business_address}")
    html = _wrap(cfg, f"<h2 style='margin-top:0'>How's your order?</h2><p>Order <b>{order['public_id']}</b> should have arrived. "
                      f"If anything isn't right, just reply and we'll fix it.</p><p>You might also like:</p>{h_cards}", h_un)
    return subject, text, html


def abandoned(cfg, recovery_url: str, unsub_url: str):
    t_un, h_un = _unsub(unsub_url)
    subject = "You left something in your cart"
    text = f"Hi,\n\nYour cart at {cfg.store.name} is saved. Pick up where you left off:\n{recovery_url}{t_un}\n{cfg.store.business_address}"
    html = _wrap(cfg, "<h2 style='margin-top:0'>Still thinking it over?</h2><p>Your cart is saved, with free tracked shipping.</p>"
                      + _btn(recovery_url, "Complete my order"), h_un)
    return subject, text, html


def confirm_subscription(cfg, confirm_url: str):
    subject = f"Confirm your subscription to {cfg.store.name}"
    text = f"Please confirm you'd like new-product emails from {cfg.store.name}:\n{confirm_url}\n\nIf you didn't sign up, ignore this email."
    html = _wrap(cfg, f"<p>Please confirm you'd like new-product emails from {escape(cfg.store.name)}.</p>"
                      + _btn(confirm_url, "Confirm subscription") + "<p>If you didn't sign up, ignore this email.</p>")
    return subject, text, html


def newsletter(cfg, products: list[dict], unsub_url: str):
    t_cards, h_cards = _product_cards(cfg, products)
    t_un, h_un = _unsub(unsub_url)
    subject = f"New this week at {cfg.store.name}"
    text = f"New arrivals, all with free tracked shipping:\n\n{t_cards}{t_un}\n{cfg.store.business_address}"
    html = _wrap(cfg, f"<h2 style='margin-top:0'>New this week</h2><p>All with free tracked shipping.</p>{h_cards}", h_un)
    return subject, text, html
