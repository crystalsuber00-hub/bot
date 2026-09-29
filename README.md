# spxbot

Signal bot for a SPX credit-spread routine:

| Your rule | Implementation |
|---|---|
| Sell the side price is moving away from (this leans WITH the day's direction, not against it): SPX up → put credit spread, SPX down → call credit spread | `strategy.choose_side` compares SPX to today's open (or prior close) |
| 15–20 delta short strike | Picks the strike with \|delta\| in [0.15, 0.20] closest to 0.175; long leg `spread_width` points further out |
| Exit at 50–60% of credit | Monitors the spread mid; exits when captured credit ≥ `profit_target` (0.50–0.60) |
| One trade a day, 10–15 min after the open | Single entry attempt in 09:40–09:45 ET; state file guarantees one trade per date |

Data and orders come from [Tradier](https://tradier.com) (SPX quotes + option chain with greeks).

## Modes
- `signal` (default): sends entry/exit alerts (console, ntfy, Telegram, Discord, and/or a JSON webhook for your own service) and paper-tracks the position. No orders.
- `trade`: also places multileg limit orders at the mid. **Start on the Tradier sandbox** (`sandbox = true`).

## Run
```
pip install -e .
cp config.example.toml config.toml
export TRADIER_TOKEN=... NTFY_TOPIC=my-secret-topic TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
spxbot -c config.toml          # long-running loop
spxbot -c config.toml --once   # single tick, e.g. from cron every minute
```
## Spread chart
While a trade is open, each poll logs the spread's mid price to `history/YYYY-MM-DD.csv`. Draw it (option candles are gappy; this uses bid/ask mid so it's smooth):
```
spxbot -c config.toml --chart              # latest day
spxbot -c config.toml --chart 2026-09-30   # specific day -> history/2026-09-30.html
```
Open the HTML in a browser: blue line = cost to buy back the spread, grey dashes = entry credit, green dashes = exit target.

## Backtest (free data, model prices)
```
python -m spxbot.backtest --refresh     # pulls SPX 5-min bars + VIX1D from Yahoo into data/, then simulates
```
Yahoo only serves ~60 days of 5-minute bars, so `data/` accumulates: re-run `--refresh` regularly and the sample grows. Options are **Black-Scholes estimates** (VIX1D as implied vol, crude put/call skew, $0.10 slippage, $0.65/leg commission), not real quotes, and stops are checked on 5-minute closes. It compares trading with the move, against it, and one-sided baselines.

Tests: `pip install -e '.[dev]' && pytest`

## Assumptions to check
- "Price movement" = SPX now vs. today's open at entry time. Set `reference = "prev_close"` for gap-inclusive, and `min_move_pct` to skip flat days.
- Defaults are 0DTE SPXW, $10 wide, 1 contract, min credit $0.50 — none of these were in your description, so adjust.
- No stop loss or time exit by default (you didn't mention one); `stop_loss_multiple` and `force_close_time` are available.
- Weekdays only; market holidays aren't checked (no chain → the day is skipped). Early-close days aren't special-cased.
- Orders are day limits at the mid and are not re-priced or fill-tracked; the bot assumes a fill. Verify in sandbox before going live.
- Not financial advice; options can lose more than the credit received.
