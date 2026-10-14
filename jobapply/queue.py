from __future__ import annotations

import json
from pathlib import Path

PATH = Path("jobs.json")


def load(path: Path = PATH) -> dict[str, dict]:
    return json.loads(path.read_text()) if path.exists() else {}


def save(jobs: dict[str, dict], path: Path = PATH) -> None:
    path.write_text(json.dumps(jobs, indent=2))


def _key(j: dict) -> tuple[str, str]:
    return ((j.get("company") or "").strip().lower(), (j.get("title") or "").strip().lower())


def merge(jobs: dict[str, dict], found: list[dict]) -> int:
    """Add unseen listings. One already applied to under another link (same company + title) is skipped."""
    done = {_key(j) for j in jobs.values() if j.get("status") == "applied"}
    new = 0
    for j in found:
        if j["id"] not in jobs:
            if _key(j) in done and j.get("status") == "new":
                j["status"] = "skipped"  # already applied to this company and title
            jobs[j["id"]] = j
            new += 1
    return new
