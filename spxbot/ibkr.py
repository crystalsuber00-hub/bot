"""Interactive Brokers data + execution via ib_async (talks to a local IB Gateway / TWS).

Option "symbols" used by the engine/state are self-describing keys so positions
survive restarts: ``SPXW|20260930|P|6000``.
"""
from __future__ import annotations

import logging
import math
import time

from .config import Ibkr
from .models import Bar, OptionQuote, OrderStatus, Quote

INDEXES = {"SPX", "XSP", "NDX", "RUT", "VIX"}

log = logging.getLogger("spxbot")


def _px(x) -> float:
    """IB uses nan / -1 for 'no quote'; treat as 0."""
    return float(x) if x is not None and not math.isnan(x) and x > 0 else 0.0


def _opt(x):
    return float(x) if x is not None and not math.isnan(x) and x > 0 else None


def make_key(root: str, expiration: str, right: str, strike: float) -> str:
    return f"{root}|{expiration.replace('-', '')}|{'P' if right == 'put' else 'C'}|{strike:g}"


def parse_key(key: str) -> tuple[str, str, str, float]:
    root, ymd, r, strike = key.split("|")
    return root, ymd, ("put" if r == "P" else "call"), float(strike)


def tick_round(x: float, tick: float = 0.05) -> float:
    return round(round(x / tick) * tick, 2)


