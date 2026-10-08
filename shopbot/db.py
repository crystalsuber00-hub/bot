from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    supplier TEXT NOT NULL,
    supplier_pid TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    bullets TEXT NOT NULL DEFAULT '[]',
    seo_description TEXT NOT NULL DEFAULT '',
    keyword TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    images TEXT NOT NULL DEFAULT '[]',
    ship_cost REAL NOT NULL DEFAULT 0,
    logistic_name TEXT NOT NULL DEFAULT '',
    ship_days TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    inactive_reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (supplier, supplier_pid)
);
CREATE TABLE IF NOT EXISTS variants (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    supplier_vid TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    cost REAL NOT NULL,
    price REAL NOT NULL,
    compare_at REAL NOT NULL DEFAULT 0,
    stock INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (product_id, supplier_vid)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '{}',
    subtotal REAL NOT NULL DEFAULT 0,
    amount_paid REAL NOT NULL DEFAULT 0,
    stripe_session_id TEXT NOT NULL DEFAULT '',
    payment_intent TEXT NOT NULL DEFAULT '',
    supplier_order_id TEXT NOT NULL DEFAULT '',
    supplier_status TEXT NOT NULL DEFAULT '',
    supplier_cost REAL NOT NULL DEFAULT 0,
    tracking_number TEXT NOT NULL DEFAULT '',
    tracking_url TEXT NOT NULL DEFAULT '',
    carrier TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    marketing_consent INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    paid_at REAL,
    ordered_at REAL,
    shipped_at REAL,
    delivered_at REAL,
    followup_sent_at REAL
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL,
    variant_id INTEGER NOT NULL,
    supplier_vid TEXT NOT NULL,
    title TEXT NOT NULL,
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    cost REAL NOT NULL,
    ship_cost REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subscribers (
    email TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    unsubscribed_at REAL
);
CREATE TABLE IF NOT EXISTS emails (
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    sent_at REAL NOT NULL,
    PRIMARY KEY (kind, ref)
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
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_variants_product ON variants(product_id);
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
        """Run a conditional UPDATE; True if it changed a row (used to make state transitions idempotent)."""
        with self.conn() as c:
            return c.execute(sql, args).rowcount > 0

    def log(self, kind: str, message: str) -> None:
        self.x("INSERT INTO events (ts, kind, message) VALUES (?, ?, ?)", (time.time(), kind, message))

    def mark_email(self, kind: str, ref: str, to: str) -> bool:
        """Record that an email is going out; False if this (kind, ref) was already sent."""
        try:
            self.x("INSERT INTO emails (kind, ref, to_addr, sent_at) VALUES (?, ?, ?, ?)", (kind, ref, to, time.time()))
            return True
        except sqlite3.IntegrityError:
            return False

    def unmark_email(self, kind: str, ref: str) -> None:
        self.x("DELETE FROM emails WHERE kind = ? AND ref = ?", (kind, ref))


def jdump(v) -> str:
    return json.dumps(v, separators=(",", ":"))
