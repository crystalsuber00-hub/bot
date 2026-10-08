"""HTML for the storefront and admin. Everything user- or supplier-supplied goes through esc()."""
from __future__ import annotations

import json
from html import escape
from urllib.parse import quote


def esc(v) -> str:
    return escape(str(v if v is not None else ""), quote=True)


def money(x: float) -> str:
    return f"${x:,.2f}"


CSS = """
:root{--bg:#f7f6f2;--surface:#fff;--text:#1d1c19;--muted:#6c6a63;--line:#e4e1d8;--accent:#1f5f4a;--accent-text:#fff;--sale:#b4442c;--radius:12px}
@media (prefers-color-scheme:dark){:root{--bg:#141412;--surface:#1e1d1a;--text:#eeece6;--muted:#a19e95;--line:#34322d;--accent:#5fbf9a;--accent-text:#0d1a15;--sale:#ef8a6f}}
*{box-sizing:border-box}html,body{overflow-x:hidden}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif}
a{color:inherit}img{max-width:100%;display:block}
.wrap{max-width:1120px;margin:0 auto;padding:0 16px}
header{border-bottom:1px solid var(--line);background:var(--surface)}
header .wrap{display:flex;align-items:center;gap:20px;min-height:64px;flex-wrap:wrap}
.logo{font-weight:800;font-size:20px;text-decoration:none;letter-spacing:-.02em}
nav{display:flex;gap:16px;flex:1;flex-wrap:wrap}nav a{text-decoration:none;color:var(--muted)}nav a:hover{color:var(--text)}
.cart-link{text-decoration:none;font-weight:600}
.banner{background:var(--accent);color:var(--accent-text);text-align:center;padding:8px 16px;font-size:14px}
.demo{background:#c9a227;color:#1d1c19}
.hero{padding:48px 0 24px}.hero h1{font-size:clamp(28px,5vw,44px);margin:0 0 8px;letter-spacing:-.03em}.hero p{color:var(--muted);margin:0;font-size:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(220px,100%),1fr));gap:20px;padding:24px 0 48px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;text-decoration:none;display:flex;flex-direction:column}
.card img{aspect-ratio:1;object-fit:cover;width:100%;background:var(--line)}.card .b{padding:12px 14px 16px}
.card h3{font-size:15px;font-weight:600;margin:0 0 6px;line-height:1.35}
.price{font-weight:700}.was{color:var(--muted);text-decoration:line-through;font-weight:400;margin-left:6px;font-size:.9em}
.product{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:40px;padding:32px 0 56px}
@media (max-width:760px){.product{grid-template-columns:minmax(0,1fr);gap:20px}header nav{order:3;flex-basis:100%;padding-bottom:12px}.cart-link{margin-left:auto}}
.gallery img.main{border-radius:var(--radius);aspect-ratio:1;object-fit:cover;width:100%;background:var(--line)}
.thumbs{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}.thumbs img{width:64px;height:64px;object-fit:cover;border-radius:8px;cursor:pointer;border:1px solid var(--line)}
h1.t{font-size:clamp(24px,3.5vw,32px);margin:0 0 10px;letter-spacing:-.02em;line-height:1.2}
.big{font-size:24px}.muted{color:var(--muted)}
.btn{display:inline-block;background:var(--accent);color:var(--accent-text);border:0;border-radius:10px;padding:14px 22px;font-size:16px;font-weight:600;cursor:pointer;text-decoration:none}
.btn.wide{width:100%;text-align:center}.btn.ghost{background:transparent;color:var(--text);border:1px solid var(--line)}
select,input{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--text);max-width:100%}
label{display:block;font-size:14px;color:var(--muted);margin:12px 0 4px}
.perks{list-style:none;padding:0;margin:20px 0;display:grid;gap:8px}.perks li:before{content:"✓ ";color:var(--accent);font-weight:700}
.box{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px;margin:24px 0}
table{width:100%;border-collapse:collapse}td,th{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.tablewrap{overflow-x:auto}
footer{border-top:1px solid var(--line);padding:32px 0;color:var(--muted);font-size:14px;background:var(--surface)}
footer .cols{display:flex;gap:40px;flex-wrap:wrap;justify-content:space-between}footer a{margin-right:14px}
.flash{padding:12px 16px;border-radius:8px;background:var(--surface);border:1px solid var(--accent);margin:16px 0}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin:20px 0}
.kpis div{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px}.kpis b{display:block;font-size:24px}
.prose{max-width:720px;padding:32px 0 56px}
"""


