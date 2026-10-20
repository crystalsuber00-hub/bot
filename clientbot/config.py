from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path


@dataclass
class Agency:
    name: str = "Front Desk AI"
    owner_name: str = "Your Name"
    owner_title: str = "Founder"
    phone: str = ""
    website: str = ""
    calendar_url: str = ""          # Cal.com / Calendly link for a 15-minute call (used in follow-ups and replies)
    business_address: str = ""      # required in every cold email (CAN-SPAM)
    timezone: str = "America/New_York"


@dataclass
class Offer:
    monthly_price: int = 2500       # per niche override in niches.py (salons default to 1500)
    setup_fee: int = 0
    guarantee: str = ("If it hasn't recovered at least {breakeven} {unit}s in the first 30 days, "
                      "you don't pay for month two.")
    target_clients: int = 4
    mrr_goal: int = 10000


@dataclass
class Targeting:
    niches: list = field(default_factory=lambda: ["med spa", "dental", "hvac", "plumber"])
    cities: list = field(default_factory=lambda: ["Austin, TX"])
    min_reviews: int = 15           # fewer = too small to pay $2.5k/mo
    max_reviews: int = 1500         # more = chain or call center, already solved
    min_rating: float = 3.5
    exclude_words: list = field(default_factory=lambda: [
        "corporate", "headquarters", "franchise", "walmart", "target", "costco", "great clips", "supercuts",
        "sport clips", "fantastic sams", "aspen dental", "western dental", "roto-rooter", "mr. rooter", "one hour heating",
        "aire serv", "mister sparky", "benjamin franklin plumbing", "ulta", "massage envy", "european wax", "hand & stone"])
    min_score: int = 50             # leads below this aren't emailed (still in the call list if they have a phone)


@dataclass
class Sources:
    provider: str = "auto"          # "google" (best data, needs GOOGLE_PLACES_API_KEY), "osm" (free, patchier), "auto"
    google_api_key: str = ""
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    per_search: int = 60            # Google returns at most 60 per query
    contact: str = ""               # your email, sent in the User-Agent (OpenStreetMap usage policy)


@dataclass
class Mailbox:
    address: str = ""
    smtp_host: str = ""             # empty = write emails to outbox/ (dry run)
    smtp_port: int = 587
    smtp_user: str = ""
    password_env: str = ""          # name of the environment variable holding this mailbox's (app) password
    imap_host: str = ""             # for reply/bounce detection
    imap_port: int = 993
    daily_limit: int = 40           # after warm-up; stay at or under ~40-50 per mailbox
    ramp_start: int = 10            # first day's limit; grows by ramp_per_day until daily_limit
    ramp_per_day: int = 5

    @property
    def password(self) -> str:
        return os.environ.get(self.password_env, "") if self.password_env else ""


@dataclass
class Outreach:
    auto_send: bool = False         # false: drafts wait for `clientbot approve`; true: the worker sends what it writes
    send_weekdays: list = field(default_factory=lambda: [0, 1, 2, 3, 4])   # 0 = Monday
    send_start_hour: int = 8        # agency timezone
    send_end_hour: int = 16
    min_gap_seconds: int = 150      # between two sends from the same mailbox
    followup_days: list = field(default_factory=lambda: [3, 4, 7])        # wait after email 1, 2, 3
    outbox_dir: str = "outbox"
    leads_buffer: int = 150         # find more businesses when fewer than this are waiting to be emailed


@dataclass
class Copywriting:
    enabled: bool = True            # Claude when ANTHROPIC_API_KEY is set, templates otherwise
    model: str = "claude-opus-5-5"


@dataclass
class Notify:
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"
    owner_email: str = ""           # daily digest + reply alerts


@dataclass
class Config:
    db_path: str = "data/clients.db"
    agency: Agency = field(default_factory=Agency)
    offer: Offer = field(default_factory=Offer)
    targeting: Targeting = field(default_factory=Targeting)
    sources: Sources = field(default_factory=Sources)
    outreach: Outreach = field(default_factory=Outreach)
    copywriting: Copywriting = field(default_factory=Copywriting)
    notify: Notify = field(default_factory=Notify)
    mailboxes: list = field(default_factory=list)       # [Mailbox]; empty = one dry-run outbox mailbox
    niche_overrides: dict = field(default_factory=dict)  # {"hvac": {"ticket": 600}}


ENV = {
    "GOOGLE_PLACES_API_KEY": ("sources", "google_api_key"),
    "NTFY_TOPIC": ("notify", "ntfy_topic"),
}


def _fill(obj, data: dict):
    for f in fields(obj):
        if f.name not in data:
            continue
        cur, val = getattr(obj, f.name), data[f.name]
        if is_dataclass(cur):
            _fill(cur, val)
        else:
            setattr(obj, f.name, type(cur)(val) if isinstance(cur, (int, float)) and not isinstance(cur, bool) else val)


def load(path: str | None = None) -> Config:
    cfg = Config()
    if path and Path(path).exists():
        _fill(cfg, tomllib.loads(Path(path).read_text()))
    for var, (section, key) in ENV.items():
        if os.environ.get(var):
            setattr(getattr(cfg, section), key, os.environ[var])
    if os.environ.get("CLIENTBOT_DB"):
        cfg.db_path = os.environ["CLIENTBOT_DB"]
    boxes = []
    for m in cfg.mailboxes:
        if isinstance(m, Mailbox):
            boxes.append(m)
        else:
            boxes.append(Mailbox())
            _fill(boxes[-1], m)
    cfg.mailboxes = boxes or [Mailbox(address=os.environ.get("CLIENTBOT_FROM", "you@example.com"))]
    return cfg
