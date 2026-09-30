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
class Schwab:
    app_key: str = ""            # prefer env SCHWAB_APP_KEY
    app_secret: str = ""         # prefer env SCHWAB_APP_SECRET
    callback_url: str = "https://127.0.0.1"   # must match the Callback URL of your app on developer.schwab.com
    token_file: str = "schwab_token.json"


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
class Spy03:
    """SPY 0/3 model: 0DTE in the morning window, 3DTE in the afternoon window, nothing in between.
    Signal-only: alerts + paper tracking, never places orders."""
    symbol: str = "SPY"
    state_file: str = "spy03_state.json"
    # the clock (Eastern time)
    zero_dte_start: str = "09:30"
    zero_dte_end: str = "11:30"      # last new 0DTE entry
    three_dte_start: str = "13:00"
    three_dte_end: str = "15:30"     # last new 3DTE entry (a later entry is a "late entry")
    zero_dte_exit_time: str = "12:00"  # 0DTE still open at this time is closed: hold the reaction, not the hope
    three_dte_exit_time: str = "15:55"  # "" = let 3DTE positions carry overnight
    three_dte_days: int = 3          # trading days to expiration for the afternoon contract
    bar_minutes: int = 5
    # the map
    manual_levels: list = field(default_factory=list)  # your own levels for today, e.g. [650, 652.5]
    use_prior_day: bool = True       # prior day high / low / close
    use_premarket: bool = True       # 04:00-09:30 high / low
    round_step: float = 5.0          # whole-dollar zones every N dollars (0 = off)
    max_zones: int = 4               # three-zone rule: keep only the nearest few (manual levels always kept)
    merge_distance: float = 0.30     # levels closer than this are one zone
    # the reaction
    touch_tolerance: float = 0.10    # bar must come within this of the level
    confirm_distance: float = 0.10   # and close at least this far beyond it
    min_body_ratio: float = 0.5      # decisive bar: body >= this share of the bar's range (not just a wick)
    chop_lookback: int = 6           # bars checked for chop around the level
    max_level_crosses: int = 3       # this many closes flipping sides of the level = messy, skip
    max_chase: float = 0.60          # price already this far past the level = the move already happened, skip
    invalidation_buffer: float = 0.45  # thesis is wrong if SPY closes this far back through the level
    min_reward_risk: float = 1.5     # room to the next zone vs distance to invalidation
    default_reward_risk: float = 2.0  # target when there is no next zone
    # the contract
    min_delta: float = 0.40          # near-the-money
    max_delta: float = 0.60
    max_spread_pct: float = 0.10     # bid/ask spread / mid (liquidity check)
    min_premium: float = 0.30
    max_premium: float = 5.00
    # the risk
    premium_target_pct: float = 0.20  # exit when the option is up this much ...
    premium_stop_pct: float = 0.20    # ... or down this much (symmetric 20/20)
    account_size: float = 10000
    risk_pct: float = 0.01            # of account per idea
    max_contracts: int = 10
    max_morning_trades: int = 1
    max_afternoon_trades: int = 1
    max_daily_loss: float = 200       # $; no new signals once today's realized loss reaches this. 0 = off
    require_morning_thesis: bool = True  # 3DTE only continues a morning thesis that is still intact
    skip_dates: list = field(default_factory=list)


@dataclass
class Stocks:
    """0DTE option signals on high-volume stocks/ETFs: SPY 0/3 level -> reaction rules, one contract under
    `max_contract_cost`. Signal-only: alerts + paper tracking, never places orders."""
    watchlist: list = field(default_factory=lambda: [
        "SPY", "QQQ", "IWM", "META", "AAPL", "TSLA", "MSFT", "NVDA", "AMZN", "AMD", "GOOGL", "PLTR"])
    max_contract_cost: float = 150.0  # $; one contract must cost LESS than this at the moment of the signal
    min_conviction: int = 6           # alert only setups that pass this many of the 7 conviction checks
    market_symbols: list = field(default_factory=lambda: ["SPY", "QQQ"])  # "market agrees" check
    contracts: int = 1
    state_file: str = "stocks_state.json"
    entry_start: str = "09:40"
    entry_end: str = "11:30"
    exit_time: str = "15:50"          # 0DTE: out before the close
    max_trades_per_day: int = 2       # one open at a time
    bar_minutes: int = 5
    # the contract: nearest the money that fits the budget, not further out than min_delta
    min_delta: float = 0.20
    max_delta: float = 0.60
    max_spread_pct: float = 0.10      # bid/ask spread / mid
    min_premium: float = 0.10
    premium_target_pct: float = 0.50  # take profit at +50% on the option
    premium_stop_pct: float = 0.20    # stop at -20% on the option
    warn_pct: float = 0.80            # "almost there" alert at 80% of the way to the target or the stop
    # reaction rules, in units of the stock's average 5-min bar range (so they fit SPY, IWM or TSLA alike)
    touch_atr: float = 0.2
    confirm_atr: float = 0.2
    max_chase_atr: float = 1.0
    stop_atr: float = 0.8             # the idea is wrong if the stock closes this far back through the level
    min_body_ratio: float = 0.5
    min_reward_risk: float = 1.5
    default_reward_risk: float = 2.0
    use_premarket: bool = True
    strong_body_ratio: float = 0.7    # conviction check: candle body >= this share of its range
    strong_reward_risk: float = 2.0   # conviction check: room to the next level >= this x the risk