def layout(cfg, title: str, body: str, *, cart_count: int = 0, description: str = "", collections: dict | None = None,
           head_extra: str = "", demo: bool = False, canonical: str = "") -> str:
    s = cfg.store
    nav = "".join(f'<a href="/c/{esc(slug)}">{esc(kw.title())}</a>' for slug, kw in list((collections or {}).items())[:5])
    full_title = f"{title} | {s.name}" if title else f"{s.name} — {s.tagline}"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(full_title)}</title><meta name="description" content="{esc(description or s.tagline)}">
{f'<link rel="canonical" href="{esc(canonical)}">' if canonical else ''}
<meta property="og:title" content="{esc(full_title)}"><meta property="og:site_name" content="{esc(s.name)}">
<style>{CSS}</style>{head_extra}</head><body>
{'<div class="banner demo">Demo store: no real payments or shipments.</div>' if demo else ''}
<div class="banner">Free tracked shipping on every order · {s.return_days}-day returns</div>
<header><div class="wrap"><a class="logo" href="/">{esc(s.name)}</a><nav><a href="/">Shop</a>{nav}<a href="/track">Track order</a></nav>
<a class="cart-link" href="/cart">Cart ({cart_count})</a></div></header>
<main class="wrap">{body}</main>
<footer><div class="wrap cols"><div><b>{esc(s.name)}</b><br>{esc(s.business_address)}<br><a href="mailto:{esc(s.contact_email)}">{esc(s.contact_email)}</a>
<p><a href="/pages/shipping">Shipping</a><a href="/pages/returns">Returns</a><a href="/pages/privacy">Privacy</a><a href="/pages/terms">Terms</a><a href="/pages/contact">Contact</a></p></div>
<form method="post" action="/subscribe"><label for="nl">New arrivals by email</label><input id="nl" type="email" name="email" required placeholder="you@example.com">
<button class="btn" style="padding:10px 16px">Subscribe</button></form></div></footer></body></html>"""


def product_card(p: dict) -> str:
    was = f'<span class="was">{money(p["compare_at"])}</span>' if p.get("compare_at") and p["compare_at"] > p["price"] else ""
    return (f'<a class="card" href="/p/{esc(p["slug"])}"><img loading="lazy" src="{esc(p["image"])}" alt="{esc(p["title"])}">'
            f'<div class="b"><h3>{esc(p["title"])}</h3><span class="price">{money(p["price"])}</span>{was}</div></a>')


def grid(products: list[dict]) -> str:
    if not products:
        return '<p class="muted" style="padding:40px 0">New products are on their way. Check back soon.</p>'
    return '<div class="grid">' + "".join(product_card(p) for p in products) + "</div>"


def home(cfg, products: list[dict]) -> str:
    return (f'<section class="hero"><h1>{esc(cfg.store.tagline)}</h1><p>Free tracked shipping · {cfg.store.return_days}-day returns · '
            f'Secure checkout</p></section>' + grid(products))


def collection(keyword: str, products: list[dict]) -> str:
    return f'<section class="hero"><h1>{esc(keyword.title())}</h1><p>{len(products)} products, all with free shipping</p></section>' + grid(products)


def product_page(cfg, p: dict, variants: list[dict], images: list[str], bullets: list[str], selected: int | None) -> str:
    v0 = next((v for v in variants if v["id"] == selected), variants[0])
    opts = "".join(f'<option value="{v["id"]}" data-price="{v["price"]:.2f}" data-was="{v["compare_at"]:.2f}" data-img="{esc(v["image"])}"'
                   f'{" selected" if v["id"] == v0["id"] else ""}>{esc(v["name"] or "Standard")} — {money(v["price"])}</option>' for v in variants)
    variant_field = (f'<label for="v">Option</label><select id="v" name="variant" onchange="pick(this)">{opts}</select>' if len(variants) > 1
                     else f'<input type="hidden" name="variant" value="{v0["id"]}">')
    was = f'<span class="was" id="was">{money(v0["compare_at"])}</span>' if v0["compare_at"] > v0["price"] else '<span class="was" id="was"></span>'
    thumbs = "".join(f'<img src="{esc(i)}" alt="" onclick="document.getElementById(\'main\').src=this.src">' for i in images[:8])
    paras = "".join(f"<p>{esc(par)}</p>" for par in p["description"].split("\n\n") if par.strip())
    return f"""<div class="product"><div class="gallery"><img id="main" class="main" src="{esc(v0['image'] or p['image'])}" alt="{esc(p['title'])}">
