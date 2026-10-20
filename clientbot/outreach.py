"""Sending cold email from your mailboxes, and reading their inboxes for replies and bounces."""
from __future__ import annotations

import email
import email.header
import imaplib
import logging
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path

import requests

from .config import Config, Mailbox

log = logging.getLogger("clientbot")


def footer(cfg: Config) -> str:
    """Signature + the two things US law (CAN-SPAM) requires in every commercial email: a postal address and a way to opt out."""
    a = cfg.agency
    lines = [a.owner_name, f"{a.owner_title}, {a.name}" if a.owner_title else a.name]
    lines += [x for x in (a.phone, a.website) if x]
    return ("\n\n" + "\n".join(lines) + f"\n\n{a.business_address}\n"
            "Not the right fit? Reply \"no\" and I won't email you again.")


class Mailer:
    """SMTP through each mailbox (Google Workspace, Microsoft 365, Zoho...). A mailbox without smtp_host writes .eml files
    to the outbox folder instead, so you can read exactly what would go out."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sent: list[EmailMessage] = []

    def send(self, box: Mailbox, to: str, subject: str, body: str, in_reply_to: str = "") -> str:
        """Returns the Message-ID, or '' if sending failed."""
        a = self.cfg.agency
        msg = EmailMessage()
        msg["From"] = formataddr((a.owner_name, box.address))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=box.address.rsplit("@", 1)[-1] or None)
        msg["List-Unsubscribe"] = f"<mailto:{box.address}?subject=unsubscribe>"
        if in_reply_to:
            msg["In-Reply-To"] = msg["References"] = in_reply_to
        msg.set_content(body + footer(self.cfg))
        self.sent.append(msg)
        if not box.smtp_host:
            out = Path(self.cfg.outreach.outbox_dir)
            out.mkdir(parents=True, exist_ok=True)
            name = f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', to.lower())[:40]}-{len(self.sent)}.eml"
            (out / name).write_bytes(bytes(msg))
            log.info("email (outbox) to %s: %s", to, subject)
            return msg["Message-ID"]
        try:
            with smtplib.SMTP(box.smtp_host, box.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(box.smtp_user or box.address, box.password)
                smtp.send_message(msg)
            log.info("email from %s to %s: %s", box.address, to, subject)
            return msg["Message-ID"]
        except smtplib.SMTPRecipientsRefused:
            log.warning("email to %s refused by the server", to)
            return "refused"
        except (smtplib.SMTPException, OSError) as err:
            log.warning("email from %s to %s failed: %s", box.address, to, err)
            return ""


class Incoming:
    def __init__(self, message_id: str, sender: str, subject: str, body: str, auto: bool, bounce: bool, mentions: list[str]):
        self.message_id, self.sender, self.subject, self.body = message_id, sender, subject, body
        self.auto, self.bounce, self.mentions = auto, bounce, mentions


def parse(raw: bytes) -> Incoming:
    m = email.message_from_bytes(raw)
    sender = parseaddr(m.get("From", ""))[1].lower()
    subject = str(email.header.make_header(email.header.decode_header(m.get("Subject", ""))))
    parts = []
    for part in (m.walk() if m.is_multipart() else [m]):
        if part.get_content_type() in ("text/plain", "message/delivery-status", "text/rfc822-headers") or \
                (part.get_content_type() == "text/html" and not parts):
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
    body = "\n".join(parts)
    if "<html" in body.lower():
        body = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", body))
    bounce = bool(re.search(r"mailer-daemon|postmaster", sender) or
                  re.search(r"undeliverable|delivery status notification|mail delivery (failed|subsystem)|returned mail", subject, re.I))
    auto = bounce or m.get("Auto-Submitted", "no").lower() != "no" or bool(m.get("X-Autoreply") or m.get("X-Autorespond"))
    # quoted text below the reply is ours; keep only what they wrote
    reply = re.split(r"\n(On .{5,120} wrote:|-{2,} ?Original Message|From: .+\nSent: )", body)[0].strip()
    mentions = sorted({e.lower() for e in re.findall(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", body, re.I)}) if bounce else []
    return Incoming(m.get("Message-ID", "").strip(), sender, subject, reply[:6000], auto, bounce, mentions)


class Inbox:
    """Reads recent mail over IMAP without marking it read."""

    def fetch(self, box: Mailbox, days: int = 3) -> list[Incoming]:
        if not box.imap_host:
            return []
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
        out = []
        with imaplib.IMAP4_SSL(box.imap_host, box.imap_port) as imap:
            imap.login(box.smtp_user or box.address, box.password)
            imap.select("INBOX", readonly=True)
            _, ids = imap.search(None, f'(SINCE "{since}")')
            for num in ids[0].split()[-300:]:
                _, data = imap.fetch(num, "(BODY.PEEK[])")
                if data and isinstance(data[0], tuple):
                    out.append(parse(data[0][1]))
        return out


class Notifier:
    """Alerts for you: ntfy push (phone) and/or email."""

    def __init__(self, cfg: Config, mailer: Mailer):
        self.cfg, self.mailer = cfg, mailer

    def alert(self, title: str, body: str = "", urgent: bool = False) -> None:
        n = self.cfg.notify
        log.info("ALERT | %s | %s", title, body)
        if n.ntfy_topic:
            try:
                requests.post(f"{n.ntfy_server.rstrip('/')}/{n.ntfy_topic}", data=(body or title).encode(),
                              headers={"Title": title.encode("ascii", "replace").decode(), "Priority": "high" if urgent else "default",
                                       "Tags": "moneybag" if urgent else "envelope"}, timeout=10)
            except requests.RequestException as e:
                log.warning("ntfy failed: %s", e)
        if n.owner_email and (urgent or "digest" in title.lower()):
            box = self.cfg.mailboxes[0]
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = box.address, n.owner_email, f"[clientbot] {title}"
            msg.set_content(body or title)
            if not box.smtp_host:
                return
            try:
                with smtplib.SMTP(box.smtp_host, box.smtp_port, timeout=30) as smtp:
                    smtp.starttls()
                    smtp.login(box.smtp_user or box.address, box.password)
                    smtp.send_message(msg)
            except (smtplib.SMTPException, OSError) as e:
                log.warning("owner alert email failed: %s", e)

