"""Backtest the 0DTE stock signals on REAL historical option prices from Massive (formerly Polygon).

The live engine is replayed minute by minute over past days. Stock 5-min bars come from Massive stock aggregates;
option prices are the real 1-minute trade prices of each contract that day. Massive's Starter data has trade prices,
not bid/ask quotes, so the bid/ask is the traded price minus/plus an assumed half-spread (ETFs 1%, stocks 2.5%,
at least $0.01/$0.02); delta is computed from the option's own price (implied volatility). Everything is cached in
data/massive/, so re-runs are fast and cost no API calls.

    export MASSIVE_API_KEY=...
    python -m spxbot.massive_backtest --check                         # what the key can access
    python -m spxbot.massive_backtest --start 2024-10-01 --end 2026-09-29 --workers 4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from .backtest import bs
from .config import Config, Stocks
from .models import Bar, OptionQuote, Quote
from .stocks import StocksEngine, StocksState

log = logging.getLogger("spxbot")
NY = ZoneInfo("America/New_York")
API = "https://api.massive.com"
CACHE = Path("data/massive")
ETFS = {"SPY", "QQQ", "IWM", "DIA"}


class MassiveError(RuntimeError):
    pass


class MassiveAPI:
    def __init__(self, key: str, base: str = API, cache: Path = CACHE, session=None):
        self.key, self.base, self.cache = key, base.rstrip("/"), cache
        self.s = session or requests.Session()
        self.calls = 0

    def _get(self, url: str, params: Optional[dict] = None) -> dict:
        params = dict(params or {})
        params["apiKey"] = self.key
        for attempt in range(8):
            r = self.s.get(url, params=params, timeout=60)
            self.calls += 1
            if r.status_code == 429 or r.status_code >= 500:  # rate limit (free tiers: 5/min) or server hiccup
                time.sleep(min(60, 13 * (attempt + 1)))
                continue
            if r.status_code in (401, 403):
                raise MassiveError(f"{r.status_code} for {url.split('?')[0]}: your plan doesn't include this data "
                                   f"({' '.join(r.text.split())[:160]})")
            r.raise_for_status()
            return r.json()
        raise MassiveError(f"gave up after repeated rate limits/errors: {url}")

    def _paged(self, path: str, params: dict) -> list[dict]:
        out, data = [], self._get(self.base + path, params)
        out += data.get("results") or []
        while data.get("next_url"):
            data = self._get(data["next_url"])
            out += data.get("results") or []
        return out

    def _cached(self, key: str, fetch) -> list:
        f = self.cache / (hashlib.sha1(key.encode()).hexdigest() + ".json")
        if f.exists():
            return json.loads(f.read_text())
        rows = fetch()
        self.cache.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rows))
        return rows

    def aggs(self, ticker: str, mult: int, span: str, frm: str, to: str) -> list[dict]:
        key = f"aggs|{ticker}|{mult}|{span}|{frm}|{to}"
        return self._cached(key, lambda: self._paged(
            f"/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{frm}/{to}",
            {"adjusted": "true", "sort": "asc", "limit": 50000}))

    def contracts(self, underlying: str, expiration: str, right: str) -> list[dict]:
        key = f"contracts|{underlying}|{expiration}|{right}"
        return self._cached(key, lambda: self._paged(
            "/v3/reference/options/contracts",
            {"underlying_ticker": underlying, "expiration_date": expiration, "contract_type": right,
             "expired": "true" if expiration < date.today().isoformat() else "false", "limit": 1000}))


def occ(underlying: str, expiration: str, right: str, strike: float) -> str:
    return f"O:{underlying}{expiration[2:4]}{expiration[5:7]}{expiration[8:10]}{'C' if right == 'call' else 'P'}" \
           f"{int(round(strike * 1000)):08d}"


def parse_occ(t: str) -> tuple[str, str, str, float]:
    body = t[2:]
    i = next(i for i, ch in enumerate(body) if ch.isdigit())
    und, ymd, cp, k = body[:i], body[i:i + 6], body[i + 6], body[i + 7:]
    return und, f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}", "call" if cp == "C" else "put", int(k) / 1000


def implied_vol(price: float, S: float, K: float, T: float, right: str) -> Optional[float]:
    intrinsic = max(S - K, 0) if right == "call" else max(K - S, 0)
    if T <= 0 or price <= intrinsic + 1e-4:
        return None
    lo, hi = 0.01, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs(S, K, T, mid, right)[0] > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


class HistoryFeed:
    """Broker interface for StocksEngine, answered from Massive history as of `self.now`."""

    def __init__(self, api: MassiveAPI, symbols: list[str], start: str, end: str, strikes_each_side: int = 12):
        self.api, self.now, self.n = api, None, strikes_each_side
        self.bars: dict[str, dict[str, list[Bar]]] = {}
        frm = (date.fromisoformat(start) - timedelta(days=10)).isoformat()
        for sym in symbols:
            days: dict[str, list[Bar]] = {}
            for r in api.aggs(sym, 5, "minute", frm, end):
                t = datetime.fromtimestamp(r["t"] / 1000, NY).strftime("%Y-%m-%dT%H:%M")
                if "04:00" <= t[11:] < "16:00":
                    days.setdefault(t[:10], []).append(Bar(t, r["o"], r["h"], r["l"], r["c"]))
            self.bars[sym] = days
        self._minutes: dict[tuple[str, str], list[tuple[int, float]]] = {}

    def trading_days(self, start: str, end: str) -> list[str]:
        ref = self.bars.get("SPY") or next(iter(self.bars.values()))
        return [d for d in sorted(ref) if start <= d <= end and any(b.time[11:] == "09:30" for b in ref[d])]

    # --- stock data ---
    def get_bars(self, sym, day, minutes):
        return list(self.bars.get(sym, {}).get(day, []))

    def _spot(self, sym) -> float:
        cut = (self.now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")  # last completed 5-min bar
        done = [b for b in self.bars.get(sym, {}).get(self.now.date().isoformat(), []) if b.time <= cut]
        return done[-1].close if done else 0.0

    def get_quote(self, sym):
        return Quote(self._spot(sym))

    def get_daily(self, sym, start, end):
        out = []
        for d, bars in sorted(self.bars.get(sym, {}).items()):
            rth = [b for b in bars if b.time[11:] >= "09:30"]
            if start <= d <= end and rth:
                out.append(Bar(d, rth[0].open, max(b.high for b in rth), min(b.low for b in rth), rth[-1].close))
        return out

    # --- options ---
    def get_expirations(self, sym):
        day = self.now.date().isoformat()
        has = self.api.contracts(sym, day, "call")
        return [day] if has else []

    def _price(self, ticker: str) -> Optional[float]:
        """Last traded price at or before now (within 15 minutes), from real 1-minute option bars."""
        day = self.now.date().isoformat()
        key = (ticker, day)
        if key not in self._minutes:
            rows = self.api.aggs(ticker, 1, "minute", day, day)
            self._minutes[key] = [(r["t"] // 1000, r["c"]) for r in rows]
        ts = self.now.timestamp() - 60  # a bar is known once its minute has closed
        last = [(t, c) for t, c in self._minutes[key] if t <= ts]
        if not last or ts - last[-1][0] > 15 * 60:
            return None
        return last[-1][1]

    def _quote(self, ticker: str) -> Optional[OptionQuote]:
        und, exp, right, k = parse_occ(ticker)
        px = self._price(ticker)
        S = self._spot(und)
        if px is None or not S:
            return None
        half = max(0.01, px * 0.01) if und in ETFS else max(0.02, px * 0.025)
        mins = max(1, 16 * 60 - (self.now.hour * 60 + self.now.minute))
        T = mins / (252 * 390)
        iv = implied_vol(px, S, k, T, right)
        if iv:
            delta = bs(S, k, T, iv, right)[1]
        else:  # priced at intrinsic value: deep in the money (delta ~1) or worthless
            itm = S > k if right == "call" else S < k
            delta = (1.0 if right == "call" else -1.0) if itm else 0.0
        return OptionQuote(ticker, k, right, max(px - half, 0.0), px + half, delta)

    def get_chain(self, sym, exp, root=None, right=None, near=None):
        S = self._spot(sym)
        out = []
        for rt in ([right] if right else ["call", "put"]):
            strikes = sorted({c["strike_price"] for c in self.api.contracts(sym, exp, rt)})
            if not strikes or not S:
                continue
            below = [k for k in strikes if k <= S][-self.n:]
            above = [k for k in strikes if k > S][:self.n]
            for k in below + above:
                q = self._quote(occ(sym, exp, rt, k))
                if q:
                    out.append(q)
        return out

    def get_option_quotes(self, symbols):
        return {s: q for s in symbols if (q := self._quote(s))}


class Quiet:
    def send(self, text, payload=None):
        pass


def replay(api: MassiveAPI, k: Stocks, start: str, end: str, state_file: str, tick: int = 60) -> StocksState:
    feed = HistoryFeed(api, sorted(set(k.watchlist) | set(k.market_symbols)), start, end)
    cfg = Config(model="stocks")
    cfg.stocks = replace(k, state_file=state_file)
    eng = StocksEngine(cfg, feed, Quiet(), StocksState(state_file))
    for d in feed.trading_days(start, end):
        t = datetime.fromisoformat(f"{d}T09:30:05").replace(tzinfo=NY)
        while t.strftime("%H:%M") <= "16:01":
            feed.now = t
            try:
                eng.tick(t)
            except Exception:
                log.exception("tick %s failed", t)
            t += timedelta(seconds=tick)
        log.warning("%s done (%d API calls so far)", d, api.calls)
    return eng.state


def check(api: MassiveAPI) -> None:
    """Probe what the key can access, before a long run."""
    day = (date.today() - timedelta(days=7))
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    d = day.isoformat()
    for name, fn in (("stock 5-min bars (SPY)", lambda: api.aggs("SPY", 5, "minute", d, d)),
                     ("stock 5-min bars 2 years ago (SPY)",
                      lambda: api.aggs("SPY", 5, "minute", (day - timedelta(days=700)).isoformat(),
                                       (day - timedelta(days=695)).isoformat())),
                     ("option contract list (SPY 0DTE)", lambda: api.contracts("SPY", d, "call")),
                     ("option 1-min prices", lambda: api.aggs(occ("SPY", d, "call", round(
                         api.aggs("SPY", 1, "day", d, d)[0]["c"])), 1, "minute", d, d))):
        try:
            rows = fn()
            print(f"[{'ok' if rows else '--'}] {name}: {len(rows)} rows")
        except Exception as e:
            print(f"[!!] {name}: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="spxbot.massive_backtest")
    ap.add_argument("--key", default=os.environ.get("MASSIVE_API_KEY", ""))
    ap.add_argument("--check", action="store_true", help="show what the key can access, then exit")
    ap.add_argument("--start", default=(date.today() - timedelta(days=365)).isoformat())
    ap.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat())
    ap.add_argument("-c", "--config", help="use the [stocks] settings from this config file")
    ap.add_argument("--state", default="data/massive_backtest.json", help="where to write the simulated trades")
    ap.add_argument("--tick", type=int, default=60, help="seconds between simulated polls")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    if not a.key:
        ap.error("set MASSIVE_API_KEY or pass --key")
    api = MassiveAPI(a.key)
    if a.check:
        return check(api)
    k = Stocks()
    if a.config:
        from .config import load_config
        k = load_config(a.config).stocks
    Path(a.state).unlink(missing_ok=True)
    state = replay(api, k, a.start, a.end, a.state, a.tick)
    from .stocks_report import build_report
    print(build_report(state))
    print(f"\n{api.calls} API calls. Trades saved in {a.state}.")


if __name__ == "__main__":
    main()
