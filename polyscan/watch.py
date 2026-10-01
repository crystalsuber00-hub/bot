"""Copy-trade signals: poll the best wallets and alert when they put real money on something.

Signal only. Nothing here places orders.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

from spxbot.config import Notify
from spxbot.notify import Notifier

from .api import Client

log = logging.getLogger("polyscan")


def pick_watchlist(scan: dict, top: int, min_score: float, allow_bots: bool = False) -> list[dict]:
    rows = sorted(scan["profiles"], key=lambda p: p["score"], reverse=True)
    out = [p for p in rows if p["score"] >= min_score and "dormant" not in p["flags"]
           and (allow_bots or "bot-speed" not in p["flags"])]
    return [{"wallet": p["wallet"], "name": p["name"] or p["wallet"][:10], "score": p["score"],
             "pnl": p["series"]["pnl"], "style": p["style"]} for p in out[:top]]


def notifier_from_env() -> Notifier:
    env = os.environ.get
    return Notifier(Notify(
        telegram_bot_token=env("TELEGRAM_BOT_TOKEN", ""), telegram_chat_id=env("TELEGRAM_CHAT_ID", ""),
        discord_webhook_url=env("DISCORD_WEBHOOK_URL", ""), webhook_url=env("WEBHOOK_URL", ""),
        ntfy_topic=env("NTFY_TOPIC", ""), ntfy_server=env("NTFY_SERVER", "https://ntfy.sh"),
        ntfy_token=env("NTFY_TOKEN", "")))


class Watcher:
    def __init__(self, api: Client, notifier: Notifier, watchlist: list[dict], state_path: str,
                 min_usd: float = 1000, consensus_hours: float = 24, sells: bool = True):
        self.api, self.n, self.watch = api, notifier, watchlist
        self.min_usd, self.consensus_s, self.sells = min_usd, consensus_hours * 3600, sells
        self.path = Path(state_path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"seen": {}, "buys": []}

    def save(self):
        self.path.write_text(json.dumps(self.state, indent=1))

    def tick(self) -> list[dict]:
        alerts = []
        for w in self.watch:
            rows = [r for r in self.api.recent_activity(w["wallet"], 100) if r.get("type") == "TRADE"]
            if not rows:
                continue
            last = self.state["seen"].get(w["wallet"])
            self.state["seen"][w["wallet"]] = max(r["timestamp"] for r in rows)
            if last is None:  # first sight of this wallet: remember where we are, don't replay history
                continue
            alerts += self._digest(w, [r for r in rows if r["timestamp"] > last])
        alerts += self._consensus()
        for a in alerts:
            self.n.send(a["text"], a)
        self.save()
        return alerts

    def _digest(self, w: dict, rows: list[dict]) -> list[dict]:
        # one wallet often fills a single order across dozens of trades; add them up per outcome and side
        agg = defaultdict(lambda: {"usd": 0.0, "shares": 0.0})
        for r in rows:
            k = (r.get("side"), r.get("asset"))
            a = agg[k]
            a["usd"] += float(r.get("usdcSize", 0))
            a["shares"] += float(r.get("size", 0))
            a.update(title=r.get("title", ""), outcome=r.get("outcome", ""), slug=r.get("eventSlug", ""), t=r["timestamp"])
        out = []
        for (side, asset), a in agg.items():
            if a["usd"] < self.min_usd or (side == "SELL" and not self.sells):
                continue
            px = a["usd"] / a["shares"] if a["shares"] else 0
            verb = "BOUGHT" if side == "BUY" else "SOLD"
            text = (f"{w['name']} {verb} ${a['usd']:,.0f} of {a['outcome']} @ {px:.2f}\n"
                    f"{a['title']}\n90d P&L ${w['pnl']:,.0f} · score {w['score']:.0f} · {w['style']}\n"
                    f"https://polymarket.com/event/{a['slug']}")
            out.append({"event": "entry" if side == "BUY" else "exit", "text": text, "wallet": w["wallet"],
                        "side": side, "asset": asset, "usd": a["usd"], "price": px, "title": a["title"],
                        "outcome": a["outcome"], "slug": a["slug"], "t": a["t"]})
            if side == "BUY":
                self.state["buys"].append({"wallet": w["wallet"], "name": w["name"], "asset": asset, "t": a["t"],
                                           "usd": a["usd"], "title": a["title"], "outcome": a["outcome"],
                                           "slug": a["slug"]})
        return out

    def _consensus(self) -> list[dict]:
        """Two or more watched wallets independently buying the same outcome is a stronger signal."""
        cutoff = time.time() - self.consensus_s
        self.state["buys"] = [b for b in self.state["buys"] if b["t"] >= cutoff]
        done = set(self.state.setdefault("consensus_sent", []))
        by_asset = defaultdict(list)
        for b in self.state["buys"]:
            by_asset[b["asset"]].append(b)
        out = []
        for asset, bs in by_asset.items():
            who = sorted({b["name"] for b in bs})
            key = f"{asset}:{len(who)}"
            if len(who) < 2 or key in done:
                continue
            done.add(key)
            b = bs[-1]
            text = (f"CONSENSUS: {len(who)} top wallets bought {b['outcome']}\n{b['title']}\n"
                    f"{', '.join(who)} · ${sum(x['usd'] for x in bs):,.0f} total\n"
                    f"https://polymarket.com/event/{b['slug']}")
            out.append({"event": "entry", "text": text, "asset": asset, "wallets": who, "title": b["title"]})
        self.state["consensus_sent"] = sorted(done)[-500:]
        return out

    def loop(self, every: int):
        log.info("watching %d wallets every %ds (min $%s)", len(self.watch), every, f"{self.min_usd:,.0f}")
        while True:
            try:
                self.tick()
            except Exception:  # keep watching through transient API errors
                log.exception("tick failed")
            time.sleep(every)
