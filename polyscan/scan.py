"""Build the candidate pool, rank by trailing-window P&L, and profile the leaders."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from . import metrics as m
from .api import Client

log = logging.getLogger("polyscan")

CATEGORIES = ["POLITICS", "SPORTS", "CRYPTO", "CULTURE", "ECONOMICS", "TECH", "FINANCE", "WEATHER", "MENTIONS"]


def candidate_pool(api: Client, depth_all: int, depth_month: int) -> dict[str, dict]:
    """Polymarket has no 90-day leaderboard, so cast a wide net and measure 90 days ourselves.

    Anyone with a big 90-day result is almost surely top-N all-time or top-N for the past month;
    the volume and category boards catch specialists and grinders the P&L boards miss.
    """
    boards = [("ALL", "PNL", depth_all, "OVERALL"), ("MONTH", "PNL", depth_month, "OVERALL"),
              ("MONTH", "VOL", depth_month // 2, "OVERALL"), ("WEEK", "PNL", depth_month // 2, "OVERALL")]
    boards += [("MONTH", "PNL", 100, c) for c in CATEGORIES] + [("ALL", "PNL", 100, c) for c in CATEGORIES]
    pool: dict[str, dict] = {}
    with ThreadPoolExecutor(6) as ex:
        for (period, order, _, cat), rows in zip(boards, ex.map(lambda b: api.leaderboard(*b), boards)):
            for r in rows:
                w = r["proxyWallet"].lower()
                e = pool.setdefault(w, {"wallet": w, "name": m.display_name(r.get("userName") or "", w), "x": r.get("xUsername") or "",
                                        "boards": []})
                e["boards"].append(f"{period}/{order}/{cat}#{r['rank']}")
            log.info("leaderboard %s/%s/%s: %d rows (pool %d)", period, order, cat, len(rows), len(pool))
    return pool


def rank(api: Client, pool: dict[str, dict], now: float, days: int, workers: int) -> list[dict]:
    wallets = list(pool)

    def one(w):
        return w, api.pnl_series(w)

    rows = []
    with ThreadPoolExecutor(workers) as ex:
        for i, (w, series) in enumerate(ex.map(one, wallets), 1):
            if i % 250 == 0:
                log.info("pnl curves %d/%d", i, len(wallets))
            if not series:
                continue
            rows.append({**pool[w], "series": m.series_metrics(series, now, days)})
    rows.sort(key=lambda r: r["series"]["pnl"], reverse=True)
    return rows


def profile(api: Client, row: dict, now: float, days: int, trade_cap: int, closed_cap: int) -> dict:
    w, since = row["wallet"], int(now - days * 86400)
    closed = api.closed_positions(w, since, closed_cap)
    open_ = api.positions(w)
    trades = api.trades(w, since, trade_cap, end=int(now))
    resolved = m.resolved_positions(closed, open_, since, closed_capped=len(closed) >= closed_cap)
    p = m.position_metrics(resolved, open_)
    tr = m.trade_metrics(trades, capped=len(trades) >= trade_cap, now=now)
    if "…" in row["name"] and trades and trades[0].get("name"):
        row["name"] = m.display_name(trades[0]["name"], w)
    return {**row, "positions": p, "trading": tr, "flags": m.flags(row["series"], p, tr), "style": m.style(p, tr)}


def run(days: int = 90, depth_all: int = 1000, depth_month: int = 500, deep: int = 100,
        trade_cap: int = 5000, closed_cap: int = 3000, workers: int = 8, api: Client | None = None) -> dict:
    api = api or Client()
    now = time.time()
    t0 = time.time()
    pool = candidate_pool(api, depth_all, depth_month)
    ranked = rank(api, pool, now, days, workers)
    leaders = [r for r in ranked if r["series"]["pnl"] > 0][:deep]
    log.info("profiling top %d of %d wallets", len(leaders), len(ranked))

    profiles = []
    with ThreadPoolExecutor(max(1, workers // 2)) as ex:
        futs = [ex.submit(profile, api, r, now, days, trade_cap, closed_cap) for r in leaders]
        for i, f in enumerate(futs, 1):
            profiles.append(f.result())
            if i % 10 == 0:
                log.info("profiled %d/%d", i, len(leaders))

    n = len(profiles)
    order = sorted(range(n), key=lambda i: profiles[i]["series"]["pnl"])
    pct = {i: (k + 1) / n for k, i in enumerate(order)}
    for i, p in enumerate(profiles):
        p["rank"] = i + 1
        p.update(m.copy_score(p["series"], p["positions"], p["trading"], pct[i]))

    return {
        "generated": int(now),
        "days": days,
        "elapsed_s": round(time.time() - t0),
        "pool_size": len(pool),
        # big lifetime winners who are losing money over the window
        "faded": sum(1 for r in ranked if r["series"]["pnl"] < 0 and r["series"]["pnl_all"] > 1_000_000),
        "losing": sum(1 for r in ranked if r["series"]["pnl"] < 0),
        "ranked_size": len(ranked),
        "leaderboard": [{"wallet": r["wallet"], "name": r["name"], "x": r["x"],
                         **{k: r["series"][k] for k in ("pnl", "pnl_30d", "pnl_7d", "pnl_all", "max_drawdown",
                                                         "sharpe", "green_week_rate", "best_day_share", "age_days")}}
                        for r in ranked[:500]],
        "profiles": profiles,
    }
