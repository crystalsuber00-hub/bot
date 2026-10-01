"""Replay copy trading over a past window from recorded data, so nothing has to run live.

1. Pick the wallets the way `watch --rank profit` would have on the window's first day, using only
   data from before it (90-day P&L ending that day; bots and wallets idle for 14 days left out).
2. Rebuild the alerts the watcher would have sent from those wallets' trade history.
3. Feed them to the real CopyTrader in paper mode on a simulated clock. Polymarket US prices come
   from its minute-level price history one poll (60s) after each whale fill; positions settle at the
   market's recorded result once the game would have finished.

Limits: price history gives the best ask/bid, not depth, so fills assume a small order fits at the
top of the book (true for $2 bets in game-winner markets). A game is treated as finished four hours
after its scheduled start, which only affects when the daily stop sees the result.
"""
from __future__ import annotations

import json
import logging
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import metrics as m
from .api import Client
from .scan import candidate_pool
from .ustrade import CopyTrader, USClient

log = logging.getLogger("polyscan")
DAY = 86400
GAME_HOURS = 4

VARIANTS = {
    "as built: first side only, 3c limit": {},
    "plus second side bought by hand": {"paper_short": True},
    "loose: both sides, 10c limit, any price": {"paper_short": True, "max_slip": 0.10, "lo": 0.01, "hi": 0.99},
    "copy everything, no price limit": {"paper_short": True, "max_slip": 1.0, "lo": 0.0, "hi": 1.0},
}


def watchlist_asof(api: Client, asof: float, top: int, workers: int = 8) -> list[dict]:
    pool = candidate_pool(api, 1000, 500)
    with ThreadPoolExecutor(workers) as ex:
        series = dict(zip(pool, ex.map(api.pnl_series, pool)))
    ranked = sorted(((m.series_metrics(s, asof, 90)["pnl"], w) for w, s in series.items() if s), reverse=True)
    picks = []
    for pnl, w in ranked:
        if pnl <= 0 or len(picks) >= top:
            break
        trades = api.trades(w, int(asof - 30 * DAY), 3000, end=int(asof))
        tr = m.trade_metrics(trades, capped=len(trades) >= 3000, now=asof)
        if m.is_bot(tr) or m.dormant(tr):
            continue
        picks.append({"wallet": w, "name": pool[w]["name"], "pnl": pnl})
        log.info("watchlist as of %s: %-22s 90d $%s", _d(asof), pool[w]["name"], f"{pnl:,.0f}")
    return picks


