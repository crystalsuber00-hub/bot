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
    headlines: list[str] = field(default_factory=list)  # rotated per job so emails aren't identical
    cover_letters_per_day: int = 3  # browser applications that get a cover letter; the rest are resume-only
    headline: str = ""                                  # one-sentence opener; falls back to summary
    highlights: list[str] = field(default_factory=list) # bullet points in the emails
    cover_letter: str = (
        "Hi {company} team,\n\n"
        "I'm applying for the {title} role. {headline}\n\n"
        "{bullets}\n\n"
        "My resume is attached. I'd welcome the chance to talk.\n\n"
        "Best,\n{name}\n{phone} | {email}"
    )
    intro_email: str = (
        "Hi,\n\n"
        "I just applied for the {title} position at {company} and wanted to introduce myself "
        "directly. {headline}\n\n"
        "{bullets}\n\n"
        "My resume is attached, and I'd love to talk if there's a fit.\n\n"
        "Thank you,\n{name}\n{phone} | {email}"
    )
    title_keywords: list[str] = field(default_factory=lambda: ["payroll", "accounts payable"])  # title must contain one
    search_locations: list[str] = field(default_factory=lambda: [
        "San Francisco, CA", "Oakland, CA", "San Jose, CA", "Fremont, CA", "Walnut Creek, CA",
        "San Mateo, CA", "Santa Clara, CA", "Hayward, CA"])
    auto_min_score: int = 1        # run mode approves listings scoring at least this
    send_hours: list[int] = field(default_factory=lambda: [8, 18])  # only email between these local hours
    run_every_minutes: int = 60
    inbox: str = "inbox"           # drop JSON/CSV listing files here; run mode imports them
    sources: list[str] = field(default_factory=lambda: ["remotive", "remoteok"])
    daily_limit: int = 25          # max applications sent per day
    delay_seconds: int = 45        # pause between sends
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""


def load_profile(path: str | Path) -> Profile:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Profile(**data.get("profile", data))
