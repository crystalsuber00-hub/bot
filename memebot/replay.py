"""Would copying 'smart money' Solana memecoin wallets have made money? Replay it from public data.

1. Candidates: wallets trading the tokens DexScreener shows as boosted/trending right now.
2. Selection (no hindsight): score each wallet only on what it realized in the `select_days` before the
   test window starts; busy bots (over `max_tx_per_day`) are dropped.
3. Replay: copy the chosen wallets' buys during the test window at the market price `delay` seconds
   later (GeckoTerminal minute candles), pay slippage and network fees, sell when the wallet sells at
   least half its bag, or after `max_hold_h` hours. Coins that stop trading (rugs) are valued at 0.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .data import Data, parse_swap

log = logging.getLogger("memebot")
DAY = 86400


# -- wallet scoring ---------------------------------------------------------------------------

def realized_pnl(swaps: list[dict], start: float, end: float) -> dict:
    """Realized dollar profit per token from sells in [start, end], average-cost basis from all earlier buys."""
    pos = defaultdict(lambda: [0.0, 0.0])  # tokens, cost in SOL
    per = defaultdict(float)
    for s in sorted(swaps, key=lambda s: s["t"]):
        if s["t"] > end:
            break
        p = pos[s["mint"]]
        if s["side"] == "buy":
            p[0] += s["tokens"]
            p[1] += s["usd"]
        elif p[0] > 0:
            frac = min(1.0, s["tokens"] / p[0])
            cost = p[1] * frac
            if s["t"] >= start:
                per[s["mint"]] += s["usd"] - cost
            p[0] -= p[0] * frac
            p[1] -= cost
    wins = sum(1 for v in per.values() if v > 0)
    return {"usd": sum(per.values()), "tokens": len(per), "wins": wins,
            "win_rate": wins / len(per) if per else 0.0, "per_token": dict(per)}


def candidates(data: Data, max_pools: int = 25) -> list[str]:
    """Wallets active in currently boosted tokens, most-seen first; one-pool spammers left out."""
    seen, pools = Counter(), 0
    for mint in data.boosted_mints():
        if pools >= max_pools:
            break
        pool = data.best_pool(mint)
        if not pool:
            continue
        pools += 1
        per = Counter(t["tx_from_address"] for t in data.pool_traders(pool["pool"]))
        for w, n in per.items():
            if n <= 20:  # dozens of trades in one pool in a day: a bot or a market maker
                seen[w] += 1
    log.info("%d candidate wallets from %d pools", len(seen), pools)
    # a wallet in many of the hottest coins at once is almost always a sniper bot: prefer 2-6 coins
    return [w for w, n in seen.most_common() if 2 <= n <= 6] + [w for w, n in seen.items() if n == 1]


def wallet_history(data: Data, wallet: str, since: float, max_tx: int, sol_usd: float = 150.0) -> list[dict] | None:
    sigs = data.signatures(wallet, since, max_tx)
    if sigs is None:
        return None
    swaps = [parse_swap(tx, wallet, sol_usd) for tx in data.transactions([s["signature"] for s in sigs])]
    return [s for s in swaps if s]


def select(data: Data, t0: float, end: float, select_days: float, top: int, n_candidates: int,
           max_tx_per_day: float, max_scan: int = 400) -> tuple[list[dict], dict]:
    """Examine wallets until `n_candidates` non-bots have full histories; bots cost one cheap lookup."""
    since = t0 - select_days * DAY
    max_tx = int(max_tx_per_day * (end - since) / DAY)
    scored, histories, bots = [], {}, 0
    sol_usd = data.sol_usd()
    for w in candidates(data)[:max_scan]:
        if len(histories) >= n_candidates:
            break
        h = wallet_history(data, w, since, max_tx, sol_usd)
        if h is None:
            bots += 1
            continue
        histories[w] = h
        i = len(histories)
        r = realized_pnl(h, since, t0)
        log.info("[%d/%d] %s… %d swaps, before-window profit $%+.2f on %d coins", i, n_candidates, w[:6],
                 len(h), r["usd"], r["tokens"])
        if r["tokens"] >= 4 and r["usd"] > 0:
            scored.append({"wallet": w, "usd": r["usd"], "tokens": r["tokens"], "win_rate": r["win_rate"]})
    log.info("examined %d wallets, skipped %d bots; %d were profitable on 4+ coins before the window",
             len(histories), bots, len(scored))
    scored.sort(key=lambda x: -x["usd"])
    return scored[:top], histories


# -- replay ------------------------------------------------------------------------------------

class Prices:
    """Minute USD prices per token from GeckoTerminal, fetched lazily and cached."""

    def __init__(self, data: Data, cache: dict | None = None):
        self.data, self.c = data, cache if cache is not None else {}

    def pool(self, mint):
        if mint not in self.c:
            self.c[mint] = {"pool": self.data.best_pool(mint), "candles": {}}
        return self.c[mint]

    def at(self, mint: str, t: float, within: float = 600) -> float | None:
        """Close of the first minute candle at or after t, if one exists within `within` seconds."""
        e = self.pool(mint)
        if not e["pool"]:
            return None
        cs = e["candles"]
        if not any(int(k) <= t <= int(k) + 60000 for k in cs):  # fetch ~16 hours from t on
            rows = self.data.candles(e["pool"]["pool"], int(t) + 60000)
            cs[str(int(t))] = rows
        for k, rows in cs.items():
            if int(k) <= t <= int(k) + 60000:
                for r in rows:
                    if r[0] + 60 > t and r[0] - t <= within:
                        return float(r[4])
        return None


@dataclass
class Book:
    cash: float
    start: float
    pos: dict = field(default_factory=dict)
    closed: list = field(default_factory=list)
    halted: bool = False

    def equity(self) -> float:
        return self.cash + sum(p["cost"] for p in self.pos.values())


def simulate(signals: list[dict], prices: Prices, bankroll: float = 100, stake_pct: float = 0.05,
             delay: float = 15, slip: float = 0.02, tx_fee: float = 0.05, max_hold_h: float = 24,
             daily_loss_pct: float = 0.10, halt_pct: float = 0.30, end: float | None = None) -> dict:
    b = Book(cash=bankroll, start=bankroll)
    their = defaultdict(float)         # each copied wallet's bag per token, to spot "sold most of it"
    reasons = Counter()
    end = end or time.time()

    def sell(key, t, why):
        p = b.pos.pop(key)
        px = prices.at(p["mint"], t, within=6 * 3600)  # quiet coins can go minutes without a trade
        value = 0.0 if px is None else p["tokens"] * px * (1 - slip) - tx_fee
        pnl = value - p["cost"]
        b.cash += value
        b.closed.append({**p, "exit_t": t, "exit_px": px, "pnl": round(pnl, 2), "why": why if px is not None else "no price (rug?)",
                         "day": int(t // DAY)})
        if b.start - b.equity() >= halt_pct * b.start:
            b.halted = True

    for s in sorted(signals, key=lambda s: s["t"]):
        key = (s["wallet"], s["mint"])
        # time stops for anything held too long
        for k, p in list(b.pos.items()):
            if s["t"] - p["t"] > max_hold_h * 3600:
                sell(k, p["t"] + max_hold_h * 3600, "held 24h")
        if s["side"] == "sell":
            before = their[key]
            their[key] = max(0.0, before - s["tokens"])
            if key in b.pos and before > 0 and s["tokens"] >= 0.5 * before:
                sell(key, s["t"] + delay, "wallet sold")
            continue
        their[key] += s["tokens"]
        if key in b.pos or any(p["mint"] == s["mint"] for p in b.pos.values()):
            reasons["already holding"] += 1
            continue
        today = int(s["t"] // DAY)
        day_loss = -sum(c["pnl"] for c in b.closed if c["day"] == today)
        if b.halted:
            reasons["halted at 30% down"] += 1
            continue
        if day_loss >= daily_loss_pct * b.start:
            reasons["daily stop"] += 1
            continue
        stake = max(1.0, stake_pct * b.equity())
        if b.cash < stake + tx_fee:
            reasons["no cash left"] += 1
            continue
        px = prices.at(s["mint"], s["t"] + delay)
        if not px:
            reasons["no price"] += 1
            continue
        entry = px * (1 + slip)
        b.cash -= stake + tx_fee
        b.pos[key] = {"wallet": s["wallet"], "mint": s["mint"], "t": s["t"] + delay, "entry_px": entry,
                      "tokens": stake / entry, "cost": stake + tx_fee, "whale_sol": s["sol"]}
        reasons["copied"] += 1
    for k, p in list(b.pos.items()):  # close out what's left
        sell(k, min(end, p["t"] + max_hold_h * 3600), "window ended" if p["t"] + max_hold_h * 3600 > end else "held 24h")
    c = b.closed
    peak, dd, run = bankroll, 0.0, bankroll
    for x in sorted(c, key=lambda x: x["exit_t"]):
        run += x["pnl"]
        peak, dd = max(peak, run), max(dd, peak - run)
    return {"trades": len(c), "wins": sum(x["pnl"] > 0 for x in c), "pnl": round(sum(x["pnl"] for x in c), 2),
            "end_value": round(b.cash, 2), "max_drawdown": round(dd, 2), "rugs": sum(x["why"].startswith("no price") for x in c),
            "reasons": dict(reasons.most_common()), "exits": dict(Counter(x["why"] for x in c)),
            "best": max((x["pnl"] for x in c), default=0), "worst": min((x["pnl"] for x in c), default=0),
            "by_wallet": {w: round(sum(x["pnl"] for x in c if x["wallet"] == w), 2) for w in {x["wallet"] for x in c}},
            "closed": c}


VARIANTS = {
    "bot, 15s behind": {"delay": 15},
    "1 minute behind": {"delay": 60},
    "5 minutes behind (by hand)": {"delay": 300},
}


def run(test_days: float = 1.5, select_days: float = 2, top: int = 8, n_candidates: int = 25,
        bankroll: float = 100, max_tx_per_day: float = 80, out: str = "memebot_out") -> dict:
    data, outp = Data(), Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    end = time.time()
    t0 = end - test_days * DAY
    picks, hist = select(data, t0, end, select_days, top, n_candidates, max_tx_per_day)
    log.info("picked %d wallets: %s", len(picks), ", ".join(f"{p['wallet'][:6]}… ${p['usd']:+.0f}" for p in picks))
    signals = [{**s, "wallet": p["wallet"]} for p in picks for s in hist[p["wallet"]] if t0 <= s["t"] <= end]
    log.info("%d buys and sells to copy in the test window", len(signals))
    cache_path = outp / "prices.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    prices = Prices(data, cache)
    results = {}
    for name, v in VARIANTS.items():
        results[name] = simulate(signals, prices, bankroll=bankroll, end=end, **v)
        cache_path.write_text(json.dumps(prices.c))
        r = results[name]
        log.info("%-28s %3d trades %3d won  P&L %+7.2f  rugs %d", name, r["trades"], r["wins"], r["pnl"], r["rugs"])
    res = {"t0": t0, "end": end, "select_days": select_days, "bankroll": bankroll, "picks": picks,
           "signals": len(signals), "results": results, "generated": int(end)}
    (outp / f"replay_{time.strftime('%Y-%m-%d', time.gmtime(t0))}_{time.strftime('%Y-%m-%d', time.gmtime(end))}.json").write_text(json.dumps(res, indent=1))
    return res


def report(res: dict) -> str:
    d = lambda t: time.strftime("%b %d", time.gmtime(t))
    lines = [f"Solana memecoin copy replay {d(res['t0'])} to {d(res['end'])} · ${res['bankroll']:.0f} start, 5% bets · "
             f"{len(res['picks'])} wallets picked on the {res['select_days']:.0f} days before · {res['signals']} wallet swaps", ""]
    for name, r in res["results"].items():
        lines.append(f"{name}: {r['trades']} trades, {r['wins']} won · P&L {r['pnl']:+.2f} · ends at ${r['end_value']:.2f} · "
                     f"worst drop {r['max_drawdown']:.2f} · rugs/no exit price {r['rugs']}")
        lines.append("   exits: " + ", ".join(f"{k} {v}" for k, v in r["exits"].items())
                     + " · not copied: " + ", ".join(f"{k} {v}" for k, v in r["reasons"].items() if k != "copied"))
    return "\n".join(lines)
