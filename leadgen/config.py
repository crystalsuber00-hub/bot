from __future__ import annotations

import tomllib
from pathlib import Path

PATH = Path("business/config.toml")


def load() -> dict:
    return tomllib.loads(PATH.read_text())
