from __future__ import annotations

import logging
import threading
import time

from .shop import Shop

log = logging.getLogger("shopbot")


def jobs(shop: Shop) -> list[tuple[str, float, callable]]:
    s = shop.cfg.schedule
    return [
        ("fulfill", s.fulfill_minutes * 60, shop.fulfill),
        ("track", s.tracking_minutes * 60, shop.track),
        ("followups", s.followup_minutes * 60, shop.followups),
        ("newsletter", 3600, shop.newsletter),        # checks its own weekday/hour and sends once a week
        ("sync", s.sync_hours * 3600, shop.sync),
        ("research", s.research_hours * 3600, shop.research),
    ]


def run_due(shop: Shop, now: float | None = None) -> list[str]:
    """Run every job whose interval has passed. Last-run times live in the database, so restarts don't re-run everything."""
    now = now or time.time()
    ran = []
    for name, every, fn in jobs(shop):
        row = shop.db.one("SELECT last_run FROM jobs WHERE name = ?", (name,))
        if row and now - row["last_run"] < every:
            continue
        shop.db.x("INSERT INTO jobs (name, last_run) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET last_run = excluded.last_run", (name, now))
        try:
            result = fn()
            log.info("job %s: %s", name, result)
            ran.append(name)
        except Exception as e:
            log.exception("job %s failed", name)
            shop.db.log("error", f"Job {name} failed: {e}")
            shop.notifier.alert(f"Job '{name}' failed", str(e), urgent=True)
    return ran


def loop(shop: Shop, stop: threading.Event | None = None, tick: float = 30) -> None:
    stop = stop or threading.Event()
    log.info("worker started")
    while not stop.is_set():
        run_due(shop)
        stop.wait(tick)
