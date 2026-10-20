"""Where leads come from: Google Places (best), OpenStreetMap (free), a CSV export (Outscraper, Apollo...), or demo data.

Each source returns plain dicts with the lead columns; the agency dedupes and stores them. Google Maps pages
are never scraped (against Google's terms); the Places API is the sanctioned way to get the same data."""
from __future__ import annotations

import csv
import hashlib
import logging
import random
import re
from urllib.parse import urlparse

import requests

from .config import Config
from .niches import Niche

log = logging.getLogger("clientbot")


class SourceError(RuntimeError):
    pass


def domain_of(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url if "://" in url else "http://" + url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def clean_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if len(digits) == 10 else (p or "").strip()


def lead(source: str, source_id: str, name: str, niche: str, city: str, **kw) -> dict:
    website = (kw.get("website") or "").strip()
    if website and "://" not in website:
        website = "https://" + website
    return {"source": source, "source_id": str(source_id), "name": name.strip(), "niche": niche, "city": city,
            "address": kw.get("address", "") or "", "phone": clean_phone(kw.get("phone", "")),
            "website": website, "domain": domain_of(website), "email": (kw.get("email") or "").strip().lower(),
            "contact_name": (kw.get("contact_name") or "").strip(), "rating": kw.get("rating"), "reviews": kw.get("reviews")}


class GooglePlaces:
    """Places API (New) Text Search. Up to 60 results per query (3 pages of 20). Billed per request by Google."""
    URL = "https://places.googleapis.com/v1/places:searchText"
    FIELDS = ("places.id,places.displayName,places.formattedAddress,places.websiteUri,places.nationalPhoneNumber,"
              "places.rating,places.userRatingCount,places.businessStatus,nextPageToken")
    name = "google"

    def __init__(self, cfg: Config, session: requests.Session | None = None):
        if not cfg.sources.google_api_key:
            raise SourceError("set GOOGLE_PLACES_API_KEY to search Google Places")
        self.cfg, self.http = cfg, session or requests.Session()

    def search(self, niche: Niche, city: str) -> list[dict]:
        out, token = [], None
        while len(out) < self.cfg.sources.per_search:
            body = {"textQuery": f"{niche.query} in {city}", "pageSize": 20}
            if token:
                body["pageToken"] = token
            r = self.http.post(self.URL, json=body, timeout=30, headers={
                "X-Goog-Api-Key": self.cfg.sources.google_api_key, "X-Goog-FieldMask": self.FIELDS})
            if not r.ok:
                raise SourceError(f"Google Places {r.status_code}: {r.text[:300]}")
            data = r.json()
            for p in data.get("places", []):
                if p.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
                    continue
                out.append(lead("google", p["id"], p.get("displayName", {}).get("text", ""), niche.key, city,
                                address=p.get("formattedAddress", ""), phone=p.get("nationalPhoneNumber", ""),
                                website=p.get("websiteUri", ""), rating=p.get("rating"), reviews=p.get("userRatingCount")))
            token = data.get("nextPageToken")
            if not token:
                break
        return out


class OpenStreetMap:
    """Free. Coverage is good for salons, clinics and dentists in most US cities, thin for trades, and there are
    no ratings or review counts (those leads score on their website alone)."""
    name = "osm"

    def __init__(self, cfg: Config, session: requests.Session | None = None):
        self.cfg, self.http = cfg, session or requests.Session()
        self.http.headers["User-Agent"] = f"clientbot/0.1 ({cfg.sources.contact or 'lead research'})"
        self._areas: dict[str, int] = {}

    def area_id(self, city: str) -> int:
        if city not in self._areas:
            r = self.http.get(self.cfg.sources.nominatim_url, params={"q": city, "format": "json", "limit": 5}, timeout=30)
            r.raise_for_status()
            for place in r.json():
                if place.get("osm_type") == "relation":
                    self._areas[city] = 3600000000 + int(place["osm_id"])
                    break
            else:
                raise SourceError(f"OpenStreetMap doesn't know the city {city!r}; try 'City, ST'")
        return self._areas[city]

    def search(self, niche: Niche, city: str) -> list[dict]:
        if not niche.osm:
            raise SourceError(f"no OpenStreetMap tags for niche {niche.key!r}; use Google or a CSV")
        parts = "".join(f'nwr["{k}"="{v}"](area.a);' for k, v in niche.osm)
        query = f"[out:json][timeout:90];area(id:{self.area_id(city)})->.a;({parts});out center tags;"
        r = self.http.post(self.cfg.sources.overpass_url, data={"data": query}, timeout=120)
        if not r.ok:
            raise SourceError(f"Overpass {r.status_code}: {r.text[:300]}")
        out = []
        for el in r.json().get("elements", []):
            t = el.get("tags", {})
            name = t.get("name", "")
            if not name or (niche.osm_strict and not any(k in name.lower() for k in niche.keywords)):
                continue
            addr = " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street"), t.get("addr:city")) if x)
            out.append(lead("osm", f"{el['type']}/{el['id']}", name, niche.key, city, address=addr,
                            phone=t.get("phone") or t.get("contact:phone", ""),
                            website=t.get("website") or t.get("contact:website") or t.get("url", ""),
                            email=t.get("email") or t.get("contact:email", "")))
        return out[: self.cfg.sources.per_search * 3]


