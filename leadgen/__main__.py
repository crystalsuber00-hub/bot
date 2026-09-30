import argparse
from collections import Counter

from . import config, daily, find, preview, tracker


def main() -> None:
    ap = argparse.ArgumentParser(prog="leadgen", description="Local website business pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="find new leads in your city and build their preview sites")
    f.add_argument("--place", help='override config, e.g. "Macon, GA"')
    sub.add_parser("today", help="write business/today.md: follow-ups and new calls")
    m = sub.add_parser("mark", help="record a call outcome")
    m.add_argument("id")
    m.add_argument("status", choices=tracker.STATUSES)
    m.add_argument("note", nargs="?", default="")
    m.add_argument("--followup", type=int, help="days until follow-up (default by status)")
    e = sub.add_parser("email", help="print a sales email for a lead that has an email address")
    e.add_argument("id")
    w = sub.add_parser("final", help="after they pay: build the live version (no preview banner)")
    w.add_argument("id")
    sub.add_parser("stats", help="pipeline counts")
    sub.add_parser("categories", help="list business categories")
    a = ap.parse_args()
    cfg = config.load()

    if a.cmd == "categories":
        print(", ".join(find.CATEGORIES))
    elif a.cmd == "find":
        place = a.place or cfg["search"]["place"]
        leads = find.find(place, cfg["search"]["categories"] or None)
        added = tracker.merge(leads, place.split(",")[0])
        built = 0
        for r in tracker.load():
            path = preview.SITE / preview.slug(r["name"], r["id"]) / "index.html"
            if r["status"] == "new" and not path.exists():
                preview.build(r, cfg["you"])
                built += 1
        print(f"{len(leads)} businesses without a real website found in {place}; {added} new; {built} previews built in site/")
    elif a.cmd == "today":
        text = daily.write(cfg)
        print(text[:400] + "\n...\nfull list: business/today.md")
    elif a.cmd == "mark":
        r = tracker.mark(a.id, a.status, a.note, a.followup)
        print(f"{r['name']}: {r['status']}" + (f", follow up {r['next_followup']}" if r["next_followup"] else ""))
    elif a.cmd == "email":
        r = next((x for x in tracker.load() if x["id"] == a.id), None)
        if not r:
            raise SystemExit("no such lead")
        if r["status"] == "do_not_contact":
            raise SystemExit("this lead asked not to be contacted")
        print(daily.email_draft(r, cfg["you"], cfg["offer"]))
    elif a.cmd == "final":
        r = next((x for x in tracker.load() if x["id"] == a.id), None)
        if not r:
            raise SystemExit("no such lead")
        out = preview.SITE.parent / "clients" / preview.slug(r["name"], r["id"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(preview.render(r, cfg["you"], final=True))
        print(f"live version: {out}/index.html. Edit it with their real services, photos and text before launch.")
    elif a.cmd == "stats":
        rows = tracker.load()
        c = Counter(r["status"] for r in rows)
        print(f"{len(rows)} leads: " + ", ".join(f"{s} {c[s]}" for s in tracker.STATUSES if c[s]))
        won = c["won"]
        o = cfg["offer"]
        print(f"won {won}: ${won * o['setup_price']:,} setup + ${won * o['monthly_price']:,}/month recurring")


if __name__ == "__main__":
    main()
