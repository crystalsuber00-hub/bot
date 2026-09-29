from __future__ import annotations

import mimetypes
import os
import smtplib
import webbrowser
from email.message import EmailMessage
from pathlib import Path

import requests

from .profile import Profile


def cover_letter(job: dict, p: Profile) -> str:
    """Template letter; if ANTHROPIC_API_KEY is set, Claude tailors it to the listing."""
    base = p.cover_letter.format(company=job["company"] or "hiring", title=job["title"],
                                 summary=p.summary, name=p.name, email=p.email, phone=p.phone)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return base
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        json={"model": os.environ.get("JOBAPPLY_MODEL", "claude-sonnet-5-5"), "max_tokens": 600,
              "messages": [{"role": "user", "content":
                  "Rewrite this cover letter for the job below. Keep it under 150 words, "
                  "truthful, no invented experience.\n\nLETTER:\n" + base +
                  f"\n\nJOB: {job['title']} at {job['company']}\n{job['description'][:3000]}"}]},
        timeout=60)
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def intro_letter(job: dict, p: Profile) -> str:
    return p.intro_email.format(company=job["company"] or "your company", title=job["title"],
                                summary=p.summary, name=p.name, email=p.email, phone=p.phone)


def send_email(job: dict, p: Profile, letter: str, to: str = "", subject: str = "") -> None:
    msg = EmailMessage()
    msg["From"], msg["To"] = p.email, to or job["email"]
    msg["Subject"] = subject or f"Application: {job['title']} - {p.name}"
    msg.set_content(letter)
    if p.resume:
        path = Path(p.resume)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        msg.add_attachment(path.read_bytes(), maintype=ctype.split("/")[0],
                           subtype=ctype.split("/")[1], filename=path.name)
    with smtplib.SMTP(p.smtp_host, p.smtp_port) as s:
        s.starttls()
        s.login(p.smtp_user or p.email, os.environ["SMTP_PASSWORD"])
        s.send_message(msg)


def open_manual(job: dict, letter: str, out_dir: Path) -> Path:
    """No email address: save the letter and open the listing to finish in the browser."""
    out_dir.mkdir(exist_ok=True)
    f = out_dir / f"{job['id']}.txt"
    f.write_text(letter)
    webbrowser.open(job["url"])
    return f
