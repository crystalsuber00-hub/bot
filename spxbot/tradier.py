"""Tradier market data + order execution. https://documentation.tradier.com"""
from __future__ import annotations

import requests

from .config import Tradier
from .models import OptionQuote, Quote


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

    @staticmethod
    def _as_list(x):
        return x if isinstance(x, list) else ([x] if x else [])

    # --- market data -------------------------------------------------------
    def get_quote(self, symbol: str) -> Quote:
        q = self._get("/markets/quotes", symbols=symbol)["quotes"]["quote"]
        return Quote(last=float(q["last"]), open=q.get("open"), prev_close=q.get("prevclose"))

    def get_chain(self, symbol: str, expiration: str, root: str | None = None) -> list[OptionQuote]:
        data = self._get("/markets/options/chains", symbol=symbol, expiration=expiration, greeks="true")
        out = []
        for o in self._as_list((data.get("options") or {}).get("option")):
            if root and o.get("root_symbol") != root:
                continue
            out.append(self._parse_option(o))
        return out

    def get_option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]:
        data = self._get("/markets/quotes", symbols=",".join(symbols), greeks="true")
        return {o["symbol"]: self._parse_option(o) for o in self._as_list(data["quotes"]["quote"])}

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
