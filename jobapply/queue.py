from __future__ import annotations

import json
from pathlib import Path

PATH = Path("jobs.json")


def load(path: Path = PATH) -> dict[str, dict]:
    return json.loads(path.read_text()) if path.exists() else {}


def save(jobs: dict[str, dict], path: Path = PATH) -> None:
    path.write_text(json.dumps(jobs, indent=2))


def merge(jobs: dict[str, dict], found: list[dict]) -> int:
    new = 0
    for j in found:
        if j["id"] not in jobs:
            jobs[j["id"]] = j
            new += 1
    return new
