# spxbot

Signal bot for a SPX credit-spread routine:

| Your rule | Implementation |
|---|---|
| Sell the side price is moving away from (this leans WITH the day's direction, not against it): SPX up → put credit spread, SPX down → call credit spread | `strategy.choose_side` compares SPX to today's open (or prior close) |
| 15–20 delta short strike | Picks the strike with \|delta\| in [0.15, 0.20] closest to 0.175; long leg `spread_width` points further out |
| Exit at 50–60% of credit | Monitors the spread mid; exits when captured credit ≥ `profit_target` (0.50–0.60) |
| One trade a day, 10–15 min after the open | Single entry attempt in 09:40–09:45 ET; state file guarantees one trade per date |

Data and orders come from either [Tradier](https://tradier.com) (`broker = "tradier"`) or Interactive Brokers (`broker = "ibkr"`), both providing SPX quotes and option chains with greeks.

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
## Interactive Brokers setup
```
pip install -e '.[ibkr]'
```
1. Install **IB Gateway** (or TWS), log in to your **paper** account first, and enable the API: *Configure → Settings → API → Enable ActiveX and Socket Clients*, keep **Read-Only API** on for signal mode, and note the socket port (Gateway paper 4002, live 4001; TWS paper 7497, live 7496).
2. Your IB account needs real-time data subscriptions covering the SPX index and US options (OPRA). Subscription names change; check *Account Management → Market Data Subscriptions*. Without them the bot errors out with "no SPX price". Delayed data can be selected with `market_data_type = 3`, but that defeats a 9:40 entry.
3. Set `broker = "ibkr"` in `config.toml` and run `spxbot -c config.toml`.

Notes: signal mode connects **read-only**, so the client physically cannot place orders. In trade mode the spread goes in as one combo (BAG) limit order at the mid (a credit is a negative limit price in IB's convention). Gateway must stay running and logged in; it restarts daily, and the bot reconnects on the next poll. The bot only requests strikes near spot on the needed side, to stay under IB's market-data line limit. The IB path is unit-tested against a fake IB connection only; it has not been run against a real Gateway.

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
