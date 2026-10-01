"""Copy the watched wallets' game-winner bets onto Polymarket US (the CFTC-regulated US exchange).

Paper mode (the default) reads real Polymarket US prices and records what it would have bought,
then scores those paper trades when the games settle. Live mode places real orders through the
Polymarket US Retail API. Live mode is only switched on by --live together with API keys in
POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY.

What it copies, on purpose narrowly:
  * only a whale's BUY of a game's moneyline (who wins), matched to the same game on Polymarket US
    by event slug (both exchanges use e.g. nfl-kc-lv-2026-10-04) and team name;
  * only when the copied team is the market's first ("long") side. Buying the other team means
    shorting the instrument, and the docs don't pin down how that order is priced, so those are
    sent to you as a manual alert instead of risking a mis-priced order;
  * only with an IOC limit order at a price that still beats the whale's fill after the taker fee.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("polyscan")

GATEWAY = "https://gateway.polymarket.us"
API = "https://api.polymarket.us"
TAKER_THETA = 0.0695   # docs.polymarket.us/fees: taker fee = theta * contracts * p * (1 - p)
ET = ZoneInfo("America/New_York")


def taker_fee(p: float) -> float:
    """Fee per contract for taking liquidity at price p."""
    return TAKER_THETA * p * (1 - p)


# -- client ---------------------------------------------------------------------------

class USClient:
    def __init__(self, key_id: str = "", secret_key: str = "", timeout: float = 20):
        self.key_id, self.timeout = key_id, timeout
        self.s = requests.Session()
        self._key = None
        if secret_key:
            from cryptography.hazmat.primitives.asymmetric import ed25519
            self._key = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(secret_key)[:32])

    @classmethod
    def from_env(cls) -> "USClient":
        return cls(os.environ.get("POLYMARKET_KEY_ID", ""), os.environ.get("POLYMARKET_SECRET_KEY", ""))

    @property
    def authed(self) -> bool:
        return bool(self.key_id and self._key)

    def public(self, path: str, **params):
        try:
            r = self.s.get(GATEWAY + path, params=params, timeout=self.timeout)
            return r.json() if r.ok else None
        except (requests.RequestException, ValueError) as e:
            log.warning("US public %s failed: %s", path, e)
            return None

    def private(self, method: str, path: str, body: dict | None = None):
        if not self.authed:
            raise RuntimeError("Polymarket US API keys not set (POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY)")
        ts = str(int(time.time() * 1000))
        sig = base64.b64encode(self._key.sign(f"{ts}{method}{path}".encode())).decode()
        headers = {"X-PM-Access-Key": self.key_id, "X-PM-Timestamp": ts, "X-PM-Signature": sig,
                   "Content-Type": "application/json"}
        r = self.s.request(method, API + path, headers=headers, data=json.dumps(body) if body is not None else None,
                           timeout=self.timeout)
        if not r.ok:
            raise RuntimeError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    # public market data
    def event(self, slug: str) -> dict | None:
        d = self.public(f"/v1/events/slug/{slug}")
        return (d or {}).get("event")

    def bbo(self, market_slug: str) -> dict | None:
        d = self.public(f"/v1/markets/{market_slug}/bbo")
        return (d or {}).get("marketData")

    def settlement(self, market_slug: str) -> float | None:
        d = self.public(f"/v1/markets/{market_slug}/settlement")
        return float(d["settlement"]) if d and d.get("settlement") is not None else None

    # account
    def buying_power(self) -> float:
        bal = self.private("GET", "/v1/account/balances").get("balances") or [{}]
        return _amt(bal[0].get("buyingPower"))

    def order(self, body: dict) -> dict:
        return self.private("POST", "/v1/orders", body)


def _amt(a) -> float:
    if isinstance(a, dict):
        a = a.get("value")
    try:
        return float(a)
    except (TypeError, ValueError):
        return 0.0


# -- matching -------------------------------------------------------------------------

def find_moneyline(event: dict | None, outcome: str) -> tuple[dict | None, dict | None, str]:
    """(market, side, reason) for the game-winner market on Polymarket US and the side named `outcome`."""
    if not event:
        return None, None, "game not listed on Polymarket US"
    slug = event.get("slug", "")
    markets = event.get("markets") or []
    ml = [m for m in markets if m.get("slug") == f"aec-{slug}"] or \
         [m for m in markets if str(m.get("sportsMarketType", "")).endswith(("_full_game_winner", "_match_winner"))]
    if len(ml) != 1:
        return None, None, "no single game-winner market on Polymarket US"
    m, o = ml[0], outcome.strip().lower()

    def names(s):
        t = s.get("team") or {}
        return {x.strip().lower() for x in (s.get("description"), t.get("name"), t.get("alias"), t.get("safeName")) if x}

    exact = [s for s in m.get("marketSides", []) if o in names(s)]
    # "Oklahoma State" on one exchange is "Oklahoma State Cowboys" on the other
    prefix = [s for s in m.get("marketSides", []) if any(n.startswith(o + " ") for n in names(s))]
    sides = exact or prefix
    if len(sides) != 1:
        return m, None, f"couldn't tell which side '{outcome}' is"
    return m, sides[0], ""


# -- sizing ---------------------------------------------------------------------------

def plan_entry(their_px: float, ask: float | None, tick: float, min_qty: float, stake: float,
               max_slip: float = 0.03, lo: float = 0.15, hi: float = 0.85) -> dict:
    """Limit price and size for copying a buy, or the reason not to.

    The ceiling is the whale's average price plus max_slip, all-in: price plus the taker fee has to
    stay under it, because the edge being copied is only a few cents.
    """
    if ask is None or ask <= 0:
        return {"go": False, "why": "no seller on Polymarket US right now"}
    ceiling = min(their_px + max_slip, hi + taker_fee(hi))
    tick = tick or 0.01
    limit = math.floor(ceiling / tick) * tick
    while limit > 0 and limit + taker_fee(limit) > ceiling + 1e-9:
        limit -= tick
    limit = round(limit, 4)
    if ask > limit:
        return {"go": False, "why": f"US price {ask:.3f} is above your {limit:.3f} limit (fee included)", "ask": ask}
    if not lo <= ask <= hi:
        return {"go": False, "why": f"price {ask:.2f} is outside {lo:.2f}-{hi:.2f}", "ask": ask}
    step = min_qty if 0 < min_qty < 1 else 1
    qty = math.floor(stake / (limit + taker_fee(limit)) / step) * step
    qty = round(qty, 4)
    if qty < (min_qty or 1):
        return {"go": False, "why": f"${stake:.2f} buys less than the minimum size", "ask": ask}
    return {"go": True, "ask": ask, "limit": limit, "qty": qty, "cost": round(qty * (limit + taker_fee(limit)), 2)}


# -- the trader -------------------------------------------------------------------------

class CopyTrader:
    def __init__(self, us: USClient, state_path: str, bankroll: float, live: bool = False,
                 stake_pct: float = 0.10, max_slip: float = 0.03, max_losses_per_day: int = 3,
                 max_drawdown_pct: float = 0.5, stop_file: str = "STOP"):
        if live and not us.authed:
            raise SystemExit("--live needs POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY in the environment")
        self.us, self.live, self.bankroll = us, live, bankroll
        self.stake = max(1.0, bankroll * stake_pct)
        self.max_slip, self.max_losses, self.max_dd = max_slip, max_losses_per_day, max_drawdown_pct
        self.path, self.stop_file = Path(state_path), Path(stop_file)
        fresh = {"positions": {}, "closed": [], "halted": ""}
        self.state = json.loads(self.path.read_text()) if self.path.exists() else fresh
        for k, v in fresh.items():
            self.state.setdefault(k, v)

    @property
    def mode(self) -> str:
        return "LIVE" if self.live else "PAPER"

    def save(self):
        self.path.write_text(json.dumps(self.state, indent=1))

    # -- guards
    def _blocked(self) -> str:
        if self.stop_file.exists():
            return f"stop file {self.stop_file} exists"
        if self.state["halted"]:
            return f"halted: {self.state['halted']}"
        today = datetime.now(ET).date().isoformat()
        losses = sum(1 for c in self.state["closed"] if c["pnl"] < 0 and c["day"] == today)
        if losses >= self.max_losses:
            return f"{losses} losses today; resuming tomorrow"
        return ""

    def open_cost(self) -> float:
        return sum(p["cost"] for p in self.state["positions"].values())

    def realized(self) -> float:
        return sum(c["pnl"] for c in self.state["closed"])

    # -- entries and exits
    def on_alert(self, a: dict) -> str:
        try:
            note = self._on_buy(a) if a.get("side") == "BUY" else self._on_sell(a)
        except Exception as e:  # never let a trading error stop the alerts
            log.exception("autotrade failed")
            note = f"error: {e}"
        self.save()
        return f"{self.mode}: {note}" if note else ""

    def _on_buy(self, a: dict) -> str:
        if a.get("market") != a.get("slug"):
            return "skipped, only game-winner bets are copied (this is a spread, total or prop)"
        if a["asset"] in self.state["positions"]:
            return "already holding this one"
        market, side, why = find_moneyline(self.us.event(a["slug"]), a["outcome"])
        if not side:
            return f"skipped, {why}"
        if not side.get("long"):
            return (f"not auto-bought: {a['outcome']} is the second side of {market['slug']}. "
                    f"To copy, buy {a['outcome']} in the app at no more than {a['price'] + self.max_slip:.2f}")
        if (b := self._blocked()):
            return f"not buying, {b}"
        if self.open_cost() + self.stake > self.bankroll + 1e-6:
            return f"not buying, ${self.open_cost():.2f} of ${self.bankroll:.0f} already in open bets"
        bbo = self.us.bbo(market["slug"]) or {}
        if bbo.get("state") not in (None, "MARKET_STATE_OPEN"):
            return f"skipped, market is {bbo.get('state')}"
        plan = plan_entry(a["price"], _amt(bbo.get("longQuote") or bbo.get("bestAsk")) or None,
                          float(market.get("orderPriceMinTickSize") or 0.01), float(market.get("minimumTradeQty") or 1),
                          self.stake, self.max_slip)
        if not plan["go"]:
            return f"skipped, {plan['why']}"
        if self.live:
            if self.us.buying_power() < plan["cost"]:
                return "not buying, not enough buying power in the account"
            qty, px = self._fill(self.us.order({
                "marketSlug": market["slug"], "intent": "ORDER_INTENT_BUY_LONG", "type": "ORDER_TYPE_LIMIT",
                "price": {"value": f"{plan['limit']:.4f}", "currency": "USD"}, "quantity": plan["qty"],
                "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
                "synchronousExecution": True, "maxBlockTime": "10"}), plan["limit"])
            if qty <= 0:
                return f"order for {plan['qty']} @ {plan['limit']:.3f} did not fill (price moved); nothing bought"
        else:
            qty, px = plan["qty"], plan["ask"]
        cost = round(qty * (px + taker_fee(px)), 2)
        self.state["positions"][a["asset"]] = {
            "us_market": market["slug"], "team": a["outcome"], "title": a["title"], "qty": qty, "px": px,
            "cost": cost, "wallet": a.get("wallet"), "whale_px": a["price"], "t": int(time.time())}
        return f"bought {qty:g} {a['outcome']} @ {px:.3f} on {market['slug']} (${cost:.2f} incl. fee)"

    def _on_sell(self, a: dict) -> str:
        p = self.state["positions"].get(a["asset"])
        if not p or p.get("wallet") not in (None, a.get("wallet")):
            return ""
        bbo = self.us.bbo(p["us_market"]) or {}
        bid = _amt(bbo.get("bestBid"))
        if bid <= 0:
            return f"{a['outcome']}: the copied wallet sold, but there is no buyer on Polymarket US yet; holding"
        if self.live:
            qty, px = self._fill(self.us.order({
                "marketSlug": p["us_market"], "intent": "ORDER_INTENT_SELL_LONG", "type": "ORDER_TYPE_LIMIT",
                "price": {"value": f"{max(0.01, bid - 0.02):.4f}", "currency": "USD"}, "quantity": p["qty"],
                "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
                "synchronousExecution": True, "maxBlockTime": "10"}), bid)
            if qty <= 0:
                return f"tried to sell {p['team']} but it didn't fill; will retry on the next sell signal"
            if qty < p["qty"] - 1e-9:  # partial exit: book the sold part, keep the rest open
                part = round(p["cost"] * qty / p["qty"], 2)
                pnl = round(qty * (px - taker_fee(px)) - part, 2)
                self.state["closed"].append({**p, "qty": qty, "cost": part, "exit_px": px, "pnl": pnl,
                                             "how": "partly sold", "day": datetime.now(ET).date().isoformat(),
                                             "t_close": int(time.time())})
                p["qty"], p["cost"] = round(p["qty"] - qty, 4), round(p["cost"] - part, 2)
                return f"sold {qty:g} of {p['team']} @ {px:.3f} ({pnl:+.2f}); {p['qty']:g} still open"
        else:
            px = bid
        return self._close(a["asset"], px - taker_fee(px), "sold after the wallet sold")

    def _fill(self, resp: dict, fallback_px: float) -> tuple[float, float]:
        """Filled quantity and average price from a synchronous order response."""
        qty, px = 0.0, 0.0
        for ex in resp.get("executions") or []:
            o = ex.get("order") or {}
            qty = max(qty, float(o.get("cumQuantity") or 0))
            px = _amt(o.get("avgPx")) or px
        return qty, (px or fallback_px)

    def _close(self, asset: str, exit_px_net: float, how: str) -> str:
        p = self.state["positions"].pop(asset)
        pnl = round(p["qty"] * exit_px_net - p["cost"], 2)
        self.state["closed"].append({**p, "exit_px": round(exit_px_net, 4), "pnl": pnl, "how": how,
                                     "day": datetime.now(ET).date().isoformat(), "t_close": int(time.time())})
        if self.realized() <= -self.bankroll * self.max_dd and not self.state["halted"]:
            self.state["halted"] = f"lost ${-self.realized():.2f}, over {self.max_dd:.0%} of ${self.bankroll:.0f}"
        return f"{p['team']} {how}: {'+' if pnl >= 0 else '−'}${abs(pnl):.2f} (total {self.realized():+.2f})"

    def refresh(self) -> list[str]:
        """Score positions whose games have settled."""
        notes = []
        for asset, p in list(self.state["positions"].items()):
            s = self.us.settlement(p["us_market"])
            if s is not None:
                notes.append(f"{self.mode}: " + self._close(asset, s, "won" if s >= 0.5 else "lost"))
        if notes:
            self.save()
        return notes

    def summary(self) -> str:
        c = self.state["closed"]
        wins = sum(1 for x in c if x["pnl"] > 0)
        lines = [f"{self.mode} copy trading · bankroll ${self.bankroll:.0f} · ${self.stake:.2f} per bet",
                 f"closed {len(c)} ({wins} won) · realized {self.realized():+.2f} · "
                 f"open {len(self.state['positions'])} (${self.open_cost():.2f})"]
        if self.state["halted"]:
            lines.append(f"HALTED: {self.state['halted']} (run `polyscan trades --reset-halt` to resume)")
        for p in self.state["positions"].values():
            lines.append(f"  open  {p['team']:<22} {p['qty']:>7g} @ {p['px']:.3f}  ${p['cost']:.2f}  {p['us_market']}")
        for x in c[-15:]:
            lines.append(f"  {x['day']} {x['team']:<22} {x['how']:<28} {x['pnl']:+.2f}")
        return "\n".join(lines)
