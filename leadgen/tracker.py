"""leads.csv is the single source of truth. New searches add leads; they never overwrite your notes."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .find import score

HOME = Path("business")
LEADS = HOME / "leads.csv"
FIELDS = ["id", "name", "category", "phone", "email", "address", "city", "social", "hours", "lat", "lon",
          "score", "status", "last_contact", "next_followup", "notes"]
STATUSES = ["new", "called", "voicemail", "interested", "won", "lost", "do_not_contact"]


def load() -> list[dict]:
    if not LEADS.exists():
        return []
    with LEADS.open(newline="") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict]) -> None:
    HOME.mkdir(exist_ok=True)
    rows.sort(key=lambda r: (STATUSES.index(r["status"]) if r["status"] in STATUSES else 99, -int(r["score"] or 0)))
    with LEADS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def merge(new: list[dict], default_city: str) -> int:
    rows = load()
    known = {r["id"] for r in rows}
    added = 0
    for lead in new:
        if lead["id"] in known:
            continue
        lead.setdefault("city", "")
        lead["city"] = lead["city"] or default_city
        rows.append({**{k: "" for k in FIELDS}, **lead, "score": score(lead), "status": "new"})
        added += 1
    save(rows)
    return added


def mark(lead_id: str, status: str, note: str = "", followup_days: int | None = None) -> dict:
    if status not in STATUSES:
        raise SystemExit(f"status must be one of {STATUSES}")
    rows = load()
    for r in rows:
        if r["id"] == lead_id:
            r["status"] = status
            r["last_contact"] = date.today().isoformat()
            if note:
                r["notes"] = (r["notes"] + " | " if r["notes"] else "") + f"{date.today():%m/%d} {note}"
            days = followup_days if followup_days is not None else {"called": 3, "voicemail": 2, "interested": 1}.get(status)
            r["next_followup"] = date.fromordinal(date.today().toordinal() + days).isoformat() if days else ""
            save(rows)
            return r
    raise SystemExit(f"no lead with id {lead_id}")
