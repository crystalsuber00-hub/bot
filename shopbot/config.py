from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path


@dataclass
class Store:
    name: str = "My Store"
    tagline: str = "Hand-picked products, shipped to your door"
    base_url: str = "http://localhost:8000"   # public https URL in production (used in emails, feeds, Stripe redirects)
    currency: str = "usd"
    contact_email: str = "support@example.com"
    business_address: str = "123 Example St, City, ST 00000, USA"   # required in marketing emails (CAN-SPAM)
    ship_to_countries: list = field(default_factory=lambda: ["US"])
    shipping_days: str = "7-15 business days"
    return_days: int = 30
    demo: bool = False        # banner + fake checkout; refused when a Stripe key is set


@dataclass
class Research:
    keywords: list = field(default_factory=lambda: ["pet grooming", "kitchen gadget", "phone stand"])
    blocked_words: list = field(default_factory=lambda: [
        "nike", "adidas", "apple", "iphone case original", "disney", "marvel", "pokemon", "gucci",
        "louis vuitton", "chanel", "rolex", "lego", "replica", "medical", "cbd", "vape", "knife", "weapon",
        # pet products that are regulated (EPA pesticides, FDA drugs) or ship badly as liquids
        "flea", "tick", "pesticide", "insecticide", "dewormer", "medicine", "supplement", "shampoo", "spray"])
    min_cost: float = 3.0          # supplier price + shipping, USD
    max_cost: float = 40.0
    max_shipping_days: int = 20    # skip products whose cheapest shipping takes longer
    candidates_per_keyword: int = 20
    new_per_run: int = 5           # products listed per research run
    max_products: int = 60         # stop adding once the catalogue is this big
    ship_from: str = "CN"


@dataclass
class Pricing:
    # retail = (cost + shipping) * multiplier, as a .99 price; [max landed cost, multiplier], first match wins
    markup_tiers: list = field(default_factory=lambda: [[10, 3.0], [20, 2.5], [30, 2.1], [1e9, 1.8]])
    min_profit: float = 6.0        # $ per unit after supplier, shipping and card fees; below this the product is hidden
    min_price: float = 14.99
    card_fee_pct: float = 0.029
    card_fee_fixed: float = 0.30
    # crossed-out "was" price = price * this. Off by default: advertising a former price you never actually charged
    # is deceptive pricing (FTC 16 CFR 233, EU Omnibus Directive). Only enable it if you really sold at that price.
    compare_at_markup: float = 0.0


@dataclass
class Fulfillment:
    auto_order: bool = True        # place supplier orders automatically when a customer pays
    auto_pay: bool = True          # pay supplier orders from your supplier wallet balance
    auto_refund_cancelled: bool = True   # refund the customer if the supplier cancels
    max_auto_order_cost: float = 150.0   # supplier cost above this goes to manual review
    max_attempts: int = 3
    sandbox: bool = True           # CJ sandbox orders (no real charge); set false to go live


@dataclass
class Supplier:
    name: str = "demo"             # "cj" (CJdropshipping) or "demo"
    cj_api_key: str = ""
    cj_base_url: str = "https://developers.cjdropshipping.com/api2.0/v1"


@dataclass
class Payments:
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    automatic_tax: bool = False    # Stripe Tax (enable it in your Stripe dashboard first)
    recover_abandoned: bool = True # Stripe recovery links + marketing consent at checkout


@dataclass
class Email:
    smtp_host: str = ""            # empty = write emails to outbox/ instead of sending
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = ""
    outbox_dir: str = "outbox"


@dataclass
class Marketing:
    newsletter: bool = True
    newsletter_weekday: int = 3    # 0=Mon
    newsletter_hour_utc: int = 15
    followup_days: int = 5         # days after delivery to send the thank-you / recommendations email
    welcome_code: str = ""         # Stripe promotion code offered to new subscribers, e.g. WELCOME10 (create it in Stripe)
    welcome_percent: int = 10      # only used in the wording of the signup box and emails
    welcome_second_email_days: int = 3
    winback_days: int = 60         # days after delivery to invite opted-in customers back (0 = off)
    guides: bool = True            # weekly SEO buying guide per collection (needs ANTHROPIC_API_KEY)
    report_weekday: int = 0        # weekly owner report with numbers + video ideas (0=Mon)


