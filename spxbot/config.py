from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Schedule:
    timezone: str = "America/New_York"
    market_open: str = "09:30"
    entry_delay_minutes: int = 10
    entry_window_minutes: int = 5


@dataclass
class Strategy:
    target_delta_min: float = 0.15
    target_delta_max: float = 0.20
    target_delta: float = 0.175
    spread_width: float = 10
    min_credit: float = 0.50
    min_move_pct: float = 0.0
    reference: str = "open"
    quantity: int = 1
    profit_target: float = 0.50
    stop_loss_multiple: float = 0
    max_move_pct: float = 0.0    # skip days already moved more than this % by entry (0 = off)
    max_gap_pct: float = 0.0     # skip days that gapped more than this % vs prior close (0 = off)
    skip_dates: list = field(default_factory=list)   # "YYYY-MM-DD" days to sit out (Fed, CPI, jobs report...)
    pause_after_losses: int = 2  # after this many losing trades in a row, sit out for pause_days (0 = off)
    pause_days: int = 3          # trading days (Mon-Fri) to sit out
    force_close_time: str = "15:45"   # flatten before the close; "" = off


@dataclass
class Execution:
    nudge_seconds: int = 30          # wait this long between price adjustments of a working order
    nudge_step: float = 0.05         # $/share per adjustment
    max_nudges: int = 3              # entry: gives up conceding after this; exit: then goes to the natural price
    max_entry_concession: float = 0.15   # never accept less than mid credit minus this
    fill_grace_seconds: int = 90     # don't flag a missing position right after a fill (broker lag)
    max_exit_attempts: int = 5       # rejected/cancelled exit orders before freezing for manual action
    kill_file: str = "KILL"          # create this file to stop entries and flatten everything
    max_daily_loss: float = 500.0    # $; flatten if today's P&L (at mid) reaches this loss. 0 = off
    max_total_loss: float = 0.0      # $; halt new entries once cumulative realized loss reaches this. 0 = off


@dataclass
class Tradier:
    token: str = ""
    account_id: str = ""
    sandbox: bool = True


@dataclass
class Ibkr:
    host: str = "127.0.0.1"
    port: int = 4002             # IB Gateway paper 4002 / live 4001; TWS paper 7497 / live 7496
    client_id: int = 17
    market_data_type: int = 1    # 1 live, 2 frozen, 3 delayed, 4 delayed-frozen
    strike_window_pct: float = 0.03   # scan strikes within this fraction of spot (saves market-data lines)
    strike_buffer: float = 30    # extra points beyond the window so the long leg is included
    wait_seconds: float = 6.0    # how long to wait for quotes/greeks to arrive


@dataclass
class Notify:
    console: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    webhook_url: str = ""
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"
    ntfy_token: str = ""


@dataclass
class Config:
    mode: str = "signal"
    broker: str = "tradier"      # "tradier" or "ibkr"
    symbol: str = "SPX"
    option_root: str = "SPXW"
    dte: int = 0
    state_file: str = "state.json"
    history_dir: str = "history"
    lock_port: int = 47201       # localhost port used to stop a second copy of the bot starting
    poll_seconds: int = 30
    schedule: Schedule = field(default_factory=Schedule)
    strategy: Strategy = field(default_factory=Strategy)
    execution: Execution = field(default_factory=Execution)
    tradier: Tradier = field(default_factory=Tradier)
    ibkr: Ibkr = field(default_factory=Ibkr)
    notify: Notify = field(default_factory=Notify)

    def validate(self) -> None:
        s = self.strategy
        if self.broker not in ("tradier", "ibkr"):
            raise ValueError("broker must be 'tradier' or 'ibkr'")
        if self.mode not in ("signal", "trade"):
            raise ValueError("mode must be 'signal' or 'trade'")
        if not 0 < s.target_delta_min <= s.target_delta <= s.target_delta_max < 1:
            raise ValueError("need 0 < delta_min <= target_delta <= delta_max < 1")
        if not 0 < s.profit_target < 1:
            raise ValueError("profit_target must be between 0 and 1")
        if s.reference not in ("open", "prev_close"):
            raise ValueError("reference must be 'open' or 'prev_close'")
        if s.force_close_time and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s.force_close_time):
            raise ValueError("force_close_time must be HH:MM or empty")
        for d in s.skip_dates:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)):
                raise ValueError(f"skip_dates entry {d!r} must be YYYY-MM-DD")
        if s.pause_after_losses < 0 or s.pause_days < 0 or s.max_move_pct < 0 or s.max_gap_pct < 0:
            raise ValueError("pause/filter settings can't be negative")
        e = self.execution
        if e.nudge_step <= 0 or e.max_nudges < 0 or e.nudge_seconds < 1 or e.max_daily_loss < 0:
            raise ValueError("invalid [execution] settings")
        if s.spread_width <= 0 or s.quantity < 1:
            raise ValueError("spread_width and quantity must be positive")


def _fill(dc, data: dict):
    for k, v in (data or {}).items():
        if not hasattr(dc, k):
            raise ValueError(f"unknown config key: {k}")
        setattr(dc, k, v)
    return dc


def load_config(path: str | None) -> Config:
    cfg = Config()
    if path:
        data = tomllib.loads(Path(path).read_text())
        for k in ("schedule", "strategy", "execution", "tradier", "ibkr", "notify"):
            _fill(getattr(cfg, k), data.pop(k, {}))
        _fill(cfg, data)
    env = os.environ.get
    cfg.tradier.token = env("TRADIER_TOKEN", cfg.tradier.token)
    cfg.tradier.account_id = env("TRADIER_ACCOUNT_ID", cfg.tradier.account_id)
    cfg.ibkr.host = env("IBKR_HOST", cfg.ibkr.host)
    cfg.ibkr.port = int(env("IBKR_PORT", cfg.ibkr.port))
    n = cfg.notify
    n.telegram_bot_token = env("TELEGRAM_BOT_TOKEN", n.telegram_bot_token)
    n.telegram_chat_id = env("TELEGRAM_CHAT_ID", n.telegram_chat_id)
    n.discord_webhook_url = env("DISCORD_WEBHOOK_URL", n.discord_webhook_url)
    n.webhook_url = env("WEBHOOK_URL", n.webhook_url)
    n.ntfy_topic = env("NTFY_TOPIC", n.ntfy_topic)
    n.ntfy_server = env("NTFY_SERVER", n.ntfy_server)
    n.ntfy_token = env("NTFY_TOKEN", n.ntfy_token)
    cfg.validate()
    return cfg
