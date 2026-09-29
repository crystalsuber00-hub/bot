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
        self.data = {"positions": [], "skipped": {}, "halt": None}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

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

    def all_positions(self) -> list[Position]:
        return [Position(**p) for p in self.data["positions"]]

    def active_positions(self) -> list[Position]:
        return [p for p in self.all_positions() if p.status in ("pending_entry", "open", "pending_exit")]

    def realized_on(self, date: str) -> float:
        return sum(p.realized() for p in self.all_positions() if p.date == date)

    def realized_total(self) -> float:
        return sum(p.realized() for p in self.all_positions())

    # halt = {"kind": "frozen" | "entries", "reason": str, "alerted": date}
    def halt_info(self) -> Optional[dict]:
        return self.data.get("halt")

    def halt(self, kind: str, reason: str) -> None:
        cur = self.data.get("halt")
        if cur and cur["kind"] == "frozen":
            return  # never downgrade a freeze
        self.data["halt"] = {"kind": kind, "reason": reason, "alerted": ""}
        self.save()

    def clear_halt(self) -> None:
        self.data["halt"] = None
        self.save()
