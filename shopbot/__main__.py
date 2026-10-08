from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
import threading
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import requests

from . import config, scheduler
from .shop import Shop
from .suppliers import SupplierError
from .web import App

log = logging.getLogger("shopbot")


class ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)


def serve(shop: Shop) -> None:
    cfg = shop.cfg
    httpd = make_server(cfg.host, cfg.port, App(shop), server_class=ThreadingServer, handler_class=QuietHandler)
    log.info("store running at http://%s:%d  (public URL: %s)", cfg.host, cfg.port, cfg.store.base_url)
    if cfg.admin.password:
        log.info("admin dashboard: %s/admin (user %r)", cfg.store.base_url, cfg.admin.username)
    httpd.serve_forever()


def check(shop: Shop) -> int:
    """Verify every integration without taking orders. Returns the number of problems."""
    cfg, problems = shop.cfg, 0

    def report(ok: bool, what: str, hint: str = "", optional: bool = False):
        nonlocal problems
        problems += not ok and not optional
        print(f"[{'OK ' if ok else ' - ' if optional else 'FIX'}] {what}{'' if ok else '  -> ' + hint}")

    try:
        found = shop.supplier.search(cfg.research.keywords[0], 3)
        report(bool(found), f"supplier '{cfg.supplier.name}': search '{cfg.research.keywords[0]}' -> {len(found)} results",
               "no results; try other keywords")
    except Exception as e:
        report(False, f"supplier '{cfg.supplier.name}'", str(e))
    report(cfg.supplier.name != "demo", "real supplier configured", "set [supplier] name = \"cj\" and CJ_API_KEY to sell real products")
    if cfg.payments.stripe_secret_key:
        try:
            r = requests.get("https://api.stripe.com/v1/balance", auth=(cfg.payments.stripe_secret_key, ""), timeout=20)
            report(r.ok, f"Stripe key ({'live' if 'live' in cfg.payments.stripe_secret_key else 'test'} mode)", r.text[:200])
        except requests.RequestException as e:
            report(False, "Stripe", str(e))
        report(bool(cfg.payments.stripe_webhook_secret), "Stripe webhook secret",
               f"add endpoint {cfg.store.base_url}/webhooks/stripe in Stripe and set STRIPE_WEBHOOK_SECRET")
    else:
        report(False, "payments", "set STRIPE_SECRET_KEY (or store.demo = true to try the demo checkout)")
    if cfg.email.smtp_host:
        try:
            with smtplib.SMTP(cfg.email.smtp_host, cfg.email.smtp_port, timeout=20) as s:
                s.starttls()
                if cfg.email.smtp_user:
                    s.login(cfg.email.smtp_user, cfg.email.smtp_password)
            report(True, f"SMTP {cfg.email.smtp_host}")
        except (smtplib.SMTPException, OSError) as e:
            report(False, f"SMTP {cfg.email.smtp_host}", str(e))
    else:
        report(False, "email", f"no SMTP_HOST: emails are written to {cfg.email.outbox_dir}/ instead of sent")
    report(cfg.store.base_url.startswith("https://"), f"public URL {cfg.store.base_url}", "set BASE_URL to your https domain")
    report(bool(cfg.admin.password), "admin password", "set ADMIN_PASSWORD")
    report(bool(cfg.notify.ntfy_topic or cfg.notify.owner_email), "owner alerts", "set NTFY_TOPIC or [notify] owner_email")
    report("Example" not in cfg.store.business_address and "example.com" not in cfg.store.contact_email,
           "store contact details", "set store.business_address and store.contact_email (legally required in marketing email)")
    report(bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) or not cfg.copywriting.enabled,
           "AI copywriting", "optional: set ANTHROPIC_API_KEY for better product titles and descriptions", optional=True)
    so, tr, m = cfg.social, cfg.tracking, cfg.marketing
    for name, ok in [("Pinterest auto-posting", bool(so.pinterest_board_id and (so.pinterest_access_token or so.pinterest_refresh_token))),
                     ("Facebook auto-posting", bool(so.facebook_page_id and so.facebook_page_token)),
                     ("Instagram auto-posting", bool(so.instagram_user_id and so.facebook_page_token))]:
        report(ok, name, "optional: see 'Social accounts' in shopbot/README.md", optional=True)
    report(bool(tr.ga4_id or tr.meta_pixel_id or tr.google_ads_id), "analytics / ad pixels",
           "set [tracking] ids before spending on ads", optional=True)
    if m.welcome_code and cfg.payments.stripe_secret_key:
        try:
            r = requests.get("https://api.stripe.com/v1/promotion_codes", params={"code": m.welcome_code, "active": "true"},
                             auth=(cfg.payments.stripe_secret_key, ""), timeout=20)
            report(r.ok and bool(r.json().get("data")), f"Stripe promotion code {m.welcome_code}",
                   "create it in Stripe (Product catalog > Coupons) or the welcome emails promise a code that doesn't work")
        except requests.RequestException as e:
            report(False, "Stripe promotion code", str(e))
    print(f"\n{problems} item(s) to fix" if problems else "\nAll set.")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="shopbot", description="Automated dropshipping store")
    ap.add_argument("command", choices=["run", "serve", "worker", "research", "sync", "fulfill", "track", "newsletter",
                                        "social", "guides", "report",
                                        "check", "demo"], help="run = website + automation; demo = try it with fake products")
    ap.add_argument("-c", "--config", default=os.environ.get("SHOPBOT_CONFIG", "config.shop.toml"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    cfg = config.load(args.config)
    if args.command == "demo":
        cfg.supplier.name, cfg.store.demo = "demo", True
        cfg.payments.stripe_secret_key = ""
        cfg.db_path = os.environ.get("SHOPBOT_DB", "demo.db")
        cfg.admin.password = cfg.admin.password or "demo"
        cfg.store.base_url = os.environ.get("BASE_URL", f"http://localhost:{cfg.port}")
        cfg.schedule.fulfill_minutes = cfg.schedule.tracking_minutes = 0.5
        print(f"Demo store: {cfg.store.base_url}   admin: {cfg.store.base_url}/admin (admin / {cfg.admin.password})")
        print("Fake products, fake checkout; emails go to outbox/. Ctrl+C to stop.\n")
    try:
        shop = Shop(cfg)
    except (SupplierError, RuntimeError) as e:
        print(f"Setup problem: {e}", file=sys.stderr)
        return 2

    if args.command == "check":
        return 1 if check(shop) else 0
    if args.command in ("research", "sync", "fulfill", "track"):
        print(getattr(shop, args.command)())
        return 0
    if args.command in ("newsletter", "guides", "report"):
        print(getattr(shop, args.command)(force=True))
        return 0
    if args.command == "social":
        print(shop.social())
        return 0
    if args.command == "worker":
        scheduler.loop(shop)
        return 0
    if args.command in ("run", "demo"):
        threading.Thread(target=scheduler.loop, args=(shop,), daemon=True, name="worker").start()
    try:
        serve(shop)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
