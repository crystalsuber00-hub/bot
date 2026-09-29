from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import csv
import time
from datetime import date

from . import queue, sources
from .notify import alert

from .apply import (application_subject, cover_letter, intro_letter, intro_subject,
                    open_manual, send_email)
from .profile import load_profile


def cmd_search(a, p, jobs):
    found = []
    for src in a.source or p.sources:
        for q in a.query or p.keywords:
            try:
                found += sources.SOURCES[src](q, p)
            except Exception as e:  # one bad source shouldn't stop the run
                print(f"{src}/{q}: {e}", file=sys.stderr)
    for f in a.file or []:
        found += sources.from_file(f)
    found += sources.from_inbox(p.inbox)
    for j in found:
        j["score"] = sources.score(j, p)
        if j["score"] < 0:
            j["status"] = "rejected"
    print(f"{queue.merge(jobs, found)} new listings ({len(found)} fetched)")


def _pending(jobs):
    return sorted((j for j in jobs.values() if j["status"] == "new"),
                  key=lambda j: -j["score"])


def cmd_review(a, p, jobs):
    todo = _pending(jobs)
    if a.approve_above is not None:
        for j in todo:
            if j["score"] >= a.approve_above:
                j["status"] = "approved"
        print(f"approved {sum(j['status']=='approved' for j in todo)} of {len(todo)}")
        return
    for i, j in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] {j['title']} @ {j['company']}  ({j['location']}) score={j['score']}\n  {j['url']}")
        ans = input("  (a)pprove / (s)kip / (q)uit > ").strip().lower()
        if ans == "q":
            break
        j["status"] = {"a": "approved", "s": "skipped"}.get(ans, "new")


def _send_intro(j, p) -> bool:
    """Send the intro email to the company contact; never raise, so one failure can't stop the run."""
    try:
        send_email(j, p, intro_letter(j, p), to=j["contact"], subject=intro_subject(j, p))
    except Exception as e:
        print(f"  intro email to {j['contact']} FAILED ({e.__class__.__name__}); "
              "run 'jobapply apply' again later to retry it")
        return False
    j["intro_sent"] = True
    print(f"intro email sent to {j['contact']}")
    return True


def cmd_apply(a, p, jobs):
    approved = [j for j in jobs.values() if j["status"] == "approved"]
    owed = [j for j in jobs.values()
            if j["status"] == "applied" and j["contact"] and not j.get("intro_sent")]
    today = date.today().isoformat()
    sent_today = sum(j.get("applied_on") == today for j in jobs.values())
    room = max(p.daily_limit - sent_today, 0)
    print(f"{len(approved)} approved, {len(owed)} intro emails owed, {room} sends left today"
          + (" (dry run)" if a.dry_run else ""))
    if not a.dry_run:
        for j in owed:  # retry intros that failed on an earlier run
            if _send_intro(j, p):
                queue.save(jobs)
                time.sleep(p.delay_seconds)
    for j in approved[:room] if not a.dry_run else approved:
        letter = cover_letter(j, p)
        if a.dry_run:
            print(f"\n--- {j['title']} @ {j['company']} ---\n{letter}")
            if j["contact"]:
                print(f"\n[intro email to {j['contact']}]\n{intro_letter(j, p)}")
            continue
        emailed = False
        if j["email"]:
            try:
                send_email(j, p, letter)
            except Exception as e:
                print(f"FAILED to email {j['company']} ({e.__class__.__name__}: {e}); skipping it")
                continue
            emailed = True
            print(f"applied by email: {j['company']}")
        else:
            f = open_manual(j, letter, Path("letters"))
            input(f"letter saved to {f}; finish in browser, press Enter when submitted ")
        j["status"], j["applied_on"] = "applied", today
        queue.save(jobs)  # persist before the intro so a failure never re-applies
        if j["contact"] and not j.get("intro_sent"):
            emailed = _send_intro(j, p) or emailed
            queue.save(jobs)
        if emailed:  # only pace actual email sends; browser applications are already slow
            time.sleep(p.delay_seconds)


def load_contacts(path: str = "contacts.csv") -> dict[str, str]:
    """company -> HR/careers email you (or Claude) found on the company's own site."""
    try:
        with open(path, newline="") as f:
            return {r["company"].strip().lower(): r["email"].strip() for r in csv.DictReader(f)}
    except FileNotFoundError:
        return {}