class IBKRClient:
    def __init__(self, cfg: Ibkr, readonly: bool, underlying: str = "SPX", ib=None):
        if ib is None:
            from ib_async import IB  # optional dependency: pip install -e '.[ibkr]'
            ib = IB()
        self.ib, self.cfg, self.readonly, self.underlying = ib, cfg, readonly, underlying
        self._idx = None
        self._idx_ticker = None
        self._tickers: dict[str, object] = {}  # kept subscriptions for open-position monitoring

    # --- plumbing ----------------------------------------------------------
    def sleep(self, seconds: float) -> None:
        self.ib.sleep(seconds)

    def _ensure(self) -> None:
        if self.ib.isConnected():
            return
        log.info("connecting to IB at %s:%s (readonly=%s)", self.cfg.host, self.cfg.port, self.readonly)
        self.ib.connect(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id,
                        readonly=self.readonly, timeout=15)
        self.ib.reqMarketDataType(self.cfg.market_data_type)
        self._idx = self._idx_ticker = None
        self._tickers.clear()

    def _wait(self, cond) -> None:
        end = time.time() + self.cfg.wait_seconds
        while not cond() and time.time() < end:
            self.ib.sleep(0.2)

    @property
    def _sec_type(self) -> str:
        return "IND" if self.underlying in INDEXES else "STK"

    def _index(self):
        """The underlying contract (an index like SPX, or a stock/ETF like SPY) and its live ticker."""
        from ib_async import Index, Stock
        if self._idx is None:
            idx = Index(self.underlying, "CBOE") if self._sec_type == "IND" else Stock(self.underlying, "SMART", "USD")
            self.ib.qualifyContracts(idx)
            self._idx = idx
            self._idx_ticker = self.ib.reqMktData(idx, "", False, False)
        return self._idx, self._idx_ticker

    def _option_contract(self, key: str):
        from ib_async import Option
        root, ymd, right, strike = parse_key(key)
        return Option(self.underlying, ymd, strike, "P" if right == "put" else "C", "SMART",
                      currency="USD", tradingClass=root)

    # --- market data -------------------------------------------------------
    def get_quote(self, symbol: str) -> Quote:
        self._ensure()
        idx, t = self._index()
        self._wait(lambda: _px(t.marketPrice()) > 0)
        last = _px(t.marketPrice())
        if not last:
            raise RuntimeError(f"no {symbol} price from IB (market data subscription / market closed?)")
        open_, prev = _opt(t.open), _opt(t.close)
        if open_ is None or prev is None:  # index ticks don't always carry open/close: use daily bars
            bars = self.ib.reqHistoricalData(idx, "", "2 D", "1 day", "TRADES", True, 1)
            if bars:
                open_ = open_ or _opt(bars[-1].open)
                if len(bars) > 1:
                    prev = prev or _opt(bars[-2].close)
        return Quote(last=last, open=open_, prev_close=prev)

    def get_chain(self, symbol: str, expiration: str, root: str | None = None,
                  right: str | None = None, near: float | None = None) -> list[OptionQuote]:
        """Quotes+greeks for strikes near spot on the side we need (keeps within IB's market-data line limit).
        near: only strikes within this many points of spot, both sides (e.g. at-the-money SPY)."""
        self._ensure()
        idx, _ = self._index()
        spot = self.get_quote(symbol).last
        ymd = expiration.replace("-", "")
        root = root or symbol
        chains = self.ib.reqSecDefOptParams(symbol, "", self._sec_type, idx.conId)
        strikes = sorted({k for c in chains if c.tradingClass == root and ymd in c.expirations for k in c.strikes})
        w, buf = spot * self.cfg.strike_window_pct, self.cfg.strike_buffer
        want = []
        for k in strikes:
            if near is not None:
                if abs(k - spot) <= near:
                    want += [make_key(root, expiration, r, k) for r in ("put", "call") if right in (None, r)]
                continue
            if right in (None, "put") and spot - w - buf <= k <= spot:
                want.append(make_key(root, expiration, "put", k))
            if right in (None, "call") and spot <= k <= spot + w + buf:
                want.append(make_key(root, expiration, "call", k))
        if len(want) > 95:
            log.warning("%d strikes requested; IB default is ~100 market-data lines", len(want))
        return self._snapshot(want, keep=False)

    def get_bars(self, symbol: str, day: str, minutes: int) -> list[Bar]:
        """Intraday bars for `day` (must be today or recent), pre-market included, times in New York."""
        from zoneinfo import ZoneInfo
        self._ensure()
        idx, _ = self._index()
        size = f"{minutes} min" + ("s" if minutes > 1 else "")
        ny = ZoneInfo("America/New_York")
        from datetime import datetime
        end = "" if day == datetime.now(ny).date().isoformat() else f"{day.replace('-', '')} 20:00:00 US/Eastern"
        bars = self.ib.reqHistoricalData(idx, end, "1 D", size, "TRADES", False, 2)
        out = []
        for b in bars or []:
            t = b.date.astimezone(ny).strftime("%Y-%m-%dT%H:%M")
            if t.startswith(day) and t[11:] < "16:00":
                out.append(Bar(t, float(b.open), float(b.high), float(b.low), float(b.close)))
        return out

    def get_daily(self, symbol: str, start: str, end: str) -> list[Bar]:
        self._ensure()
        idx, _ = self._index()
        bars = self.ib.reqHistoricalData(idx, "", "10 D", "1 day", "TRADES", True, 1)
        out = [Bar(str(b.date)[:10], float(b.open), float(b.high), float(b.low), float(b.close)) for b in bars or []]
        return [b for b in out if start <= b.time <= end]

    def get_expirations(self, symbol: str) -> list[str]:
        self._ensure()
        idx, _ = self._index()
        chains = self.ib.reqSecDefOptParams(symbol, "", self._sec_type, idx.conId)
        ymd = {e for c in chains if c.tradingClass == symbol for e in c.expirations}
        return sorted(f"{e[:4]}-{e[4:6]}-{e[6:]}" for e in ymd)

    def get_option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]:
        self._ensure()
        return {q.symbol: q for q in self._snapshot(symbols, keep=True)}

    def _snapshot(self, keys: list[str], keep: bool) -> list[OptionQuote]:
        contracts = {k: self._option_contract(k) for k in keys if k not in self._tickers}
        if contracts:
            self.ib.qualifyContracts(*contracts.values())
        tickers = {}
        for k in keys:
            if k in self._tickers:
                tickers[k] = self._tickers[k]
            elif contracts[k].conId:  # unqualified = no such contract
                tickers[k] = self.ib.reqMktData(contracts[k], "", False, False)
        self._wait(lambda: all(t.modelGreeks is not None and (_px(t.bid) or _px(t.ask)) for t in tickers.values())
                   if tickers else True)
        out = []
        for k, t in tickers.items():
            _, _, right, strike = parse_key(k)
            g = t.modelGreeks
            out.append(OptionQuote(symbol=k, strike=strike, right=right, bid=_px(t.bid), ask=_px(t.ask),
                                   delta=(g.delta if g is not None and g.delta is not None else None)))
            if keep:
                self._tickers[k] = t
            else:
                self.ib.cancelMktData(t.contract)
        return out

    # --- execution ---------------------------------------------------------
    def _place(self, short_key: str, long_key: str, qty: int, price: float, opening: bool) -> str:
        from ib_async import ComboLeg, Contract, LimitOrder
        if self.readonly:
            raise RuntimeError("IB client is read-only (signal mode); refusing to place orders")
        self._ensure()
        s, l = self._option_contract(short_key), self._option_contract(long_key)
        self.ib.qualifyContracts(s, l)
        if not (s.conId and l.conId):
            raise RuntimeError("could not qualify spread legs with IB")
        legs = [
            ComboLeg(conId=s.conId, ratio=1, action="SELL" if opening else "BUY", exchange="SMART"),
            ComboLeg(conId=l.conId, ratio=1, action="BUY" if opening else "SELL", exchange="SMART"),
        ]
        bag = Contract(symbol=self.underlying, secType="BAG", currency="USD", exchange="SMART", comboLegs=legs)
        # IB prices a combo as net debit; a credit is a negative limit price.
        order = LimitOrder("BUY", qty, tick_round(-price if opening else price))
        order.tif = "DAY"
        trade = self.ib.placeOrder(bag, order)
        return str(trade.order.orderId)

    def open_spread(self, short_sym, long_sym, qty, credit) -> str:
        return self._place(short_sym, long_sym, qty, credit, opening=True)

    def close_spread(self, short_sym, long_sym, qty, debit) -> str:
        return self._place(short_sym, long_sym, qty, debit, opening=False)

    # --- order tracking ----------------------------------------------------
    def _find_trade(self, order_id):
        for t in self.ib.trades():
            if str(t.order.orderId) == str(order_id):
                return t
        return None

    def order_status(self, order_id: str) -> OrderStatus:
        self._ensure()
        t = self._find_trade(order_id)
        if t is None:  # e.g. after a restart: ask IB for open orders placed by any client
            self.ib.reqAllOpenOrders()
            self.ib.sleep(1)
            t = self._find_trade(order_id)
        if t is None:
            return OrderStatus("unknown")
        os_ = t.orderStatus
        if os_.status == "Filled":
            state = "filled"
        elif os_.status in ("Cancelled", "ApiCancelled"):
            state = "cancelled"
        elif os_.status == "Inactive":
            state = "rejected"
        else:  # PendingSubmit, PreSubmitted, Submitted, PendingCancel
            state = "working"
        avg = abs(float(os_.avgFillPrice)) if os_.avgFillPrice else None  # combo avg price is signed
        return OrderStatus(state, int(os_.filled or 0), avg)

    def cancel_order(self, order_id: str) -> None:
        t = self._find_trade(order_id)
        if t is not None:
            self.ib.cancelOrder(t.order)

    def replace_order(self, order_id: str, price: float, opening: bool) -> None:
        t = self._find_trade(order_id)
        if t is None:
            raise RuntimeError(f"order {order_id} not found; cannot reprice")
        t.order.lmtPrice = tick_round(-price if opening else price)
        self.ib.placeOrder(t.contract, t.order)  # same orderId = modify in place

    def positions(self) -> dict[str, int]:
        """Net quantity per option key (see make_key); short = negative."""
        self._ensure()
        out: dict[str, int] = {}
        for p in self.ib.positions():
            c = p.contract
            if c.secType != "OPT" or c.symbol != self.underlying or not p.position:
                continue
            right = "put" if c.right in ("P", "PUT") else "call"
            key = make_key(c.tradingClass, c.lastTradeDateOrContractMonth, right, c.strike)
            out[key] = out.get(key, 0) + int(p.position)
        return out