<div class="thumbs">{thumbs}</div></div>
<div><h1 class="t">{esc(p['title'])}</h1><div class="big"><span class="price" id="price">{money(v0['price'])}</span>{was}</div>
<form method="post" action="/cart/add">{variant_field}<label for="q">Quantity</label><input id="q" type="number" name="qty" value="1" min="1" max="10" style="width:90px">
<p><button class="btn wide">Add to cart</button></p></form>
<ul class="perks"><li>Free tracked shipping ({esc(cfg.store.shipping_days)})</li><li>{cfg.store.return_days}-day returns</li><li>Secure checkout with Stripe</li></ul>
{('<ul>' + ''.join(f'<li>{esc(b)}</li>' for b in bullets) + '</ul>') if bullets else ''}{paras}</div></div>
<script>function pick(s){{var o=s.options[s.selectedIndex];document.getElementById('price').textContent='$'+o.dataset.price;
var w=parseFloat(o.dataset.was);document.getElementById('was').textContent=w>parseFloat(o.dataset.price)?'$'+o.dataset.was:'';
if(o.dataset.img)document.getElementById('main').src=o.dataset.img;}}</script>"""


def product_jsonld(cfg, p: dict, variants: list[dict]) -> str:
    data = {
        "@context": "https://schema.org/", "@type": "Product", "name": p["title"], "image": [p["image"]],
        "description": p["seo_description"] or p["description"][:300], "sku": str(p["id"]),
        "offers": {"@type": "AggregateOffer", "priceCurrency": cfg.store.currency.upper(),
                   "lowPrice": f"{min(v['price'] for v in variants):.2f}", "highPrice": f"{max(v['price'] for v in variants):.2f}",
                   "offerCount": len(variants), "availability": "https://schema.org/InStock",
                   "url": f"{cfg.store.base_url}/p/{p['slug']}"},
    }
    payload = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script type="application/ld+json">{payload}</script>'


def cart_page(lines: list[dict], error: str = "") -> str:
    if not lines:
        return '<div class="prose"><h1>Your cart is empty</h1><p><a class="btn" href="/">Keep shopping</a></p></div>'
    rows = "".join(f"""<tr><td style="width:72px"><img src="{esc(l['image'])}" alt="" style="width:64px;height:64px;object-fit:cover;border-radius:8px"></td>
<td><a href="/p/{esc(l['slug'])}">{esc(l['title'])}</a><br><span class="muted">{money(l['price'])} each</span></td>
<td><form method="post" action="/cart/update" style="display:flex;gap:6px;flex-wrap:wrap"><input type="hidden" name="variant" value="{l['variant_id']}">
<input type="number" name="qty" value="{l['qty']}" min="0" max="10" style="width:72px"><button class="btn ghost" style="padding:8px 12px">Update</button></form></td>
<td align="right"><b>{money(l['price'] * l['qty'])}</b></td></tr>""" for l in lines)
    total = sum(l["price"] * l["qty"] for l in lines)
    err = f'<div class="flash">{esc(error)}</div>' if error else ""
    return f"""<div class="prose" style="max-width:860px"><h1>Your cart</h1>{err}<div class="tablewrap"><table>{rows}</table></div>
