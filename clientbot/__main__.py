from __future__ import annotations

import argparse
import csv
import imaplib
import json
import logging
import os
import smtplib
import sys
from datetime import datetime, timedelta

import requests

from . import config, n8n, proposal, scheduler, sources
from .agency import Agency
from .audit import gaps
from .config import Mailbox
from .outreach import Incoming

log = logging.getLogger("clientbot")


def dns_txt(name: str) -> list[str]:
    try:
        r = requests.get("https://dns.google/resolve", params={"name": name, "type": "TXT"}, timeout=10)
        return [a["data"].strip('"') for a in r.json().get("Answer", [])]
    except (requests.RequestException, ValueError):
        return []


def check(agency: Agency) -> int:
    """Verify every integration without emailing anyone. Returns the number of problems."""
    cfg, problems = agency.cfg, 0

    def report(ok: bool, what: str, hint: str = "", optional: bool = False):
        nonlocal problems
        problems += not ok and not optional
        print(f"[{'OK ' if ok else ' - ' if optional else 'FIX'}] {what}{'' if ok else '  -> ' + hint}")

    a = cfg.agency
    report(a.owner_name != "Your Name", "your name", "set [agency] owner_name")
    report(bool(a.business_address), "postal address in emails", "set [agency] business_address (US law requires it in cold email)")
    report(bool(a.calendar_url), "calendar link", "set [agency] calendar_url (Cal.com or Calendly), used in follow-ups and replies")
    for box in cfg.mailboxes:
        if not box.smtp_host:
            report(False, f"mailbox {box.address}", f"no smtp_host: emails go to {cfg.outreach.outbox_dir}/ (dry run)")
            continue
        try:
            with smtplib.SMTP(box.smtp_host, box.smtp_port, timeout=20) as s:
                s.starttls()
                s.login(box.smtp_user or box.address, box.password)
            report(True, f"SMTP {box.address}")
        except (smtplib.SMTPException, OSError) as e:
            report(False, f"SMTP {box.address}", f"{e} (password in env {box.password_env or '?'}; Gmail/Workspace need an app password)")
        if box.imap_host:
            try:
                with imaplib.IMAP4_SSL(box.imap_host, box.imap_port) as imap:
                    imap.login(box.smtp_user or box.address, box.password)
                report(True, f"IMAP {box.address} (reply detection)")
            except (imaplib.IMAP4.error, OSError) as e:
                report(False, f"IMAP {box.address}", str(e))
        else:
            report(False, f"reply detection for {box.address}", "set imap_host, or replies won't stop follow-ups")
        domain = box.address.rsplit("@", 1)[-1]
        report(any(t.startswith("v=spf1") for t in dns_txt(domain)), f"SPF record for {domain}",
               "add your provider's SPF TXT record or emails land in spam")
        report(any(t.startswith("v=DMARC1") for t in dns_txt("_dmarc." + domain)), f"DMARC record for {domain}",
               "add a TXT record at _dmarc: v=DMARC1; p=none; rua=mailto:you@" + domain)
    if cfg.sources.google_api_key:
        try:
            got = sources.GooglePlaces(cfg).search(agency.niche(cfg.targeting.niches[0]), cfg.targeting.cities[0])
            report(bool(got), f"Google Places: {len(got)} results for {cfg.targeting.niches[0]} in {cfg.targeting.cities[0]}", "no results")
        except (sources.SourceError, requests.RequestException) as e:
            report(False, "Google Places", str(e))
    else:
        report(False, "Google Places", "optional: set GOOGLE_PLACES_API_KEY for ratings, review counts and better coverage "
               "(OpenStreetMap is used meanwhile)", optional=True)
    report(bool(agency.copywriter.client) or not cfg.copywriting.enabled, "Claude copywriting",
           "optional: set ANTHROPIC_API_KEY and pip install -e '.[agency-ai]' for personalised emails", optional=True)
    report(bool(cfg.notify.ntfy_topic or cfg.notify.owner_email), "reply alerts", "set NTFY_TOPIC or [notify] owner_email")
    print(f"\n{problems} item(s) to fix" if problems else "\nAll set.")
    return problems


def print_leads(rows: list[dict]) -> None:
    for r in rows:
        print(f"#{r['id']:<5} {r['score']:>3}  {r['stage']:<12} {r['name'][:34]:<34} {r['niche'][:12]:<12} "
              f"{(r['email'] or r['phone'])[:34]}")


