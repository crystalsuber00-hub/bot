"""Tradier market data + order execution. https://documentation.tradier.com"""
from __future__ import annotations

import requests

from .config import Tradier
from .models import Bar, OptionQuote, OrderStatus, Quote


class TradierClient:
    def __init__(self, cfg: Tradier):
        self.cfg = cfg
        self.base = "https://sandbox.tradier.com/v1" if cfg.sandbox else "https://api.tradier.com/v1"
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {cfg.token}", "Accept": "application/json"})

    def _get(self, path: str, **params) -> dict:
        r = self.s.get(f"{self.base}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, data: dict) -> dict:
        r = self.s.post(f"{self.base}{path}", data=data, timeout=15)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, data: dict) -> dict:
        r = self.s.put(f"{self.base}{path}", data=data, timeout=15)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> dict:
        r = self.s.delete(f"{self.base}{path}", timeout=15)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _as_list(x):
        return x if isinstance(x, list) else ([x] if x else [])

    # --- market data -------------------------------------------------------
    def get_quote(self, symbol: str) -> Quote:
        q = self._get("/markets/quotes", symbols=symbol)["quotes"]["quote"]
        return Quote(last=float(q["last"]), open=q.get("open"), prev_close=q.get("prevclose"))

    def get_chain(self, symbol: str, expiration: str, root: str | None = None, right: str | None = None,
                  near: float | None = None) -> list[OptionQuote]:
        data = self._get("/markets/options/chains", symbol=symbol, expiration=expiration, greeks="true")
        out = []
        for o in self._as_list((data.get("options") or {}).get("option")):
            if root and o.get("root_symbol") != root:
                continue
            if right and o.get("option_type") != right:
                continue
            out.append(self._parse_option(o))
        if near is not None and out:
            spot = self.get_quote(symbol).last
            out = [o for o in out if abs(o.strike - spot) <= near]
        return out

    def get_option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]:
        data = self._get("/markets/quotes", symbols=",".join(symbols), greeks="true")
        return {o["symbol"]: self._parse_option(o) for o in self._as_list(data["quotes"]["quote"])}

    def get_bars(self, symbol: str, day: str, minutes: int) -> list[Bar]:
        """Intraday bars for one day, pre-market included (04:00-16:00 ET)."""
        data = self._get("/markets/timesales", symbol=symbol, interval=f"{minutes}min",
                         start=f"{day} 04:00", end=f"{day} 16:00", session_filter="all")
        rows = self._as_list((data.get("series") or {}).get("data"))
        return [Bar(r["time"][:16], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                for r in rows]

    def get_daily(self, symbol: str, start: str, end: str) -> list[Bar]:
        data = self._get("/markets/history", symbol=symbol, interval="daily", start=start, end=end)
        rows = self._as_list((data.get("history") or {}).get("day"))
        return [Bar(r["date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in rows]

    def get_expirations(self, symbol: str) -> list[str]:
        data = self._get("/markets/options/expirations", symbol=symbol)
        return [str(d) for d in self._as_list((data.get("expirations") or {}).get("date"))]

    @staticmethod
    def _parse_option(o: dict) -> OptionQuote:
        greeks = o.get("greeks") or {}
        return OptionQuote(
            symbol=o["symbol"],
            strike=float(o["strike"]),
            right=o["option_type"],
            bid=float(o.get("bid") or 0),
            ask=float(o.get("ask") or 0),
            delta=greeks.get("delta"),
        )

    # --- execution ---------------------------------------------------------
    def _spread_order(self, short_sym: str, long_sym: str, qty: int, price: float, opening: bool) -> str:
        data = {
            "class": "multileg",
            "symbol": "SPX",
            "type": "credit" if opening else "debit",
            "duration": "day",
            "price": f"{price:.2f}",
            "option_symbol[0]": short_sym,
            "side[0]": "sell_to_open" if opening else "buy_to_close",
            "quantity[0]": qty,
            "option_symbol[1]": long_sym,
            "side[1]": "buy_to_open" if opening else "sell_to_close",
            "quantity[1]": qty,
        }
        res = self._post(f"/accounts/{self.cfg.account_id}/orders", data)
        return str(res["order"]["id"])

    def open_spread(self, short_sym, long_sym, qty, credit) -> str:
        return self._spread_order(short_sym, long_sym, qty, credit, opening=True)

    def close_spread(self, short_sym, long_sym, qty, debit) -> str:
        return self._spread_order(short_sym, long_sym, qty, debit, opening=False)

    # --- order tracking ----------------------------------------------------
    def order_status(self, order_id: str) -> OrderStatus:
        try:
            o = self._get(f"/accounts/{self.cfg.account_id}/orders/{order_id}")["order"]
        except (requests.HTTPError, KeyError):
            return OrderStatus("unknown")
        status = o.get("status", "")
        filled = int(float(o.get("exec_quantity") or 0))
        avg = o.get("avg_fill_price")
        avg = abs(float(avg)) if avg not in (None, "", 0, "0") else None
        if status == "filled":
            state = "filled"
        elif status in ("canceled", "expired"):
            state = "cancelled"
        elif status in ("rejected", "error"):
            state = "rejected"
        else:  # open, partially_filled, pending, accepted_for_bidding, held, ...
            state = "working"
        return OrderStatus(state, filled, avg)

    def cancel_order(self, order_id: str) -> None:
        self._delete(f"/accounts/{self.cfg.account_id}/orders/{order_id}")

    def replace_order(self, order_id: str, price: float, opening: bool) -> None:
        self._put(f"/accounts/{self.cfg.account_id}/orders/{order_id}",
                  {"type": "credit" if opening else "debit", "duration": "day", "price": f"{price:.2f}"})

    def positions(self) -> dict[str, int]:
        """Net quantity per SPX option symbol (short = negative)."""
        data = self._get(f"/accounts/{self.cfg.account_id}/positions")
        out: dict[str, int] = {}
        block = data.get("positions")
        for p in self._as_list(block.get("position") if isinstance(block, dict) else None):
            if p["symbol"].startswith("SPX"):
                out[p["symbol"]] = out.get(p["symbol"], 0) + int(p["quantity"])
        return out