<div class="box"><div style="display:flex;justify-content:space-between"><span>Subtotal</span><b>{money(total)}</b></div>
<div style="display:flex;justify-content:space-between" class="muted"><span>Shipping</span><span>Free</span></div>
<form method="post" action="/checkout" style="margin-top:16px"><button class="btn wide">Checkout</button></form></div></div>"""


def message(title: str, body_html: str) -> str:
    return f'<div class="prose"><h1>{esc(title)}</h1>{body_html}</div>'


STATUS_TEXT = {
    "pending_payment": "Waiting for payment", "abandoned": "Not paid", "paid": "Paid, being prepared",
    "ordering": "Being prepared", "ordered": "Being prepared", "needs_review": "Being prepared",
    "shipped": "Shipped", "delivered": "Delivered", "cancelled": "Cancelled", "refunded": "Refunded",
}


def track_page(order: dict | None, items: list[dict], searched: bool, order_id: str = "", email: str = "") -> str:
    form = f"""<form method="get" action="/track" class="box"><label for="o">Order number</label><input id="o" name="order" value="{esc(order_id)}" required>
<label for="e">Email used at checkout</label><input id="e" type="email" name="email" value="{esc(email)}" required>
<p><button class="btn">Track</button></p></form>"""
    if not order:
        note = '<div class="flash">We couldn\'t find that order. Check the number and email from your confirmation.</div>' if searched else ""
        return message("Track your order", note + form)
    tracking = ""
    if order["tracking_number"]:
        url = order["tracking_url"] or f"https://t.17track.net/en#nums={quote(order['tracking_number'])}"
        tracking = f'<p>Tracking number: <b>{esc(order["tracking_number"])}</b> · <a href="{esc(url)}" rel="noopener">Track package</a></p>'
    rows = "".join(f"<li>{i['qty']} × {esc(i['title'])}</li>" for i in items)
    return message(f"Order {order['public_id']}", f'<p class="big">{esc(STATUS_TEXT.get(order["status"], order["status"]))}</p>{tracking}<ul>{rows}</ul>')


def demo_checkout(order: dict, lines: list[dict], countries: list[str]) -> str:
    total = sum(l["price"] * l["qty"] for l in lines)
    fields = "".join(f'<label for="{k}">{lbl}</label><input id="{k}" name="{k}" required style="width:100%">' for k, lbl in
                     [("name", "Full name"), ("email", "Email"), ("phone", "Phone"), ("line1", "Address"), ("city", "City"),
                      ("state", "State / province"), ("postal_code", "ZIP / postal code")])
    country = "".join(f'<option>{esc(c)}</option>' for c in countries)
    return message("Demo checkout", f"""<div class="flash">This is the demo checkout. In production, shoppers go to Stripe's hosted