@dataclass
class Config:
    model: str = "spx_credit"    # "spx_credit" (the credit-spread routine) or "spy03" (SPY 0/3 signals)
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
    schwab: Schwab = field(default_factory=Schwab)
    notify: Notify = field(default_factory=Notify)
    spy03: Spy03 = field(default_factory=Spy03)
    stocks: Stocks = field(default_factory=Stocks)

    def validate(self) -> None:
        s = self.strategy
        if self.broker not in ("tradier", "ibkr", "schwab"):
            raise ValueError("broker must be 'tradier', 'ibkr' or 'schwab'")
        if self.model == "spx_credit" and self.mode == "trade" and self.broker == "tradier" and self.symbol != "SPX":
            raise ValueError("Tradier trade mode only supports SPX; use signal mode (alerts) for XSP")
        if self.broker == "schwab" and self.model not in ("spy03", "stocks"):
            raise ValueError("broker 'schwab' is data-only and currently supported for models 'spy03' and 'stocks'")
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
        if self.model not in ("spx_credit", "spy03", "stocks"):
            raise ValueError("model must be 'spx_credit', 'spy03' or 'stocks'")
        k = self.stocks
        if not k.watchlist or k.max_contract_cost <= 0 or k.contracts < 1 or not 0 < k.min_delta <= k.max_delta < 1:
            raise ValueError("stocks: need a watchlist, max_contract_cost > 0, contracts >= 1, 0 < min_delta <= max_delta < 1")
        if not 0 < k.premium_stop_pct < 1 or k.premium_target_pct <= 0:
            raise ValueError("stocks: premium_stop_pct must be in (0, 1) and premium_target_pct > 0")
        if not 0 <= k.min_conviction <= 7:
            raise ValueError("stocks.min_conviction must be 0-7")
        if not k.entry_start < k.entry_end <= k.exit_time:
            raise ValueError("stocks: need entry_start < entry_end <= exit_time")
        m = self.spy03
        hhmm = r"([01]\d|2[0-3]):[0-5]\d"
        for k in ("zero_dte_start", "zero_dte_end", "three_dte_start", "three_dte_end", "zero_dte_exit_time"):
            if not re.fullmatch(hhmm, getattr(m, k)):
                raise ValueError(f"spy03.{k} must be HH:MM")
        if m.three_dte_exit_time and not re.fullmatch(hhmm, m.three_dte_exit_time):
            raise ValueError("spy03.three_dte_exit_time must be HH:MM or empty")
        if not m.zero_dte_start < m.zero_dte_end <= m.three_dte_start < m.three_dte_end:
            raise ValueError("spy03 windows must be in order: 0DTE start < 0DTE end <= 3DTE start < 3DTE end")
        if not 0 < m.min_delta <= m.max_delta < 1:
            raise ValueError("spy03 needs 0 < min_delta <= max_delta < 1")
        if not 0 < m.premium_stop_pct < 1 or m.premium_target_pct <= 0:
            raise ValueError("spy03 premium_stop_pct must be in (0, 1) and premium_target_pct > 0")
        if m.bar_minutes not in (1, 5, 15) or m.three_dte_days < 1 or m.max_contracts < 1:
            raise ValueError("spy03.bar_minutes must be 1, 5 or 15; three_dte_days and max_contracts >= 1")
        if m.account_size <= 0 or not 0 < m.risk_pct < 1:
            raise ValueError("spy03.account_size must be positive and risk_pct in (0, 1)")
        for d in m.skip_dates:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)):
                raise ValueError(f"spy03.skip_dates entry {d!r} must be YYYY-MM-DD")


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
        for k in ("schedule", "strategy", "execution", "tradier", "ibkr", "schwab", "notify", "spy03", "stocks"):
            _fill(getattr(cfg, k), data.pop(k, {}))
        _fill(cfg, data)
    env = os.environ.get
    cfg.tradier.token = env("TRADIER_TOKEN", cfg.tradier.token)
    cfg.tradier.account_id = env("TRADIER_ACCOUNT_ID", cfg.tradier.account_id)
    cfg.schwab.app_key = env("SCHWAB_APP_KEY", cfg.schwab.app_key)
    cfg.schwab.app_secret = env("SCHWAB_APP_SECRET", cfg.schwab.app_secret)
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
