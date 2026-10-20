"""Looks at a business's website the way a prospect would: can I book online? Can I text or chat? Who do I email?
The gaps it finds become the opening line of the cold email and the score."""
from __future__ import annotations

import html as htmllib
import logging
import re
import time
from typing import Callable
from urllib.parse import urljoin

import requests

from .niches import Niche
from .sources import domain_of

log = logging.getLogger("clientbot")

Fetch = Callable[[str], tuple]   # url -> (status, html, seconds)

# self-serve booking: the business already lets customers book without calling
BOOKING = {
    "vagaro": "Vagaro", "booksy": "Booksy", "fresha": "Fresha", "glossgenius": "GlossGenius", "schedulicity": "Schedulicity",
    "joinblvd": "Boulevard", "boulevard.io": "Boulevard", "mindbodyonline": "Mindbody", "mindbody.io": "Mindbody",
    "acuityscheduling": "Acuity", "calendly.com": "Calendly", "setmore": "Setmore", "squareup.com/appointments": "Square",
    "square.site": "Square", "book.squareup": "Square", "janeapp": "Jane", "zocdoc": "Zocdoc", "nexhealth": "NexHealth",
    "localmed": "LocalMed", "opencare": "Opencare", "housecallpro": "Housecall Pro", "getjobber": "Jobber",
    "clienthub.getjobber": "Jobber", "servicetitan": "ServiceTitan", "scheduleengine": "Schedule Engine",
    "simplybook": "SimplyBook", "zenoti": "Zenoti", "phorest": "Phorest", "mangomint": "Mangomint", "aestheticspro": "AestheticsPro",
    "cal.com": "Cal.com", "youcanbook.me": "YouCanBookMe", "chiropracticscheduling": "ChiroTouch", "chirotouch": "ChiroTouch",
}
# website chat / text-us widgets: the "speed to lead" problem is at least partly solved
CHAT = {
    "podium": "Podium", "birdeye": "Birdeye", "weave": "Weave", "intercom": "Intercom", "drift.com": "Drift",
    "tidio": "Tidio", "livechatinc": "LiveChat", "tawk.to": "tawk.to", "hubspot.com/conversations": "HubSpot chat",
    "leadconnectorhq": "GoHighLevel", "msgsndr": "GoHighLevel", "smith.ai": "Smith.ai", "ruby.com": "Ruby",
    "nicejob": "NiceJob", "signpost": "Signpost", "chatbase": "Chatbase", "zendesk": "Zendesk", "crisp.chat": "Crisp",
    "olark": "Olark", "manychat": "ManyChat", "gorgias": "Gorgias", "callrail": "CallRail",
}
BUILDERS = {"wix.com": "Wix", "squarespace": "Squarespace", "wp-content": "WordPress", "godaddy": "GoDaddy",
            "weebly": "Weebly", "shopify": "Shopify", "duda": "Duda", "webflow": "Webflow"}
CALL_TO_BOOK = re.compile(r"\b(call (us )?(today )?(to|for) (book|schedule|an appointment|make an appointment)|"
                          r"call to (book|schedule)|appointments? by phone|by appointment only)\b", re.I)
EMAIL = re.compile(r"[a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9.-]+\.[a-z]{2,24}", re.I)
JUNK_EMAIL = re.compile(r"(\.(png|jpe?g|gif|webp|svg)$|sentry|wixpress|example\.(com|org)|domain\.com|yourdomain|"
                        r"email\.com$|godaddy|@2x|u00|noreply|no-reply|donotreply|privacy@|abuse@|webmaster@)", re.I)
FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "live.com", "msn.com", "me.com"}
PREFERRED = ["owner", "info", "hello", "contact", "office", "frontdesk", "appointments", "booking", "admin", "team", "support"]
CONTACT_PATHS = ["/contact", "/contact-us", "/about"]


def http_fetch(url: str) -> tuple[int, str, float]:
    t = time.monotonic()
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (compatible; clientbot site check)"})
        return r.status_code, r.text[:600_000], time.monotonic() - t
    except requests.RequestException as e:
        log.debug("fetch %s: %s", url, e)
        return 0, "", time.monotonic() - t


def visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"(?s)<[^>]+>", " ", html))).strip()


def find(table: dict, html: str) -> list[str]:
    low = html.lower()
    return sorted({name for sig, name in table.items() if sig in low})