checkout page instead; no card details are collected here.</div><form method="post" class="box"><input type="hidden" name="order" value="{esc(order['public_id'])}">
{fields}<label for="country">Country</label><select id="country" name="country">{country}</select>
<label style="display:flex;gap:8px;align-items:center;color:var(--text)"><input type="checkbox" name="consent" value="1"> Email me new products and offers</label>
<p><button class="btn wide">Pay {money(total)} (demo)</button></p></form>""")


def policy(cfg, name: str) -> tuple[str, str] | None:
    s = cfg.store
    pages = {
        "shipping": ("Shipping", f"<p>Every order ships free with tracking. Orders are processed within 1–3 business days and "
                                 f"delivery takes {esc(s.shipping_days)}. You'll get your tracking number by email as soon as your "
                                 f"order ships. We currently ship to: {esc(', '.join(s.ship_to_countries))}.</p>"),
        "returns": ("Returns & refunds", f"<p>If you're not happy, contact us within {s.return_days} days of delivery at "
                                         f"<a href='mailto:{esc(s.contact_email)}'>{esc(s.contact_email)}</a> with your order number. "
                                         f"Items that arrive damaged or wrong are refunded or replaced at no cost. For other returns we'll "
                                         f"send instructions; refunds go back to the original payment method within 5–10 days of approval.</p>"),
        "privacy": ("Privacy policy", "<p>We collect the details needed to fulfil your order (name, email, phone, shipping address) and "
                                      "share them only with our payment processor (Stripe), our fulfilment partner and the shipping carrier. "
                                      "We never see or store your card number. If you opt in, we email you new products; every email has an "
                                      f"unsubscribe link. To access or delete your data, email <a href='mailto:{esc(s.contact_email)}'>{esc(s.contact_email)}</a>.</p>"),
        "terms": ("Terms of service", f"<p>By ordering from {esc(s.name)} you agree to pay the price shown at checkout. Products ship from "
                                      "our fulfilment partners and may arrive in separate packages. We may cancel and fully refund any order "
                                      "we can't fulfil. Product images are representative.</p>"),
        "contact": ("Contact us", f"<p>Email <a href='mailto:{esc(s.contact_email)}'>{esc(s.contact_email)}</a>; we reply within one business day.</p>"
                                  f"<p>{esc(s.name)}<br>{esc(s.business_address)}</p>"),
    }
    return pages.get(name)


# --- admin ---------------------------------------------------------------------

def admin_page(cfg, stats: dict, orders: list[dict], products: list[dict], events: list[dict], token: str, flash: str = "") -> str:
    k = stats
    status = " · ".join(f"{esc(s)}: {n}" for s, n in sorted(k["by_status"].items()))

    def act(action: str, value: str, label: str) -> str:
        return (f'<form method="post" action="/admin/action" style="display:inline"><input type="hidden" name="token" value="{token}">'
                f'<input type="hidden" name="action" value="{action}"><input type="hidden" name="id" value="{esc(value)}">'
                f'<button class="btn ghost" style="padding:4px 10px;font-size:13px">{label}</button></form>')

    order_rows = "".join(
        f"<tr><td>{esc(o['public_id'])}</td><td><span class='pill'>{esc(o['status'])}</span><br><span class='muted'>{esc(o['last_error'])}</span></td>"
        f"<td>{esc(o['email'])}</td><td>{money(o['amount_paid'])}</td><td>{esc(o['supplier_order_id'])}<br>{esc(o['tracking_number'])}</td>"
        f"<td>{act('retry', o['public_id'], 'Retry') if o['status'] == 'needs_review' else ''}</td></tr>" for o in orders)
    product_rows = "".join(
        f"<tr><td><a href='/p/{esc(p['slug'])}'>{esc(p['title'])}</a><br><span class='muted'>{esc(p['keyword'])}</span></td>"
        f"<td>{money(p['price'] or 0)}</td><td>{p['score']:.1f}</td><td>{'live' if p['active'] else esc(p['inactive_reason'] or 'hidden')}</td>"
        f"<td>{act('hide' if p['active'] else 'show', str(p['id']), 'Hide' if p['active'] else 'Show')}</td></tr>" for p in products)
    event_rows = "".join(f"<tr><td class='muted' style='white-space:nowrap'>{esc(e['when'])}</td><td>{esc(e['kind'])}</td><td>{esc(e['message'])}</td></tr>" for e in events)
    buttons = " ".join(act(a, "", l) for a, l in [("research", "Find products now"), ("sync", "Sync prices/stock"),
                                                  ("fulfill", "Process orders"), ("track", "Update tracking")])
    return f"""<div style="padding:24px 0 56px"><h1>Dashboard</h1>{f'<div class="flash">{esc(flash)}</div>' if flash else ''}
<div class="kpis"><div>Revenue<b>{money(k['revenue'])}</b></div><div>Est. profit<b>{money(k['profit'])}</b></div><div>Orders<b>{k['orders']}</b></div>
<div>Live products<b>{k['products']}</b></div><div>Subscribers<b>{k['subscribers']}</b></div></div>
<p class="muted">{status}</p><p>{buttons}</p>
<p class="muted">Google Merchant Center feed: <code>{esc(cfg.store.base_url)}/feeds/google.xml</code> · Sitemap: <code>{esc(cfg.store.base_url)}/sitemap.xml</code></p>
<h2>Orders</h2><div class="tablewrap"><table><tr><th>Order</th><th>Status</th><th>Customer</th><th>Paid</th><th>Supplier / tracking</th><th></th></tr>{order_rows}</table></div>
<h2>Products</h2><div class="tablewrap"><table><tr><th>Product</th><th>From</th><th>Score</th><th>State</th><th></th></tr>{product_rows}</table></div>
<h2>Activity</h2><div class="tablewrap"><table>{event_rows}</table></div></div>"""