@dataclass
class Social:
    """Auto-posting new and best-selling products. Each channel is off until its credentials are set."""
    pinterest_board_id: str = ""
    pinterest_access_token: str = ""      # env PINTEREST_ACCESS_TOKEN
    pinterest_refresh_token: str = ""     # env PINTEREST_REFRESH_TOKEN (lets the bot renew the access token itself)
    pinterest_client_id: str = ""         # env PINTEREST_APP_ID
    pinterest_client_secret: str = ""     # env PINTEREST_APP_SECRET
    pinterest_per_day: int = 5
    pinterest_repin_after_days: int = 21  # Pinterest rewards fresh pins: re-pin good products with new copy
    facebook_page_id: str = ""
    facebook_page_token: str = ""         # env FACEBOOK_PAGE_TOKEN (long-lived Page token)
    facebook_per_day: int = 1
    instagram_user_id: str = ""           # Instagram professional account linked to the Page; uses the Page token
    instagram_per_day: int = 1
    graph_version: str = "v25.0"
    hashtags: list = field(default_factory=lambda: ["dogsofinstagram", "doggrooming", "petcare", "catsofinstagram"])


@dataclass
class Tracking:
    """Analytics and ad pixels. Needed before running ads so the ad platforms can learn who buys."""
    ga4_id: str = ""                      # G-XXXXXXX
    meta_pixel_id: str = ""
    google_ads_id: str = ""               # AW-XXXXXXX
    google_ads_purchase_label: str = ""


@dataclass
class Copywriting:
    enabled: bool = True           # uses Claude when ANTHROPIC_API_KEY is set, plain cleanup otherwise
    model: str = "claude-opus-5-5"


@dataclass
class Notify:
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"
    owner_email: str = ""


@dataclass
class Schedule:
    research_hours: float = 24
    sync_hours: float = 6
    fulfill_minutes: float = 5
    tracking_minutes: float = 60
    followup_minutes: float = 60
    social_minutes: float = 60


@dataclass
class Admin:
    username: str = "admin"
    password: str = ""             # admin pages are disabled until set
    secret: str = ""               # signs admin form tokens; random per process if empty


@dataclass
class Config:
    db_path: str = "data/shop.db"
    host: str = "0.0.0.0"
    port: int = 8000
    store: Store = field(default_factory=Store)
    research: Research = field(default_factory=Research)
    pricing: Pricing = field(default_factory=Pricing)
    fulfillment: Fulfillment = field(default_factory=Fulfillment)
    supplier: Supplier = field(default_factory=Supplier)
    payments: Payments = field(default_factory=Payments)
    email: Email = field(default_factory=Email)
    marketing: Marketing = field(default_factory=Marketing)
    copywriting: Copywriting = field(default_factory=Copywriting)
    social: Social = field(default_factory=Social)
    tracking: Tracking = field(default_factory=Tracking)
    notify: Notify = field(default_factory=Notify)
    schedule: Schedule = field(default_factory=Schedule)
    admin: Admin = field(default_factory=Admin)


# secrets are read from the environment so config.toml can be committed without them
ENV = {
    "CJ_API_KEY": ("supplier", "cj_api_key"),
    "STRIPE_SECRET_KEY": ("payments", "stripe_secret_key"),
    "STRIPE_WEBHOOK_SECRET": ("payments", "stripe_webhook_secret"),
    "SMTP_HOST": ("email", "smtp_host"),
    "SMTP_USER": ("email", "smtp_user"),
    "SMTP_PASSWORD": ("email", "smtp_password"),
    "NTFY_TOPIC": ("notify", "ntfy_topic"),
    "ADMIN_PASSWORD": ("admin", "password"),
    "ADMIN_SECRET": ("admin", "secret"),
    "BASE_URL": ("store", "base_url"),
    "PINTEREST_ACCESS_TOKEN": ("social", "pinterest_access_token"),
    "PINTEREST_REFRESH_TOKEN": ("social", "pinterest_refresh_token"),
    "PINTEREST_APP_ID": ("social", "pinterest_client_id"),
    "PINTEREST_APP_SECRET": ("social", "pinterest_client_secret"),
    "FACEBOOK_PAGE_TOKEN": ("social", "facebook_page_token"),
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
    if os.environ.get("SHOPBOT_DB"):
        cfg.db_path = os.environ["SHOPBOT_DB"]
    if os.environ.get("PORT"):
        cfg.port = int(os.environ["PORT"])
    cfg.store.base_url = cfg.store.base_url.rstrip("/")
    return cfg
