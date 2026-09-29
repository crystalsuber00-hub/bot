from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Profile:
    name: str
    email: str
    resume: str = ""
    phone: str = ""
    keywords: list[str] = field(default_factory=list)   # any match scores +
    exclude: list[str] = field(default_factory=list)    # any match rejects
    locations: list[str] = field(default_factory=list)  # empty = anywhere
    min_salary: int = 0
    summary: str = ""                                   # 2-3 sentences about you
    cover_letter: str = (
        "Hi {company} team,\n\n"
        "I'm applying for the {title} role. {summary}\n\n"
        "My resume is attached. I'd welcome the chance to talk.\n\n"
        "Best,\n{name}\n{email} {phone}"
    )
    intro_email: str = (
        "Hi,\n\n"
        "I just applied for the {title} position at {company} and wanted to introduce myself "
        "directly. {summary}\n\n"
        "My resume is attached, and I'd love to talk if there's a fit.\n\n"
        "Thank you,\n{name}\n{email} {phone}"
    )
    daily_limit: int = 25          # max applications sent per day
    delay_seconds: int = 45        # pause between sends
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""


def load_profile(path: str | Path) -> Profile:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Profile(**data.get("profile", data))