def pick_email(emails: set[str], domain: str) -> str:
    good = [e for e in emails if not JUNK_EMAIL.search(e)]
    if not good:
        return ""
    def rank(e: str):
        local, host = e.split("@", 1)
        own = host == domain or host.endswith("." + domain)
        return (not own, host not in FREEMAIL and not own,
                PREFERRED.index(local) if local in PREFERRED else len(PREFERRED), len(e))
    return sorted(good, key=rank)[0]


def audit(website: str, fetch: Fetch = http_fetch) -> dict:
    """Everything the pitch and the score need from the site. Never raises; a dead site is itself a finding."""
    if not website:
        return {"ok": False, "reason": "no website"}
    status, html, secs = fetch(website)
    if status >= 400 or not html:
        return {"ok": False, "reason": f"site didn't load ({status or 'timeout'})", "load_seconds": round(secs, 1)}
    pages, emails = [html], set()
    for page in pages:
        emails |= {m.lower() for m in re.findall(r"mailto:([^\"'?>\s]+)", page, re.I)} | {m.lower() for m in EMAIL.findall(page)}
    if not emails:
        for path in CONTACT_PATHS:
            s, h, _ = fetch(urljoin(website, path))
            if s == 200 and h:
                pages.append(h)
                emails |= {m.lower() for m in EMAIL.findall(h)}
                if emails:
                    break
    everything = "\n".join(pages)
    text = visible_text(html)
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return {
        "ok": True,
        "title": visible_text(title.group(1))[:120] if title else "",
        "email": pick_email(emails, domain_of(website)),
        "emails": sorted(emails)[:10],
        "booking": find(BOOKING, everything),
        "chat": find(CHAT, everything),
        "builder": (find(BUILDERS, html) or [""])[0],
        "call_to_book": bool(CALL_TO_BOOK.search(text)),
        "has_form": "<form" in everything.lower(),
        "mobile": "name=\"viewport\"" in html.lower() or "name='viewport'" in html.lower(),
        "https": website.startswith("https://"),
        "load_seconds": round(secs, 1),
        "text": text[:2500],      # for personalising the email (treated as untrusted data)
    }


def gaps(a: dict) -> list[str]:
    """Plain-English observations, strongest first. Only things the audit actually saw."""
    if not a.get("ok"):
        return [a.get("reason", "no website")]
    out = []
    if not a["booking"]:
        out.append("there's no way to book online; new customers have to call" if a["call_to_book"] or not a["has_form"]
                   else "booking goes through a contact form, so someone has to reply before anything is booked")
    if not a["chat"]:
        out.append("nobody answers website visitors after hours (no chat or text-us option)")
    if a["load_seconds"] and a["load_seconds"] > 5:
        out.append(f"the homepage took {a['load_seconds']:.0f} seconds to load")
    if not a["mobile"]:
        out.append("the site isn't set up for phones")
    return out


def score(lead: dict, a: dict, niche: Niche, min_reviews: int, max_reviews: int, min_rating: float,
          exclude_words: list) -> tuple[int, list[str]]:
    """0-100: how likely this business needs the service and can pay for it, and whether we can reach them."""
    pts, why = 0, []
    def add(n, reason):
        nonlocal pts
        pts += n
        why.append(f"{n:+d} {reason}")
    name = lead["name"].lower()
    if any(w in name for w in exclude_words):
        add(-60, "chain/franchise (decisions made at head office)")
    if lead.get("email") or a.get("email"):
        add(25, "email found")
    elif lead.get("phone"):
        add(5, "phone only (call list)")
    if not a.get("ok"):
        add(5 if a.get("reason") == "no website" else 0, a.get("reason", "no website"))
    else:
        add(10, "has a working website")
        if not a["booking"]:
            add(20, "no online booking")
        if not a["chat"]:
            add(10, "no chat/text widget")
        else:
            add(-15, f"already uses {', '.join(a['chat'])}")
    reviews, rating = lead.get("reviews"), lead.get("rating")
    if reviews is not None:
        if reviews < min_reviews:
            add(-15, f"only {reviews} reviews (probably too small)")
        elif reviews > max_reviews:
            add(-20, f"{reviews} reviews (likely a chain or has a call center)")
        else:
            add(15 if reviews >= 50 else 8, f"{reviews} reviews (busy, independent)")
    if rating is not None and rating < min_rating:
        add(-10, f"rating {rating} (unhappy customers; harder sell)")
    elif rating is not None and rating >= 4.3:
        add(5, f"rating {rating} (cares about customers)")
    if niche.ticket >= 300:
        add(15, f"high-value {niche.unit}s (~${niche.ticket})")
    elif niche.ticket >= 150:
        add(8, f"mid-value {niche.unit}s (~${niche.ticket})")
    return max(0, min(100, pts)), why