# CSV headers from Outscraper, Apollo, Apify and hand-made sheets, mapped to lead columns
HEADERS = {
    "name": ["name", "business name", "company", "company name", "title", "account name"],
    "website": ["website", "site", "url", "domain", "company website"],
    "phone": ["phone", "phone number", "phone_1", "company phone", "telephone"],
    "email": ["email", "email_1", "email address", "work email", "contact email"],
    "address": ["address", "full_address", "street", "formatted address"],
    "city": ["city", "location"],
    "rating": ["rating", "stars", "google rating"],
    "reviews": ["reviews", "reviews count", "review count", "user ratings total", "reviewscount"],
    "contact_name": ["contact name", "first name", "owner", "full name", "person name"],
    "niche": ["niche", "category", "type", "industry"],
}


def read_csv(path: str, niche: str = "", city: str = "") -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    cols = {h.lower().strip(): h for h in rows[0].keys()}
    pick = {field: next((cols[a] for a in aliases if a in cols), None) for field, aliases in HEADERS.items()}
    if not pick["name"]:
        raise SourceError(f"{path}: no business name column (expected one of {HEADERS['name']})")
    out = []
    for row in rows:
        get = lambda f: (row.get(pick[f]) or "").strip() if pick[f] else ""
        if not get("name"):
            continue
        num = lambda f, typ: typ(re.sub(r"[^\d.]", "", get(f))) if re.search(r"\d", get(f)) else None
        sid = hashlib.sha1(f"{get('name')}|{get('website')}|{get('phone')}".lower().encode()).hexdigest()[:16]
        out.append(lead("csv", sid, get("name"), (niche or get("niche") or "local business").lower(), city or get("city"),
                        address=get("address"), phone=get("phone"), website=get("website"), email=get("email"),
                        contact_name=get("contact_name"), rating=num("rating", float), reviews=num("reviews", int)))
    return out


class Demo:
    """Fake businesses on .example domains, with fake websites served by demo_fetch (no network)."""
    name = "demo"

    def __init__(self, cfg: Config | None = None, seed: int = 7):
        self.rng = random.Random(seed)

    def search(self, niche: Niche, city: str) -> list[dict]:
        firsts = ["Glow", "Radiant", "Summit", "Oak", "Bluebonnet", "Lone Star", "Capitol", "Riverside", "Hill Country", "Cedar"]
        out = []
        for i, first in enumerate(firsts[:6]):
            name = f"{first} {niche.key.title()}"
            slug = re.sub(r"[^a-z0-9]+", "", name.lower())
            out.append(lead("demo", f"{niche.key}-{city}-{i}", name, niche.key, city, address=f"{100 + i} Main St, {city}",
                            phone=f"512{self.rng.randint(200, 999)}{self.rng.randint(1000, 9999)}", website=f"https://{slug}.example",
                            rating=round(self.rng.uniform(3.8, 4.9), 1), reviews=self.rng.choice([8, 45, 120, 260, 610, 2400])))
        return out


DEMO_SITES = {
    0: '<title>{name}</title><meta name="viewport" content="width=device-width"><p>Call us to book: {phone}</p>'
       '<a href="mailto:info@{domain}">Email us</a><form></form><p>Mon-Fri 9-6. Family owned since 2009 by Dr. Maria Lopez.</p>',
    1: '<title>{name}</title><p>Book online</p><script src="https://www.vagaro.com/widget.js"></script>'
       '<a href="mailto:hello@{domain}">hello@{domain}</a>',
    2: '<title>{name}</title><p>Text us!</p><script src="https://widget.podium.com/podium-widget.js"></script>'
       '<p>office@{domain}</p>',
    3: '<title>{name}</title><p>Welcome. Call {phone} for appointments.</p>',
    4: '<title>{name}</title><meta name="viewport" content="width=device-width"><p>Call to schedule.</p>'
       '<a href="mailto:frontdesk@{domain}">contact</a>',
    5: '<title>{name}</title><p>Call us.</p><a href="mailto:contact@{domain}">contact</a>',
}


def demo_fetch(url: str) -> tuple[int, str, float]:
    """Stand-in for an HTTP GET of a demo business website: (status, html, seconds)."""
    domain = domain_of(url)
    m = re.match(r"https?://([^/]+)(/.*)?", url)
    if not domain.endswith(".example") or (m and m.group(2) not in (None, "", "/")):
        return 404, "", 0.1
    i = int(hashlib.sha1(domain.encode()).hexdigest(), 16) % len(DEMO_SITES)
    name = domain.split(".")[0].title()
    return 200, DEMO_SITES[i].format(name=name, domain=domain, phone="(512) 555-0100"), 0.4


def provider(cfg: Config, name: str | None = None):
    name = name or cfg.sources.provider
    if name == "auto":
        name = "google" if cfg.sources.google_api_key else "osm"
    return {"google": GooglePlaces, "osm": OpenStreetMap, "demo": Demo}[name](cfg)
