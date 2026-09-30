import csv

import pytest

from leadgen import daily, find, preview, tracker

YOU = {"name": "Sam", "business": "Sam Web Co", "phone": "(478) 555-0100", "email": "s@x.com",
       "mailing_address": "1 Main St, Macon, GA"}
OFFER = {"setup_price": 500, "monthly_price": 39, "deposit_percent": 50, "preview_base_url": "https://x.github.io/sites",
         "daily_calls": 20}
ELEMENTS = [
    {"type": "node", "id": 1, "lat": 32.8, "lon": -83.6, "tags": {"name": "Joe's Barbers", "shop": "barber",
     "phone": "+1-478-555-0001", "addr:housenumber": "5", "addr:street": "Oak St", "opening_hours": "Mo-Fr 09:00-18:00"}},
    {"type": "node", "id": 2, "lat": 32.8, "lon": -83.6, "tags": {"name": "Has Site Salon", "shop": "hairdresser",
     "website": "https://hassite.com", "phone": "1"}},
    {"type": "node", "id": 3, "lat": 32.8, "lon": -83.6, "tags": {"name": "Chain Cuts", "shop": "hairdresser",
     "brand": "Chain"}},
    {"type": "way", "id": 4, "center": {"lat": 32.9, "lon": -83.7}, "tags": {"name": "FB Nails", "shop": "beauty",
     "beauty": "nails", "website": "https://facebook.com/fbnails", "email": "fb@nails.com"}},
]


@pytest.fixture
def leads(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(find, "bbox", lambda p: "0,0,1,1")
    monkeypatch.setattr(find, "_query", lambda q: ELEMENTS)
    monkeypatch.setattr(find.time, "sleep", lambda s: None)
    return find.find("Macon, GA", ["barber"])


def test_filters_real_websites_and_chains_keeps_social_only(leads):
    names = {l["name"] for l in leads}
    assert names == {"Joe's Barbers", "FB Nails"}
    fb = next(l for l in leads if l["name"] == "FB Nails")
    assert fb["category"] == "nails" and fb["lat"] == 32.9 and "facebook" in fb["social"]


def test_merge_keeps_notes_and_mark_sets_followup(leads):
    assert tracker.merge(leads, "Macon") == 2
    r = tracker.mark("n1", "called", "left msg with staff")
    assert r["next_followup"] and "left msg" in r["notes"]
    assert tracker.merge(leads, "Macon") == 0  # re-running find never duplicates or resets
    assert next(x for x in tracker.load() if x["id"] == "n1")["status"] == "called"


def test_preview_has_banner_and_noindex_final_does_not(leads):
    lead = {**next(l for l in leads if l["id"] == "n1"), "score": 0}
    p = preview.render(lead, YOU)
    assert "Free preview made for Joe&#x27;s Barbers" in p and "noindex" in p and "tel:+14785550001" in p
    assert "Mon – Fri 9am – 6pm" in p and "(478) 555-0001" in p
    f = preview.render(lead, YOU, final=True)
    assert "Free preview" not in f and "noindex" not in f


def test_today_lists_new_leads_and_email_is_compliant(leads):
    tracker.merge(leads, "Macon")
    text = daily.write({"you": YOU, "offer": OFFER})
    assert "Joe's Barbers" in text and "https://x.github.io/sites/joe-s-barbers-" in text
    mail = daily.email_draft(next(x for x in tracker.load() if x["id"] == "w4"), YOU, OFFER)
    assert YOU["mailing_address"] in mail and "won't email again" in mail


def test_do_not_contact_never_listed_again(leads):
    tracker.merge(leads, "Macon")
    tracker.mark("n1", "do_not_contact")
    assert "Joe's Barbers" not in daily.write({"you": YOU, "offer": OFFER})