def demo(cfg: config.Config) -> int:
    """The whole pipeline on fake businesses: nothing leaves the computer."""
    cfg.db_path = os.environ.get("CLIENTBOT_DB", "demo-clients.db")
    if os.path.exists(cfg.db_path):
        os.remove(cfg.db_path)
    cfg.mailboxes = [Mailbox(address="you@your-agency.example")]
    cfg.agency.business_address = cfg.agency.business_address or "123 Example St, Austin, TX 78701"
    cfg.agency.calendar_url = cfg.agency.calendar_url or "https://cal.com/you/15min"
    cfg.targeting.niches, cfg.targeting.cities = ["med spa", "hvac"], ["Austin, TX"]
    cfg.outreach.outbox_dir = "outbox-demo"
    agency = Agency(cfg, source=sources.Demo(), fetch=sources.demo_fetch)
    print("1. Find:   ", agency.find())
    print("2. Audit:  ", agency.audit(100))
    print("3. Write:  ", agency.write(100))
    print("4. Approve:", agency.approve(), "sequences")
    t = datetime.now(agency.tz).replace(hour=9, minute=0, second=0, microsecond=0)
    while t.weekday() not in cfg.outreach.send_weekdays:
        t += timedelta(days=1)
    now = t.timestamp()
    for _ in range(12):
        agency.send(now)
        now += cfg.outreach.min_gap_seconds + 1
    print(f"5. Send:    {len(agency.mailer.sent)} emails written to {cfg.outreach.outbox_dir}/ (first day of warm-up: "
          f"{cfg.mailboxes[0].ramp_start} max)")
    lead = agency.db.one("SELECT * FROM leads WHERE step > 0 ORDER BY score DESC LIMIT 1")
    if lead:
        agency.clock = lambda: now
        print("6. Reply:  ", agency.handle(cfg.mailboxes[0], Incoming("<demo-reply@x>", lead["email"], "Re: " + lead["subject"],
                                                                     "Sure, send the video. How much is it?", False, False, [])))
        print("7. Proposal:", proposal.write(agency, lead["id"], out_dir="proposals-demo"))
    print("\n" + agency.status())
    if lead:
        print("\n" + agency.lead_report(lead["id"]))
    print(f"\nTry: clientbot -c {os.environ.get('CLIENTBOT_CONFIG', 'config.agency.toml')} --db {cfg.db_path} leads")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="clientbot", description="Find, email and sign local businesses for an AI front-desk service")
    ap.add_argument("-c", "--config", default=os.environ.get("CLIENTBOT_CONFIG", "config.agency.toml"))
    ap.add_argument("--db", help="database file (default from config)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="try the whole pipeline on fake businesses")
    sub.add_parser("check", help="verify mailboxes, DNS, lead source and Claude")
    p = sub.add_parser("find", help="search for businesses")
    p.add_argument("--niche", action="append")
    p.add_argument("--city", action="append")
    p.add_argument("--source", choices=["google", "osm", "demo"])
    p = sub.add_parser("import", help="import leads from a CSV (Outscraper, Apollo, a spreadsheet)")
    p.add_argument("file")
    p.add_argument("--niche", default="")
    p.add_argument("--city", default="")
    p = sub.add_parser("audit", help="check websites and score new leads")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("write", help="write email sequences for audited leads")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("drafts", help="show drafts waiting for approval")
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("approve", help="queue drafts for sending (all, or the ids given)")
    p.add_argument("ids", nargs="*", type=int)
    sub.add_parser("send", help="send what's due now (one email per mailbox; the worker paces the rest)")
    sub.add_parser("inbox", help="check mailboxes for replies and bounces")
    sub.add_parser("run", help="worker: find, audit, write, send, read replies, daily digest")
    sub.add_parser("status", help="pipeline and progress to your MRR goal")
    p = sub.add_parser("leads", help="list leads")
    p.add_argument("--stage")
    p.add_argument("--limit", type=int, default=40)
    p = sub.add_parser("lead", help="everything about one lead: audit, emails, replies")
    p.add_argument("id", type=int)
    p = sub.add_parser("calls", help="today's call / walk-in list with openers")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("mark", help="move a lead: meeting, proposal, won, lost")
    p.add_argument("id", type=int)
    p.add_argument("stage", choices=["replied", "meeting", "proposal", "won", "lost"])
    p.add_argument("--mrr", type=float)
    p.add_argument("--note", default="")
    p = sub.add_parser("proposal", help="one-page HTML proposal for a lead")
    p.add_argument("id", type=int)
    p.add_argument("--missed-per-week", type=int, default=10)
    p.add_argument("--recover-rate", type=float, default=0.3)
    p = sub.add_parser("agent", help="build a client's n8n workflows from their profile (client.example.toml)")
    p.add_argument("profile")
    p.add_argument("--out", default="builds")
    p = sub.add_parser("suppress", help="never email this address or @domain")
    p.add_argument("value")
    sub.add_parser("resume", help="un-pause mailboxes paused for bounces")
    p = sub.add_parser("export", help="write all leads to a CSV")
    p.add_argument("file")
    sub.add_parser("digest", help="send the daily digest now")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    cfg = config.load(args.config)
    if args.db:
        cfg.db_path = args.db
    if args.cmd == "demo":
        return demo(cfg)
    if args.cmd == "agent":
        try:
            for path in n8n.build(args.profile, args.out):
                print("wrote", path)
        except (ValueError, OSError) as e:
            print(f"Problem: {e}", file=sys.stderr)
            return 2
        print("Next: follow SETUP.md in that folder.")
        return 0
    agency = Agency(cfg)
    try:
        if args.cmd == "check":
            return 1 if check(agency) else 0
        if args.cmd == "find":
            print(agency.find(args.niche, args.city, args.source))
        elif args.cmd == "import":
            print(agency.import_csv(args.file, args.niche, args.city))
        elif args.cmd == "audit":
            print(agency.audit(args.limit))
        elif args.cmd == "write":
            print(agency.write(args.limit))
        elif args.cmd == "drafts":
            for r in agency.db.q("SELECT id FROM leads WHERE stage = 'drafted' ORDER BY score DESC LIMIT ?", (args.limit,)):
                print(agency.lead_report(r["id"]), "\n" + "=" * 80)
        elif args.cmd == "approve":
            print(f"queued {agency.approve(args.ids)} sequences")
        elif args.cmd == "send":
            print(agency.send())
        elif args.cmd == "inbox":
            print(agency.inbox())
        elif args.cmd == "run":
            scheduler.loop(agency)
        elif args.cmd == "status":
            print(agency.status())
        elif args.cmd == "leads":
            rows = agency.db.q("SELECT * FROM leads WHERE stage = ? ORDER BY score DESC LIMIT ?", (args.stage, args.limit)) \
                if args.stage else agency.active()[: args.limit]
            print_leads(rows)
        elif args.cmd == "lead":
            print(agency.lead_report(args.id))
        elif args.cmd == "calls":
            for r in agency.call_list(args.limit):
                print(f"#{r['id']} {r['name']}  {r['phone']}  ({r['niche']}, score {r['score']}, {r['stage']})")
                g = gaps(json.loads(r["audit"] or "{}"))
                if g:
                    print(f"   noticed: {g[0]}")
                print(f"   say: {r['call_script'] or '(run clientbot write first)'}\n")
        elif args.cmd == "mark":
            print(agency.mark(args.id, args.stage, args.mrr, args.note))
        elif args.cmd == "proposal":
            print("wrote", proposal.write(agency, args.id, missed_per_week=args.missed_per_week, recover_rate=args.recover_rate))
        elif args.cmd == "suppress":
            agency.db.suppress(args.value, "manual")
            print(f"suppressed {args.value}")
        elif args.cmd == "resume":
            for box in cfg.mailboxes:
                agency.db.set_setting(f"paused:{box.address}", "0")
            print("mailboxes resumed")
        elif args.cmd == "export":
            rows = agency.db.q("SELECT id, name, niche, city, phone, email, website, rating, reviews, score, stage, mrr, notes FROM leads")
            with open(args.file, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["id"])
                w.writeheader()
                w.writerows(rows)
            print(f"wrote {len(rows)} leads to {args.file}")
        elif args.cmd == "digest":
            print(agency.digest(force=True))
    except (ValueError, sources.SourceError) as e:
        print(f"Problem: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
