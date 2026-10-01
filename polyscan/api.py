"""Thin client for Polymarket's public (no-auth) data endpoints."""
from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger("polyscan")

DATA = "https://data-api.polymarket.com"
PNL = "https://user-pnl-api.polymarket.com"

LEADERBOARD_PAGE = 50          # the API silently caps limit at 50
ACTIVITY_PAGE = 500
ACTIVITY_MAX_OFFSET = 5000     # deeper offsets are rejected; slide the end time instead
CLOSED_PAGE = 50


class Client:
    def __init__(self, min_interval: float = 0.05, retries: int = 4, timeout: float = 30):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "polyscan/0.1"
        self.min_interval, self.retries, self.timeout = min_interval, retries, timeout
        self._lock, self._last = threading.Lock(), 0.0

    def get(self, url: str, **params):
        for attempt in range(self.retries + 1):
            with self._lock:  # global pacing across threads keeps us under the rate limit
                wait = self._last + self.min_interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                r = self.s.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                if r.status_code >= 400:  # bad request: retrying won't help
                    log.debug("%s %s -> %s %s", url, params, r.status_code, r.text[:200])
                    return None
                return r.json()
            except (requests.RequestException, ValueError) as e:
                if attempt == self.retries:
                    log.warning("giving up on %s %s: %s", url, params, e)
                    return None
                time.sleep(min(2 ** attempt, 16))

    # -- endpoints -------------------------------------------------------------

    def leaderboard(self, period: str, order_by: str, depth: int, category: str = "OVERALL") -> list[dict]:
        """period: DAY | WEEK | MONTH | ALL; order_by: PNL | VOL."""
        out = []
        for off in range(0, depth, LEADERBOARD_PAGE):
            page = self.get(f"{DATA}/v1/leaderboard", timePeriod=period, orderBy=order_by,
                            limit=LEADERBOARD_PAGE, offset=off, category=category) or []
            out += page
            if len(page) < LEADERBOARD_PAGE:
                break
        return out

    def pnl_series(self, wallet: str) -> list[dict]:
        """Daily cumulative P&L (realized + mark-to-market), [{"t": unix, "p": usd}, ...]."""
        return self.get(f"{PNL}/user-pnl", user_address=wallet, interval="all", fidelity="1d") or []

    def closed_positions(self, wallet: str, since: int, cap: int) -> list[dict]:
        """Closed positions newest first, stopping at `since` (unix) or `cap` rows."""
        out = []
        for off in range(0, cap, CLOSED_PAGE):
            page = self.get(f"{DATA}/closed-positions", user=wallet, limit=CLOSED_PAGE, offset=off,
                            sortBy="TIMESTAMP", sortDirection="DESC") or []
            out += [p for p in page if p.get("timestamp", 0) >= since]
            if len(page) < CLOSED_PAGE or page[-1].get("timestamp", 0) < since:
                break
        return out

    def positions(self, wallet: str) -> list[dict]:
        out = []
        for off in range(0, 2000, 500):
            page = self.get(f"{DATA}/positions", user=wallet, limit=500, offset=off, sizeThreshold=1) or []
            out += page
            if len(page) < 500:
                break
        return out

    def trades(self, wallet: str, since: int, cap: int, end: int | None = None) -> list[dict]:
        """TRADE activity newest first within [since, end], at most `cap` rows."""
        out, end = [], end or int(time.time())
        while len(out) < cap:
            off, got_any, oldest = 0, False, end
            while off < ACTIVITY_MAX_OFFSET and len(out) < cap:
                page = self.get(f"{DATA}/activity", user=wallet, type="TRADE", limit=ACTIVITY_PAGE, offset=off,
                                start=since, end=end, sortBy="TIMESTAMP", sortDirection="DESC") or []
                out += page
                got_any = got_any or bool(page)
                if page:
                    oldest = page[-1]["timestamp"]
                if len(page) < ACTIVITY_PAGE:
                    return out[:cap]
                off += ACTIVITY_PAGE
            if not got_any or oldest <= since:
                break
            end = oldest - 1  # hit the offset ceiling: continue from the oldest row seen
        return out[:cap]

    def recent_activity(self, wallet: str, limit: int = 50) -> list[dict]:
        return self.get(f"{DATA}/activity", user=wallet, type="TRADE", limit=limit, sortBy="TIMESTAMP",
                        sortDirection="DESC") or []
