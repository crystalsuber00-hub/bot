"""Minimal Alpaca REST client. PAPER ONLY: the trading URL is hard-coded to the paper endpoint."""
from __future__ import annotations

import requests

PAPER = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"


class AlpacaError(RuntimeError):
    pass


class Alpaca:
    def __init__(self, key: str, secret: str, feed: str = "indicative"):
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.feed = feed

    def _req(self, method: str, url: str, **kw):
        r = requests.request(method, url, headers=self.h, timeout=20, **kw)
        if r.status_code >= 400:
            raise AlpacaError(f"{method} {url.split('.markets')[-1]} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}

    def clock(self) -> dict:
        return self._req("GET", f"{PAPER}/v2/clock")

    def account(self) -> dict:
        return self._req("GET", f"{PAPER}/v2/account")

    def positions(self) -> list[dict]:
        return self._req("GET", f"{PAPER}/v2/positions")

    def contracts(self, underlying: str, exp_gte: str, exp_lte: str, strike_gte: float, strike_lte: float) -> list[dict]:
        out, token = [], None
        while True:
            params = {"underlying_symbols": underlying, "expiration_date_gte": exp_gte,
                      "expiration_date_lte": exp_lte, "strike_price_gte": strike_gte,
                      "strike_price_lte": strike_lte, "status": "active", "limit": 10000}
            if token:
                params["page_token"] = token
            res = self._req("GET", f"{PAPER}/v2/options/contracts", params=params)
            out += res.get("option_contracts", [])
            token = res.get("next_page_token")
            if not token:
                return out

    def quotes(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
        """{symbol: (bid, ask)} from option snapshots."""
        res = self._req("GET", f"{DATA}/v1beta1/options/snapshots",
                        params={"symbols": ",".join(symbols), "feed": self.feed})
        out = {}
        for sym, snap in res.get("snapshots", {}).items():
            q = snap.get("latestQuote") or {}
            out[sym] = (float(q.get("bp") or 0), float(q.get("ap") or 0))
        return out

    def stock_price(self, symbol: str) -> float:
        res = self._req("GET", f"{DATA}/v2/stocks/{symbol}/trades/latest", params={"feed": "iex"})
        return float(res["trade"]["p"])

    def submit_mleg(self, legs: list[dict], qty: int, limit_price: float) -> dict:
        """limit_price: positive = debit paid, negative = credit received (Alpaca's mleg convention)."""
        body = {"order_class": "mleg", "type": "limit", "time_in_force": "day", "qty": str(qty),
                "limit_price": f"{limit_price:.2f}", "legs": legs}
        return self._req("POST", f"{PAPER}/v2/orders", json=body)

    def order(self, order_id: str) -> dict:
        return self._req("GET", f"{PAPER}/v2/orders/{order_id}", params={"nested": "true"})

    def cancel(self, order_id: str) -> None:
        self._req("DELETE", f"{PAPER}/v2/orders/{order_id}")