def signals(api: Client, watch: list[dict], start: float, end: float, min_usd: float) -> list[dict]:
    """The alerts the watcher would have produced: fills added up per wallet, outcome, side and minute."""
    out = []
    for w in watch:
        agg = defaultdict(lambda: {"usd": 0.0, "shares": 0.0})
        for r in api.trades(w["wallet"], int(start), 50000, end=int(end)):
            a = agg[(r.get("side"), r.get("asset"), r["timestamp"] // 60)]
            a["usd"] += float(r.get("usdcSize", 0))
            a["shares"] += float(r.get("size", 0))
            a.update(outcome=r.get("outcome", ""), slug=r.get("eventSlug", ""), market=r.get("slug", ""),
                     title=r.get("title", ""), t=max(a.get("t", 0), r["timestamp"]))
        for (side, asset, _), a in agg.items():
            if a["usd"] >= min_usd and a["shares"]:
                out.append({**a, "side": side, "asset": asset, "price": a["usd"] / a["shares"],
                            "wallet": w["wallet"], "name": w["name"]})
    return sorted(out, key=lambda a: a["t"])


class HistoryUS:
    """Stands in for USClient: real Polymarket US listings, prices as of the simulated time."""
    authed = False

    def __init__(self, us: USClient | None = None, delay: float = 60, cache: str | None = None):
        self.us, self.delay, self.now = us or USClient(), delay, 0.0
        self.path = Path(cache) if cache else None
        c = json.loads(self.path.read_text()) if self.path and self.path.exists() else {}
        self.events, self.hist = c.get("events", {}), c.get("hist", {})
        self.settled = {k: v for k, v in c.get("settled", {}).items() if v is not None}
        self._tried: dict[str, float] = {}

    def save(self):
        if self.path:
            self.path.write_text(json.dumps({"events": self.events, "hist": self.hist, "settled": self.settled}))

    def _get(self, fn, *a, **kw):
        time.sleep(0.1)  # public API allows 20 requests a second; stay well under it
        return fn(*a, **kw)

    def event(self, slug: str):
        if slug not in self.events:
            ev = self._get(self.us.event, slug)
            # keep only what the replay uses; a single NFL game lists ~700 markets
            self.events[slug] = ev and {**{k: ev.get(k) for k in ("slug", "startDate")}, "markets": [
                mk for mk in ev.get("markets") or [] if mk.get("slug") == f"aec-{slug}"
                or str(mk.get("sportsMarketType", "")).endswith(("_full_game_winner", "_match_winner"))]}
        return self.events[slug]

    def end_time(self, market: str) -> float:
        ev = self.events.get(market[4:] if market.startswith("aec-") else market) or {}
        try:
            start = datetime.fromisoformat(ev["startDate"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError, AttributeError):
            return float("inf")
        return start + GAME_HOURS * 3600

    def _quote(self, market: str, t: float):
        key = f"{market}|{int(t // 600)}"
        if key not in self.hist:  # one 15-minute window per 10-minute slot
            a = int(t // 600 * 600)
            d = self._get(self.us.public, "/v1/price-history", symbol=market, fidelity=1,
                          **{"timestamp.startTimestamp": a, "timestamp.endTimestamp": a + 900})
            if d is None:  # request failed: don't remember it as "no prices"
                return None
            self.hist[key] = [[h["timestamp"], h.get("longPrice"), h.get("shortPrice")] for h in d.get("history") or []]
        pts = [p for p in self.hist[key] if p[0] >= t and p[1] is not None]
        return pts[0] if pts and pts[0][0] - t <= 300 else None

    def bbo(self, market: str):
        if self.now >= self.end_time(market):
            return {"state": "MARKET_STATE_EXPIRED"}
        q = self._quote(market, self.now)
        if not q:
            return {"state": "MARKET_STATE_OPEN"}
        _, long_px, short_px = q
        return {"state": "MARKET_STATE_OPEN", "longQuote": {"value": long_px}, "shortQuote": {"value": short_px},
                "bestBid": {"value": 1 - short_px if short_px is not None else 0}}

    def settlement(self, market: str):
        if self.now < self.end_time(market):
            return None
        if market not in self.settled:
            if self.now - self._tried.get(market, -1e18) < 3600:  # ask at most once per simulated hour
                return None
            self._tried[market] = self.now
            v = self._get(self.us.settlement, market)
            if v is None:  # not settled yet, or the lookup failed: ask again next time
                return None
            self.settled[market] = v
        return self.settled[market]


REASONS = [
    (": bought", "copied"), ("only game-winner", "spread, total or prop"), ("not listed", "game not on Polymarket US"),
    ("no single game-winner", "no game-winner market"), ("couldn't tell", "team name unclear"),
    ("second side", "second side (manual)"), ("above your", "price moved too far"), ("outside", "favorite or long shot"),
    ("no seller", "no US price at that minute"), ("minimum size", "under exchange minimum"),
    ("already holding", "already holding"), ("open bets", "money tied up in open bets"),
    ("losses today", "daily stop"), ("today (limit", "daily stop"), ("halted", "30% halt"),
    ("market is", "game already over"), ("down to", "account empty"),
]


def _reason(note: str) -> str:
    return next((r for k, r in REASONS if k in note), note[:40])


def simulate(alerts: list[dict], hus: HistoryUS, bankroll: float, variant: dict, settle_until: float) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tr = CopyTrader(hus, f"{tmp}/s.json", bankroll, auto=True, clock=lambda: hus.now,
                        stop_file=f"{tmp}/STOP", **variant)
        reasons, curve = Counter(), []
        for a in alerts:
            hus.now = a["t"] + hus.delay
            tr.refresh()
            if a["side"] == "BUY":
                reasons[_reason(tr.on_alert(a))] += 1
            else:
                tr.on_alert(a)
            curve.append(tr.realized())
        hus.now = settle_until
        tr.refresh()
        closed = tr.state["closed"]
    peak, dd = 0.0, 0.0
    for v in curve + [tr.realized()]:
        peak, dd = max(peak, v), max(dd, peak - v)
    by_wallet = defaultdict(float)
    for c in closed:
        by_wallet[c.get("wallet")] += c["pnl"]
    staked = sum(c["cost"] for c in closed)
    return {"signals": sum(reasons.values()), "reasons": dict(reasons.most_common()), "trades": len(closed),
            "wins": sum(c["pnl"] > 0 for c in closed), "pnl": round(tr.realized(), 2), "staked": round(staked, 2),
            "roi": round(tr.realized() / staked, 4) if staked else 0.0, "max_drawdown": round(dd, 2),
            "open_unsettled": len(tr.state["positions"]), "halted": tr.state["halted"],
            "by_wallet": {k: round(v, 2) for k, v in by_wallet.items()}, "closed": closed}


def run(start: float, end: float, top: int = 15, bankroll: float = 100, min_usd: float = 1000,
        delay: float = 60, out: str = "polyscan_out", watchlist: list[dict] | None = None) -> dict:
    api, outp = Client(), Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    watch = watchlist or watchlist_asof(api, start, top)
    alerts = signals(api, watch, start, end, min_usd)
    log.info("%d alerts from %d wallets between %s and %s", len(alerts), len(watch), _d(start), _d(end))
    hus = HistoryUS(delay=delay, cache=str(outp / "backtest_cache.json"))
    results = {}
    for name, v in VARIANTS.items():
        results[name] = simulate(alerts, hus, bankroll, v, settle_until=time.time())
        hus.save()
        r = results[name]
        log.info("%-42s %3d trades  %3d won  P&L %+7.2f  ROI %+.1f%%", name, r["trades"], r["wins"], r["pnl"], 100 * r["roi"])
    res = {"start": start, "end": end, "bankroll": bankroll, "min_usd": min_usd, "delay": delay,
           "watchlist": watch, "alerts": len(alerts), "buy_alerts": sum(a["side"] == "BUY" for a in alerts),
           "results": results, "generated": int(time.time())}
    (outp / f"backtest_{_d(start)}_{_d(end)}.json").write_text(json.dumps(res, indent=1))
    return res


def _d(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def report(res: dict) -> str:
    names = {w["wallet"]: w["name"] for w in res["watchlist"]}
    lines = [f"Copy-trading replay {_d(res['start'])} to {_d(res['end'])} · ${res['bankroll']:.0f} start, "
             f"2% bets · {len(res['watchlist'])} wallets · {res['buy_alerts']} buy alerts of ${res['min_usd']:,.0f}+", ""]
    for name, r in res["results"].items():
        lines.append(f"{name}")
        lines.append(f"  {r['trades']} trades, {r['wins']} won · P&L {r['pnl']:+.2f} on ${r['staked']:.2f} staked "
                     f"({100 * r['roi']:+.1f}%) · worst drop {r['max_drawdown']:.2f}"
                     + (f" · HALTED: {r['halted']}" if r["halted"] else "")
                     + (f" · {r['open_unsettled']} still open" if r["open_unsettled"] else ""))
        lines.append("  why alerts were not copied: " + ", ".join(f"{k} {v}" for k, v in r["reasons"].items() if k != "copied"))
    best = max(res["results"].values(), key=lambda r: r["pnl"])
    lines += ["", "P&L by wallet (best setting):"] + [
        f"  {names.get(w, w)[:22]:<22} {v:+.2f}" for w, v in sorted(best["by_wallet"].items(), key=lambda x: -x[1])]
    return "\n".join(lines)
