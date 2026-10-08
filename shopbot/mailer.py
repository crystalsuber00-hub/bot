from __future__ import annotations

import logging
import re
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

import requests

from .config import Config

log = logging.getLogger("shopbot")


class Mailer:
    """Sends through any SMTP provider (Gmail app password, SendGrid, Amazon SES, Postmark...).
    Without SMTP settings, emails are written to the outbox folder so nothing is lost while testing."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sent: list[EmailMessage] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None, unsubscribe_url: str = "") -> bool:
        e, s = self.cfg.email, self.cfg.store
        msg = EmailMessage()
        msg["From"] = formataddr((s.name, e.from_address or s.contact_email))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Reply-To"] = s.contact_email
        msg["Message-ID"] = make_msgid()
        if unsubscribe_url:
            msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        self.sent.append(msg)
        if not e.smtp_host:
            out = Path(e.outbox_dir)
            out.mkdir(parents=True, exist_ok=True)
            name = f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', subject.lower())[:40]}.eml"
            (out / name).write_bytes(bytes(msg))
            log.info("email (outbox) to %s: %s", to, subject)
            return True
        try:
            with smtplib.SMTP(e.smtp_host, e.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                if e.smtp_user:
                    smtp.login(e.smtp_user, e.smtp_password)
                smtp.send_message(msg)
            log.info("email to %s: %s", to, subject)
            return True
        except (smtplib.SMTPException, OSError) as err:
            log.warning("email to %s failed: %s", to, err)
            return False


class Notifier:
    """Alerts for the store owner: ntfy push and/or email."""

    def __init__(self, cfg: Config, mailer: Mailer):
        self.cfg, self.mailer = cfg, mailer

    def alert(self, title: str, body: str = "", urgent: bool = False) -> None:
        n = self.cfg.notify
        log.info("ALERT | %s | %s", title, body)
        if n.ntfy_topic:
            try:
                requests.post(f"{n.ntfy_server.rstrip('/')}/{n.ntfy_topic}", data=(body or title).encode(),
                              headers={"Title": title, "Priority": "high" if urgent else "default",
                                       "Tags": "rotating_light" if urgent else "shopping_cart"}, timeout=10)
            except requests.RequestException as e:
                log.warning("ntfy failed: %s", e)
        if n.owner_email and urgent:
            self.mailer.send(n.owner_email, f"[{self.cfg.store.name}] {title}", body or title)
