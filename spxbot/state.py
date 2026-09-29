from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .models import Position


class State:
    """JSON file: {"positions": [...], "skipped": {date: reason}}. One trade per date."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.data = {"positions": [], "skipped": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        os.replace(tmp, self.path)

    def position_for(self, date: str) -> Optional[Position]:
        for p in self.data["positions"]:
            if p["date"] == date:
                return Position(**p)
        return None

    def upsert(self, pos: Position) -> None:
        rows = [p for p in self.data["positions"] if p["id"] != pos.id]
        rows.append(pos.to_dict())
        self.data["positions"] = rows
        self.save()

    def skipped(self, date: str) -> Optional[str]:
        return self.data["skipped"].get(date)

    def mark_skipped(self, date: str, reason: str) -> None:
        self.data["skipped"][date] = reason
        self.save()
