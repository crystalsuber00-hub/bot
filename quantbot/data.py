"""Daily adjusted closes from Yahoo's free chart API, cached to data/prices/*.csv.

Every backtest in a run reads the same cached snapshot, so results are reproducible:
refresh the cache explicitly (``quantbot fetch``), not in the middle of a research run.
"""
from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import numpy as np
import requests

from .universe import BENCHMARK, all_symbols

log = logging.getLogger("quantbot")
PRICES = Path(__file__).resolve().parent.parent / "data" / "prices"
UA = {"User-Agent": "Mozilla/5.0"}


def fetch_symbol(symbol: str, rng: str = "5y") -> list[tuple[str, float]]:
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}",
                     params={"interval": "1d", "range": rng, "events": "div,split"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res.get("timestamp") or []
    ind = res["indicators"]
    closes = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
    out = []
    for t, c in zip(ts, closes):
        if c is not None:
            out.append((datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), float(c)))
    return out


def refresh(symbols: list[str] | None = None, rng: str = "5y", pause: float = 0.2) -> list[str]:
    """Download and overwrite the cache. Returns the symbols that failed."""
    PRICES.mkdir(parents=True, exist_ok=True)
    failed = []
    for s in symbols or all_symbols():
        try:
            rows = fetch_symbol(s, rng)
        except Exception as e:  # network, delisted, bad symbol
            log.warning("fetch %s failed: %s", s, e)
            failed.append(s)
            continue
        with open(PRICES / f"{s}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "adjclose"])
            w.writerows(rows)
        time.sleep(pause)
    return failed


@dataclass
class Panel:
    """Aligned price matrix: ``px[t, i]`` is the adjusted close of ``symbols[i]`` on ``dates[t]`` (NaN if not trading yet)."""
    dates: np.ndarray    # datetime64[D]
    symbols: list[str]
    px: np.ndarray

    def col(self, symbol: str) -> np.ndarray:
        return self.px[:, self.symbols.index(symbol)]

    def slice(self, end: str | np.datetime64 | None = None, start: str | np.datetime64 | None = None) -> "Panel":
        m = np.ones(len(self.dates), bool)
        if start is not None:
            m &= self.dates >= np.datetime64(start, "D")
        if end is not None:
            m &= self.dates <= np.datetime64(end, "D")
        return Panel(self.dates[m], self.symbols, self.px[m])


def load_panel(symbols: list[str] | None = None) -> Panel:
    """Build the panel from the cache, on the benchmark's trading calendar."""
    symbols = sorted(set(symbols or all_symbols()) | {BENCHMARK})
    series = {}
    for s in symbols:
        p = PRICES / f"{s}.csv"
        if not p.exists():
            continue
        with open(p) as f:
            series[s] = {row["date"]: float(row["adjclose"]) for row in csv.DictReader(f)}
    if BENCHMARK not in series:
        raise SystemExit(f"No cached prices for {BENCHMARK}. Run: python -m quantbot fetch")
    dates = sorted(series[BENCHMARK])
    syms = sorted(series)
    px = np.full((len(dates), len(syms)), np.nan)
    for j, s in enumerate(syms):
        d = series[s]
        last = np.nan
        gap = 0
        for i, day in enumerate(dates):
            if day in d:
                last, gap = d[day], 0
            else:
                gap += 1
                if gap > 5:  # stale for a week: treat as missing rather than carry forward
                    last = np.nan
            px[i, j] = last
    return Panel(np.array(dates, dtype="datetime64[D]"), syms, px)
