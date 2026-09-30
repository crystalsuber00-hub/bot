"""Hourly candles from Coinbase Exchange's public API, cached as CSV under data/."""
from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.exchange.coinbase.com/products/{product}/candles"
DATA_DIR = Path("data")
MAX_PER_REQUEST = 300


def cache_path(product: str, granularity: int) -> Path:
    return DATA_DIR / f"crypto_{product}_{granularity}.csv"


def load_cache(product: str, granularity: int) -> list[tuple[int, float]]:
    path = cache_path(product, granularity)
    if not path.exists():
        return []
    with path.open() as f:
        return [(int(r["time"]), float(r["close"])) for r in csv.DictReader(f)]


def _save(product: str, granularity: int, rows: list[tuple[int, float]]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with cache_path(product, granularity).open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "close"])
        w.writerows(rows)


def fetch_range(product: str, granularity: int, start: int, end: int) -> list[tuple[int, float]]:
    out: dict[int, float] = {}
    step = MAX_PER_REQUEST * granularity
    t = start
    while t < end:
        chunk_end = min(t + step, end)
        params = {
            "granularity": granularity,
            "start": datetime.fromtimestamp(t, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(chunk_end, timezone.utc).isoformat(),
        }
        for attempt in range(4):
            r = requests.get(API.format(product=product), params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            break
        else:
            raise RuntimeError("Coinbase rate limit: gave up")
        for ts, _low, _high, _open, close, _vol in r.json():
            out[int(ts)] = float(close)
        t = chunk_end
        time.sleep(0.15)
    return sorted(out.items())


def get_candles(product: str, granularity: int = 3600, days: int = 730,
                refresh: bool = True) -> list[tuple[int, float]]:
    """Cached (time, close) rows, oldest first. With refresh, tops the cache up to now."""
    rows = load_cache(product, granularity)
    now = int(time.time())
    if refresh:
        start = rows[-1][0] + granularity if rows else now - days * 86400
        new = fetch_range(product, granularity, start, now)
        merged = dict(rows)
        merged.update(new)
        rows = sorted(merged.items())
        _save(product, granularity, rows)
    return rows
