"""What the bot knows about each kind of local business: where to find them and what a booked job is worth.

`ticket` is a rough revenue per booked appointment/job in the US. It only feeds the ROI wording in emails and
proposals ("pays for itself at N extra bookings"), so adjust it to your city in config ([niche_overrides])."""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass
class Niche:
    key: str
    label: str              # plural, used in copy: "med spas"
    query: str              # Google Places text search
    osm: list               # OpenStreetMap tags [(key, value)]
    ticket: int             # $ per booked appointment / job
    unit: str               # "appointment", "job"
    pain: str               # the specific way missed calls hurt this business
    price: int = 0          # monthly price for this niche; 0 = [offer] monthly_price
    keywords: list = field(default_factory=list)   # words that confirm the niche in a business name/site
    osm_strict: bool = False  # OSM tag is broad (shop=beauty covers nail bars too): require a keyword in the name


NICHES = {n.key: n for n in [
    Niche("med spa", "med spas", "med spa", [("shop", "beauty"), ("healthcare", "clinic")], 400, "treatment",
          "consult requests come in at night and on weekends, and the first spa to answer gets the booking",
          keywords=["med spa", "medspa", "medical spa", "aesthetic", "botox", "injectable", "laser"], osm_strict=True),
    Niche("dental", "dental practices", "dentist", [("amenity", "dentist"), ("healthcare", "dentist")], 350, "new-patient visit",
          "the front desk is with patients, so new-patient calls go to voicemail and book with the next practice",
          keywords=["dental", "dentist", "dentistry", "orthodont"]),
    Niche("chiropractor", "chiropractors", "chiropractor", [("healthcare", "chiropractor")], 250, "new-patient visit",
          "you're adjusting patients when the phone rings, and new patients rarely leave a voicemail",
          keywords=["chiropract"]),
    Niche("hvac", "HVAC companies", "hvac contractor", [("craft", "hvac"), ("craft", "heating_engineer")], 450, "job",
          "when it's 95 degrees, the homeowner calls three companies and books whoever answers first",
          keywords=["hvac", "heating", "cooling", "air condition", "furnace"]),
    Niche("plumber", "plumbing companies", "plumber", [("craft", "plumber")], 350, "job",
          "a burst pipe can't wait for a callback; the caller hangs up and dials the next plumber",
          keywords=["plumb", "drain", "water heater"]),
    Niche("electrician", "electricians", "electrician", [("craft", "electrician")], 300, "job",
          "you're on a job site with your hands full, and the caller books whoever picks up",
          keywords=["electric"]),
    Niche("roofer", "roofing companies", "roofing contractor", [("craft", "roofer")], 900, "inspection",
          "storm-season leads go cold within hours if nobody responds", keywords=["roof"]),
    Niche("hair salon", "salons", "hair salon", [("shop", "hairdresser")], 85, "appointment",
          "stylists can't answer mid-cut, so callers who wanted to book today go elsewhere", price=1500,
          keywords=["salon", "hair", "stylist", "barber", "blowout"]),
    Niche("beauty salon", "beauty studios", "nail salon OR lash studio OR beauty salon", [("shop", "beauty")], 70, "appointment",
          "techs are mid-service when the phone rings, and after-hours booking requests go unanswered", price=1500,
          keywords=["nail", "lash", "brow", "wax", "beauty", "spa"]),
]}


def get(key: str, overrides: dict | None = None) -> Niche:
    base = NICHES.get(key.lower())
    if base is None:
        base = Niche(key.lower(), key + " businesses", key, [], 200, "appointment",
                     "calls that go to voicemail usually book with a competitor")
    return replace(base, **(overrides or {}).get(base.key, {}))
