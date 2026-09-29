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
    status: str = "open"  # open | closed
    exit_time: Optional[str] = None
    exit_debit: Optional[float] = None
    exit_reason: Optional[str] = None
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None

    def pnl(self, debit: float) -> float:
        """Dollar P&L for the whole position at a given buy-back debit."""
        return (self.credit - debit) * 100 * self.quantity

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DayRecord:
    position: Optional[Position] = None
    skipped_reason: Optional[str] = None
    extra: dict = field(default_factory=dict)
