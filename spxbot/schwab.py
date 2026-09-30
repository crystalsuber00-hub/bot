"""Charles Schwab Trader API: market data only (quotes, bars, option chains). https://developer.schwab.com

Login is OAuth: `spxbot -c spy03.toml --schwab-login` once, then the bot refreshes its own access token.
Schwab's refresh token hard-expires after 7 days, so you log in again once a week.
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from zoneinfo import ZoneInfo

import requests

from .config import Schwab
from .models import Bar, OptionQuote, Quote

API = "https://api.schwabapi.com"
NY = ZoneInfo("America/New_York")
REFRESH_DAYS = 7


class SchwabLoginNeeded(RuntimeError):
    pass


def auth_url(cfg: Schwab) -> str:
    return f"{API}/v1/oauth/authorize?client_id={quote(cfg.app_key)}&redirect_uri={quote(cfg.callback_url, safe='')}"


def code_from_redirect(url: str) -> str:
    code = parse_qs(urlparse(url.strip()).query).get("code", [""])[0]
    if not code:
        raise ValueError("no ?code= in that URL; paste the whole address bar after logging in")
    return unquote(code)


class SchwabClient:
    def __init__(self, cfg: Schwab, session=None):
        self.cfg = cfg
        self.s = session or requests.Session()
        self.path = Path(cfg.token_file)
        self.tok = json.loads(self.path.read_text()) if self.path.exists() else {}

    # --- auth -------------------------------------------------------------------
    def _token_request(self, data: dict) -> dict:
        basic = base64.b64encode(f"{self.cfg.app_key}:{self.cfg.app_secret}".encode()).decode()
        r = self.s.post(f"{API}/v1/oauth/token", data=data, timeout=15,
                        headers={"Authorization": f"Basic {basic}",
                                 "Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code >= 400:
            raise SchwabLoginNeeded(f"Schwab token request failed ({r.status_code}): {' '.join(r.text.split())[:200]}")
        return r.json()

    def _store(self, t: dict, new_login: bool) -> None:
        now = time.time()
        self.tok = {**self.tok, "access_token": t["access_token"],
                    "access_expires": now + int(t.get("expires_in", 1800)) - 60}
        if t.get("refresh_token"):
            if new_login or t["refresh_token"] != self.tok.get("refresh_token"):
                self.tok["refresh_issued"] = now
            self.tok["refresh_token"] = t["refresh_token"]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.tok, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def login(self, redirected_url: str) -> None:
        t = self._token_request({"grant_type": "authorization_code", "code": code_from_redirect(redirected_url),
                                 "redirect_uri": self.cfg.callback_url})
        self._store(t, new_login=True)

    def refresh_days_left(self) -> float:
        issued = self.tok.get("refresh_issued")
        return REFRESH_DAYS - (time.time() - issued) / 86400 if issued else 0.0

    def _access(self) -> str:
        if not self.tok.get("refresh_token"):
            raise SchwabLoginNeeded("not logged in to Schwab: run  spxbot -c <config> --schwab-login")
        if time.time() >= self.tok.get("access_expires", 0):
            if self.refresh_days_left() <= 0:
                raise SchwabLoginNeeded("Schwab login expired (7-day limit): run  spxbot -c <config> --schwab-login")
            self._store(self._token_request({"grant_type": "refresh_token",
                                             "refresh_token": self.tok["refresh_token"]}), new_login=False)
        return self.tok["access_token"]

    def _get(self, path: str, **params) -> dict:
        r = self.s.get(f"{API}/marketdata/v1{path}", params=params, timeout=15,
                       headers={"Authorization": f"Bearer {self._access()}", "Accept": "application/json"})
        if r.status_code == 401:
            self.tok["access_expires"] = 0  # force a refresh on the next call
        r.raise_for_status()
        return r.json()

    # --- market data --------------------------------------------------------------
    def get_quote(self, symbol: str) -> Quote:
        q = self._get("/quotes", symbols=symbol, fields="quote")[symbol]["quote"]
        return Quote(last=float(q["lastPrice"]), open=q.get("openPrice"), prev_close=q.get("closePrice"))

    @staticmethod
    def _ms(day: str, hhmm: str) -> int:
        return int(datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=NY).timestamp() * 1000)

    def get_bars(self, symbol: str, day: str, minutes: int) -> list[Bar]:
        """Intraday bars for one day, pre-market included (04:00-16:00 ET)."""
        data = self._get("/pricehistory", symbol=symbol, periodType="day", frequencyType="minute",
                         frequency=minutes, startDate=self._ms(day, "04:00"), endDate=self._ms(day, "16:00"),
                         needExtendedHoursData="true")
        out = []
        for c in data.get("candles") or []:
            t = datetime.fromtimestamp(c["datetime"] / 1000, NY).strftime("%Y-%m-%dT%H:%M")
            if t.startswith(day) and "04:00" <= t[11:] < "16:00":
                out.append(Bar(t, float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])))
        return out

    def get_daily(self, symbol: str, start: str, end: str) -> list[Bar]:
        data = self._get("/pricehistory", symbol=symbol, periodType="month", frequencyType="daily", frequency=1,
                         startDate=self._ms(start, "00:00"), endDate=self._ms(end, "23:59"))
        return [Bar(datetime.fromtimestamp(c["datetime"] / 1000, NY).date().isoformat(), float(c["open"]),
                    float(c["high"]), float(c["low"]), float(c["close"])) for c in data.get("candles") or []]

    def get_expirations(self, symbol: str) -> list[str]:
        data = self._get("/expirationchain", symbol=symbol)
        return sorted({e["expirationDate"][:10] for e in data.get("expirationList") or []})

    @staticmethod
    def _parse_option(o: dict) -> OptionQuote:
        delta = o.get("delta")
        return OptionQuote(symbol=o["symbol"], strike=float(o.get("strikePrice") or 0),
                           right="call" if o.get("putCall", "").upper() == "CALL" else "put",
                           bid=float(o.get("bid", o.get("bidPrice")) or 0),
                           ask=float(o.get("ask", o.get("askPrice")) or 0),
                           delta=None if delta in (None, -999.0) else float(delta))

    def get_chain(self, symbol: str, expiration: str, root: str | None = None, right: str | None = None,
                  near: float | None = None) -> list[OptionQuote]:
        params = {"symbol": symbol, "fromDate": expiration, "toDate": expiration, "strikeCount": 40}
        if right:
            params["contractType"] = right.upper()
        data = self._get("/chains", **params)
        out = []
        for key in ("callExpDateMap", "putExpDateMap"):
            for exp, strikes in (data.get(key) or {}).items():
                if not exp.startswith(expiration):
                    continue
                for rows in strikes.values():
                    out += [self._parse_option(o) for o in rows]
        if right:
            out = [o for o in out if o.right == right]
        spot = data.get("underlyingPrice") or (data.get("underlying") or {}).get("last")
        if near is not None and spot:
            out = [o for o in out if abs(o.strike - float(spot)) <= near]
        return out

    def get_option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]:
        data = self._get("/quotes", symbols=",".join(symbols), fields="quote,reference")
        out = {}
        for sym, v in data.items():
            if sym not in symbols or "quote" not in v:
                continue
            q, ref = v["quote"], v.get("reference") or {}
            out[sym] = self._parse_option({"symbol": sym, "strikePrice": ref.get("strikePrice", 0),
                                           "putCall": "CALL" if ref.get("contractType", "C") in ("C", "CALL") else "PUT",
                                           "bid": q.get("bidPrice"), "ask": q.get("askPrice"),
                                           "delta": q.get("delta")})
        return out
