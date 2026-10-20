from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

# Lead stages, in funnel order. "Dead ends" are kept so a business is never contacted twice.
# "call" = a good fit with a phone number but no email address: goes on the call / walk-in list.
STAGES = ["new", "audited", "drafted", "queued", "contacted", "replied", "meeting", "proposal", "won",
          "call", "finished", "lost", "unsubscribed", "bounced", "skipped"]
DONE = ("won", "lost", "unsubscribed", "bounced", "skipped")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    name TEXT NOT NULL,
    niche TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    contact_name TEXT NOT NULL DEFAULT '',
    rating REAL,
    reviews INTEGER,
    audit TEXT NOT NULL DEFAULT '{}',
    score INTEGER NOT NULL DEFAULT 0,
    score_reasons TEXT NOT NULL DEFAULT '[]',
    stage TEXT NOT NULL DEFAULT 'new',
    stage_reason TEXT NOT NULL DEFAULT '',
    mailbox TEXT NOT NULL DEFAULT '',
    step INTEGER NOT NULL DEFAULT 0,          -- emails sent so far
    next_send_at REAL,
    thread_id TEXT NOT NULL DEFAULT '',       -- Message-ID of email 1, so follow-ups thread under it
    subject TEXT NOT NULL DEFAULT '',
    replied_at REAL,
    reply_kind TEXT NOT NULL DEFAULT '',
    reply_text TEXT NOT NULL DEFAULT '',
    suggested_reply TEXT NOT NULL DEFAULT '',
    call_script TEXT NOT NULL DEFAULT '',
    mrr REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (source, source_id)
);
CREATE TABLE IF NOT EXISTS drafts (
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    step INTEGER NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (lead_id, step)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL,
    direction TEXT NOT NULL,                  -- out | in
    kind TEXT NOT NULL,                       -- email | reply | bounce | auto
    mailbox TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS suppressions (
    value TEXT PRIMARY KEY,                   -- an email address, or @domain
    reason TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS searches (
    niche TEXT NOT NULL,
    city TEXT NOT NULL,
    provider TEXT NOT NULL,
    found INTEGER NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (niche, city, provider)
);
CREATE TABLE IF NOT EXISTS seen_mail (
    message_id TEXT PRIMARY KEY,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    name TEXT PRIMARY KEY,
    last_run REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage, score);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS idx_messages_mailbox ON messages(mailbox, direction, ts);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def q(self, sql: str, args=()) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args=()) -> int:
        with self.conn() as c:
            return c.execute(sql, args).lastrowid

    def claim(self, sql: str, args=()) -> bool:
        """Run a conditional UPDATE; True if it changed a row (makes stage transitions idempotent)."""
        with self.conn() as c:
            return c.execute(sql, args).rowcount > 0

    def log(self, kind: str, message: str) -> None:
        self.x("INSERT INTO events (ts, kind, message) VALUES (?, ?, ?)", (time.time(), kind, message))

    def update(self, lead_id: int, **cols) -> None:
        cols["updated_at"] = time.time()
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.x(f"UPDATE leads SET {sets} WHERE id = ?", (*cols.values(), lead_id))

    def lead(self, lead_id: int) -> dict | None:
        return self.one("SELECT * FROM leads WHERE id = ?", (lead_id,))

    def suppressed(self, email: str) -> bool:
        email = email.lower().strip()
        domain = "@" + email.rsplit("@", 1)[-1]
        return self.one("SELECT 1 FROM suppressions WHERE value IN (?, ?)", (email, domain)) is not None

    def suppress(self, value: str, reason: str) -> None:
        self.x("INSERT OR IGNORE INTO suppressions (value, reason, ts) VALUES (?, ?, ?)",
               (value.lower().strip(), reason, time.time()))

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.x("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def jdump(v) -> str:
    return json.dumps(v, separators=(",", ":"))
