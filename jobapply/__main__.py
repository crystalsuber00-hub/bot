from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import queue, sources
import time
from datetime import date

from .apply import (application_subject, cover_letter, intro_letter, intro_subject,
                    open_manual, send_email)
from .profile import load_profile


def cmd_search(a, p, jobs):
    found = []
    for src in a.source or p.sources:
        for q in a.query or p.keywords:
            try:
                found += sources.SOURCES[src](q)
            except Exception as e:  # one bad source shouldn't stop the run
                print(f"{src}/{q}: {e}", file=sys.stderr)
    for f in a.file or []:
        found += sources.from_file(f)
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


def cmd_apply(a, p, jobs):
    approved = [j for j in jobs.values() if j["status"] == "approved"]
    today = date.today().isoformat()
    sent_today = sum(j.get("applied_on") == today for j in jobs.values())
    room = max(p.daily_limit - sent_today, 0)
    print(f"{len(approved)} approved, {room} sends left today" + (" (dry run)" if a.dry_run else ""))
    for j in approved[:room] if not a.dry_run else approved:
        letter = cover_letter(j, p)
        if a.dry_run:
            print(f"\n--- {j['title']} @ {j['company']} ---\n{letter}")
            if j["contact"]:
                print(f"\n[intro email to {j['contact']}]\n{intro_letter(j, p)}")
            continue
        if j["email"]:
            send_email(j, p, letter)
            print(f"applied by email: {j['company']}")
        else:
            f = open_manual(j, letter, Path("letters"))
            input(f"letter saved to {f}; finish in browser, press Enter when submitted ")
        if j["contact"] and not j.get("intro_sent"):
            send_email(j, p, intro_letter(j, p), to=j["contact"],
                       subject=intro_subject(j, p))
            j["intro_sent"] = True
            print(f"intro email sent to {j['contact']}")
        j["status"], j["applied_on"] = "applied", today
        queue.save(jobs)  # persist after each so a crash never re-applies
        time.sleep(p.delay_seconds)


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
    sub.add_parser("status")
    sub.add_parser("preview", help="show the emails exactly as they will be sent")
    sub.add_parser("edit", help="open your profile to change the email wording")
    a = ap.parse_args(argv)
    p = load_profile(a.config)
    jobs = queue.load()
    {"search": cmd_search, "review": cmd_review, "apply": cmd_apply, "status": cmd_status,
     "preview": cmd_preview, "edit": cmd_edit}[a.cmd](a, p, jobs)
    queue.save(jobs)


if __name__ == "__main__":
    main()
