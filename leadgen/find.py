"""Find local businesses with no website (or only a social page) from OpenStreetMap (ODbL, free).

OSM is incomplete: a business listed here with no website may still have one. The call script
covers that ("I couldn't find a site for you online...").
"""
from __future__ import annotations

import re
import time

import requests

UA = {"User-Agent": "leadgen/0.1 (local website outreach tool)"}
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
NOMINATIM = "https://nominatim.openstreetmap.org/search"

# friendly name -> Overpass tag filter
CATEGORIES = {
    "hair": '["shop"="hairdresser"]',
    "beauty": '["shop"="beauty"]',
    "barber": '["shop"="barber"]',
    "nails": '["shop"="beauty"]["beauty"="nails"]',
    "restaurant": '["amenity"="restaurant"]',
    "cafe": '["amenity"="cafe"]',
    "bakery": '["shop"="bakery"]',
    "auto": '["shop"="car_repair"]',
    "plumber": '["craft"="plumber"]',
    "electrician": '["craft"="electrician"]',
    "hvac": '["craft"="hvac"]',
    "landscaping": '["craft"="gardener"]',
    "cleaning": '["shop"="dry_cleaning"]',
    "fitness": '["leisure"="fitness_centre"]',
    "florist": '["shop"="florist"]',
    "tattoo": '["shop"="tattoo"]',
    "pets": '["shop"="pet_grooming"]',
    "tailor": '["craft"="tailor"]',
}
DEFAULT = ["hair", "beauty", "barber", "nails", "restaurant", "cafe", "bakery", "auto",
           "plumber", "electrician", "hvac", "landscaping", "florist", "tattoo", "pets"]
SOCIAL = re.compile(r"facebook\.com|instagram\.com|yelp\.com|linktr\.ee|tiktok\.com|google\.com|business\.site|square\.site", re.I)


def bbox(place: str) -> str:
    """'south,west,north,east' of the place's boundary (a rectangle search is far lighter than an area search)."""
    r = requests.get(NOMINATIM, params={"q": place, "format": "json", "limit": 1}, headers=UA, timeout=30)
    r.raise_for_status()
    hits = r.json()
    if not hits:
        raise SystemExit(f"couldn't find {place!r}; try 'City, ST'")
    s, n, w, e = hits[0]["boundingbox"]
    return f"{s},{w},{n},{e}"


def category_of(tags: dict) -> str:
    for name, flt in CATEGORIES.items():
        pairs = re.findall(r'\["([^"]+)"="([^"]+)"\]', flt)
        if all(tags.get(k) == v for k, v in pairs) and name != "beauty":
            return name
    return "beauty" if tags.get("shop") == "beauty" else "business"


def _query(q: str) -> list[dict]:
    for attempt in range(9):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=120)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json()["elements"]  # busy servers return an HTML error page with status 200
        except (requests.RequestException, ValueError):
            pass
        time.sleep(3 * (attempt + 1))
    raise SystemExit("OpenStreetMap servers busy or unreachable; try again in a few minutes")


def find(place: str, categories: list[str] | None = None) -> list[dict]:
    box = bbox(place)
    cats = categories or DEFAULT
    elements = []
    for c in cats:  # one category per query: big combined queries time out on the free servers
        elements += _query(f"[out:json][timeout:90];nwr{CATEGORIES[c]}[name]({box});out tags center;")
        time.sleep(1)
    leads = []
    seen = set()
    for el in elements:
        if el["id"] in seen:
            continue
        seen.add(el["id"])
        t = el.get("tags", {})
        if t.get("brand") or t.get("brand:wikidata") or t.get("disused:shop"):
            continue  # chains don't buy local websites
        site = t.get("website") or t.get("contact:website") or ""
        if site and not SOCIAL.search(site):
            continue  # already has a real website
        phone = t.get("phone") or t.get("contact:phone") or ""
        street = " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x)
        leads.append({
            "id": f"{el['type'][0]}{el['id']}",
            "name": t["name"],
            "category": category_of(t),
            "phone": phone,
            "email": t.get("email") or t.get("contact:email") or "",
            "address": street,
            "city": t.get("addr:city", ""),
            "social": site or t.get("contact:facebook") or t.get("contact:instagram") or "",
            "hours": t.get("opening_hours", ""),
            "lat": el.get("lat") or el.get("center", {}).get("lat", ""),
            "lon": el.get("lon") or el.get("center", {}).get("lon", ""),
        })
    return leads


def score(lead: dict) -> int:
    """Higher = better first call: reachable by phone, has an address, already cares about being online."""
    return 3 * bool(lead["phone"]) + 2 * bool(lead["social"]) + bool(lead["address"]) + bool(lead["hours"])
