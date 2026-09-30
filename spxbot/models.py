from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Quote:
    last: float
    open: Optional[float] = None
    prev_close: Optional[float] = None


@dataclass
class OptionQuote:
    symbol: str
    strike: float
    right: str  # "call" | "put"
    bid: float
    ask: float
    delta: Optional[float] = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class OrderStatus:
    state: str  # working | filled | cancelled | rejected | unknown
    filled_qty: int = 0
    avg_price: Optional[float] = None  # net per-share price actually achieved (always positive)


@dataclass
class Position:
    id: str
    date: str
    side: str  # "put_credit" | "call_credit"
    expiration: str
    short_symbol: str
    long_symbol: str
    short_strike: float
    long_strike: float
    credit: float  # per share, mid at entry
    quantity: int
    short_delta: float
    spx_at_entry: float
    entry_time: str
    status: str = "open"  # pending_entry | open | pending_exit | closed | cancelled
    exit_time: Optional[str] = None
    exit_debit: Optional[float] = None
    exit_reason: Optional[str] = None
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    # order-tracking bookkeeping
    est_credit: float = 0.0        # mid credit when the signal fired
    entry_limit: float = 0.0
    exit_limit: float = 0.0
    order_ts: str = ""             # when the working order was placed / last repriced
    nudges: int = 0
    cancel_requested: bool = False
    filled_at: str = ""
    exit_kind: str = ""            # profit | stop | time | kill
    exit_attempts: int = 0

    def pnl(self, debit: float) -> float:
        """Dollar P&L for the whole position at a given buy-back debit."""
        return (self.credit - debit) * 100 * self.quantity

    def realized(self) -> float:
        if self.status != "closed" or self.exit_debit is None:
            return 0.0
        return self.pnl(self.exit_debit)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DayRecord:
    position: Optional[Position] = None
    skipped_reason: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class Bar:
    time: str  # bar start, "YYYY-MM-DDTHH:MM" in exchange time (America/New_York)
    open: float
    high: float
    low: float
    close: float