def apply_contacts(jobs) -> None:
    contacts = load_contacts()
    for j in jobs.values():
        if not j.get("contact"):
            email = contacts.get((j["company"] or "").strip().lower(), "")
            if email and email.lower() != (j.get("email") or "").lower():
                j["contact"] = email


def cmd_contacts(a, p, jobs):
    """Which jobs will NOT get an intro email because no company contact is known?"""
    live = [j for j in jobs.values() if j["status"] in ("new", "approved", "manual", "applied")
            and not j.get("contact")]
    print(f"{len(live)} jobs have no contact address, so they get the application only:")
    for j in live:
        print(f"  {j['company']}  ({j['title']})")
    print("\nFound a real HR/careers address on the company's website? Add a line to contacts.csv:\n"
          "  company,email\n  Acme Inc,hr@acme.com\nThen run: jobapply apply  (owed intros go out too)")


def cmd_watch(a, p, jobs):
    """Search on a schedule and alert you about new payroll jobs. Never approves or sends anything."""
    print(f"watching every {p.run_every_minutes} min; Ctrl+C to stop")
    while True:
        try:
            cmd_search(argparse.Namespace(source=None, query=None, file=None), p, jobs)
            apply_contacts(jobs)
            fresh = [j for j in jobs.values() if j["status"] == "new" and j["score"] >= 1
                     and not j.get("alerted")]
            if fresh:
                names = "; ".join(f"{j['title']} @ {j['company']}" for j in fresh[:5])
                alert(f"{len(fresh)} new payroll job(s)",
                      f"{names}. Run: jobapply review --approve-above 1, then jobapply apply")
                for j in fresh:
                    j["alerted"] = True
            queue.save(jobs)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # keep watching through network hiccups
            print(f"check failed: {e.__class__.__name__}: {e}")
        if a.once:
            return
        time.sleep(p.run_every_minutes * 60)


def cmd_preview(a, p, jobs):
    job = sources._job("preview", "Payroll Specialist", "Example Company", "https://example.com")
    print(f"=== APPLICATION EMAIL ===\nSubject: {application_subject(job, p)}\n\n{cover_letter(job, p)}")
    print(f"\n=== INTRO EMAIL (only sent where a company contact is known) ===\n"
          f"Subject: {intro_subject(job, p)}\n\n{intro_letter(job, p)}")
    print("\nChange any of this with:  jobapply edit")


def cmd_edit(a, p, jobs):
    editor = os.environ.get("EDITOR", "nano")
    print(f"Opening {a.config} in {editor}. Edit headline / highlights / cover_letter / intro_email, "
          "save, then run: jobapply preview")
    subprocess.call([editor, a.config])


def cmd_status(a, p, jobs):
    counts: dict[str, int] = {}
    for j in jobs.values():
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    print(counts or "empty queue")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobapply")
    ap.add_argument("-c", "--config", default="profile.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="fetch and score listings")
    s.add_argument("-q", "--query", action="append", help="default: your keywords")
    s.add_argument("-s", "--source", action="append", choices=list(sources.SOURCES))
    s.add_argument("-f", "--file", action="append", help="JSON/CSV of listings")
    r = sub.add_parser("review", help="approve/skip queued listings")
    r.add_argument("--approve-above", type=int, help="bulk-approve score >= N")
    d = sub.add_parser("apply", help="send approved applications")
    d.add_argument("--dry-run", action="store_true")
    sub.add_parser("contacts", help="list jobs that will not get an intro email (no company contact known)")
    w = sub.add_parser("watch", help="search on a schedule and alert you about new jobs (sends nothing)")
    w.add_argument("--once", action="store_true", help="one check then exit")
    sub.add_parser("status")
    sub.add_parser("preview", help="show the emails exactly as they will be sent")
    sub.add_parser("edit", help="open your profile to change the email wording")
    a = ap.parse_args(argv)
    p = load_profile(a.config)
    jobs = queue.load()
    apply_contacts(jobs)
    {"search": cmd_search, "review": cmd_review, "apply": cmd_apply, "status": cmd_status,
     "preview": cmd_preview, "edit": cmd_edit, "contacts": cmd_contacts,
     "watch": cmd_watch}[a.cmd](a, p, jobs)
    queue.save(jobs)


if __name__ == "__main__":
    main()
