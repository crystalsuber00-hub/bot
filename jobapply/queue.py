from __future__ import annotations

import json
import re
from pathlib import Path

PATH = Path("jobs.json")


def load(path: Path = PATH) -> dict[str, dict]:
    return json.loads(path.read_text()) if path.exists() else {}


def save(jobs: dict[str, dict], path: Path = PATH) -> None:
    path.write_text(json.dumps(jobs, indent=2))


def _norm(s: str) -> str:
    return "".join(c for c in re.sub(r"\(.*?\)", "", (s or "").lower()) if c.isalnum())


def _key(j: dict) -> tuple[str, str, str]:
    """Same company + title + city = same job, even when the link or label differs."""
    return (_norm(j.get("company")), _norm(j.get("title")), _norm((j.get("location") or "").split(",")[0]))


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
