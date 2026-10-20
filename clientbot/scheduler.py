from __future__ import annotations

import logging
import threading
import time

from .agency import Agency

log = logging.getLogger("clientbot")


def jobs(agency: Agency) -> list[tuple[str, float, callable]]:
    return [
        ("send", 30, agency.send),              # checks its own window, daily limits and spacing
        ("inbox", 600, agency.inbox),
        ("audit", 1800, agency.audit),
        ("write", 1800, agency.write),
        ("refill", 6 * 3600, agency.refill),
        ("digest", 1800, agency.digest),        # once a day, after 7am
    ]


def run_due(agency: Agency, now: float | None = None) -> list[str]:
    """Run every job whose interval has passed. Last-run times live in the database, so restarts don't re-run everything."""
    now = now or time.time()
    ran = []
    for name, every, fn in jobs(agency):
        row = agency.db.one("SELECT last_run FROM jobs WHERE name = ?", (name,))
        if row and now - row["last_run"] < every:
            continue
        agency.db.x("INSERT INTO jobs (name, last_run) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET last_run = excluded.last_run", (name, now))
        try:
            result = fn()
            if name != "send" or result not in ("nothing due", "outside the sending window"):
                log.info("job %s: %s", name, result)
            ran.append(name)
        except Exception as e:
            log.exception("job %s failed", name)
            agency.db.log("error", f"Job {name} failed: {e}")
            agency.notifier.alert(f"Job '{name}' failed", str(e), urgent=True)
    return ran


def loop(agency: Agency, stop: threading.Event | None = None, tick: float = 15) -> None:
    stop = stop or threading.Event()
    log.info("worker started")
    while not stop.is_set():
        run_due(agency)
        stop.wait(tick)
