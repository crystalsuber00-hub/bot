"""One-page website for a lead. Preview mode adds a clear 'free preview' banner and noindex;
final mode (after they pay) removes both."""
from __future__ import annotations

import html
import re
from pathlib import Path

SITE = Path("site")

THEMES = {  # accent, dark, headline, three services
    "hair": ("#b5838d", "#2d2327", "Great hair, close to home", ["Cuts & styling", "Color & highlights", "Treatments"]),
    "barber": ("#c9a227", "#1c1f24", "Sharp cuts. No fuss.", ["Haircuts", "Beard trims", "Hot towel shaves"]),
    "beauty": ("#d4a5a5", "#2b2326", "Look and feel your best", ["Facials", "Waxing", "Lashes & brows"]),
    "nails": ("#e07a9b", "#2a2025", "Beautiful nails, every visit", ["Manicures", "Pedicures", "Nail art"]),
    "restaurant": ("#d9480f", "#221a16", "Good food, made fresh", ["Dine in", "Takeout", "Catering"]),
    "cafe": ("#a0703c", "#241c16", "Your neighborhood coffee spot", ["Coffee & espresso", "Pastries", "Space to sit and stay"]),
    "bakery": ("#c77d3a", "#261d15", "Baked fresh every morning", ["Breads", "Cakes & custom orders", "Pastries"]),
    "auto": ("#1971c2", "#141a21", "Honest auto repair", ["Diagnostics", "Brakes & tires", "Oil changes & maintenance"]),
    "plumber": ("#1c7ed6", "#131a22", "Reliable plumbing, fast", ["Leaks & repairs", "Drains", "Water heaters"]),
    "electrician": ("#f08c00", "#1d1a14", "Safe, licensed electrical work", ["Repairs", "Panels & wiring", "Lighting"]),
    "hvac": ("#1098ad", "#121c1f", "Comfort all year round", ["AC repair", "Heating", "Maintenance plans"]),
    "landscaping": ("#2f9e44", "#141d16", "Yards you'll love coming home to", ["Lawn care", "Landscaping", "Cleanups"]),
    "florist": ("#c2255c", "#241419", "Flowers for every occasion", ["Arrangements", "Weddings & events", "Same-day delivery"]),
    "tattoo": ("#e03131", "#141414", "Custom work, done right", ["Custom designs", "Cover-ups", "Walk-ins"]),
    "pets": ("#7048e8", "#1a1628", "Happy, clean pets", ["Baths & grooming", "Nail trims", "De-shedding"]),
}
DEFAULT_THEME = ("#495057", "#1b1d20", "Serving our neighbors", ["Quality service", "Friendly staff", "Fair prices"])
DAYS = {"Mo": "Mon", "Tu": "Tue", "We": "Wed", "Th": "Thu", "Fr": "Fri", "Sa": "Sat", "Su": "Sun"}


def slug(name: str, lead_id: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return f"{s}-{lead_id[-4:]}"


def pretty_phone(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else (raw or "")


def _12h(m: re.Match) -> str:
    h, mi = int(m.group(1)), m.group(2)
    suffix = "am" if h < 12 or h == 24 else "pm"
    h = h % 12 or 12
    return f"{h}{'' if mi == '00' else ':' + mi}{suffix}"


def _hours(raw: str) -> str:
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    for k, v in DAYS.items():
        parts = [re.sub(rf"\b{k}\b", v, p) for p in parts]
    parts = [re.sub(r"\b(\d{1,2}):(\d{2})\b", _12h, p).replace("-", " – ").replace(",", ", ") for p in parts]
    return "".join(f"<li>{html.escape(p)}</li>" for p in parts)


def render(lead: dict, you: dict, final: bool = False) -> str:
    e = lambda s: html.escape(str(s or ""))
    accent, dark, headline, services = THEMES.get(lead["category"], DEFAULT_THEME)
    name = e(lead["name"])
    addr = ", ".join(x for x in (lead.get("address"), lead.get("city")) if x)
    tel = re.sub(r"[^\d+]", "", lead.get("phone") or "")
    shown = e(pretty_phone(lead.get("phone") or ""))
    call = f'<a class="btn" href="tel:{tel}">Call {shown}</a>' if tel else ""
    maps = (f'<a class="btn ghost" href="https://www.openstreetmap.org/?mlat={lead["lat"]}&mlon={lead["lon"]}#map=18/'
            f'{lead["lat"]}/{lead["lon"]}">Get directions</a>') if lead.get("lat") else ""
    hours = _hours(lead.get("hours", ""))
    your_tel = re.sub(r"[^\d+]", "", you["phone"])
    banner = "" if final else (
        f'<div class="banner">Free preview made for {name} by {e(you["business"])}. Not live yet. '
        f'Want it? Call or text {e(you["name"])} at <a href="tel:{your_tel}">{e(you["phone"])}</a>.</div>')
    robots = "" if final else '<meta name="robots" content="noindex,nofollow">'
    svc = "".join(f"<div class='card'><h3>{e(s)}</h3><p>Ask us about {e(s.lower())}.</p></div>" for s in services)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{robots}
<title>{name}</title>
<style>
:root{{--a:{accent};--d:{dark}}}
*{{box-sizing:border-box}}body{{margin:0;font:17px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#222;background:#fff}}
.banner{{background:#fff3bf;color:#5c3c00;padding:10px 16px;text-align:center;font-size:15px}}
.banner a{{color:inherit;font-weight:600}}
header{{background:var(--d);color:#fff;padding:72px 20px 64px;text-align:center}}
header h1{{margin:0 0 8px;font-size:clamp(32px,6vw,52px);line-height:1.1}}
header p{{margin:0 0 28px;font-size:20px;opacity:.85}}
.btn{{display:inline-block;margin:6px;padding:14px 26px;border-radius:999px;background:var(--a);color:#fff;text-decoration:none;font-weight:600}}
.btn.ghost{{background:transparent;border:2px solid #fff}}
main{{max-width:960px;margin:0 auto;padding:48px 20px}}
h2{{font-size:28px;margin:0 0 20px}}
.grid{{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}}
.card{{border:1px solid #e9ecef;border-radius:14px;padding:22px;border-top:4px solid var(--a)}}
.card h3{{margin:0 0 6px}}.card p{{margin:0;color:#555}}
section{{margin-bottom:48px}}ul{{padding-left:20px}}
footer{{background:#f8f9fa;text-align:center;padding:28px 20px;color:#666;font-size:15px}}
</style></head><body>
{banner}
<header><h1>{name}</h1><p>{e(headline)}</p>{call}{maps}</header>
<main>
<section><h2>What we do</h2><div class="grid">{svc}</div></section>
<section><h2>Visit us</h2><p>{e(addr) or "Contact us for our location."}</p>{f"<ul>{hours}</ul>" if hours else ""}</section>
<section><h2>Get in touch</h2><p>{f'Call us at <a href="tel:{tel}">{shown}</a>.' if tel else "Stop by and say hello."}
{f' Follow us <a href="{e(lead["social"])}">online</a>.' if lead.get("social") else ""}</p></section>
</main>
<footer>&copy; {name}</footer>
</body></html>"""


def build(lead: dict, you: dict, final: bool = False) -> Path:
    out = SITE / slug(lead["name"], lead["id"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render(lead, you, final))
    return out
