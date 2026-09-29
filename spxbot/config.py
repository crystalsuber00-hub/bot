from __future__ import annotations

import os
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
    force_close_time: str = ""


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
    poll_seconds: int = 30
    schedule: Schedule = field(default_factory=Schedule)
    strategy: Strategy = field(default_factory=Strategy)
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
        for k in ("schedule", "strategy", "tradier", "ibkr", "notify"):
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
