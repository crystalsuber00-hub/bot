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
spxbot -c config.toml --check  # test connection, data and alerts; places no orders
```
## Auto-start every weekday
The bot can start itself each weekday morning and stop itself at 16:10 ET (`--until 16:10`); a second copy refuses to start. It sends "started" / "finished for the day" alerts, and an "unreachable" alert if it can't get broker data (e.g. IB Gateway isn't logged in).

1. `cp .env.example .env` and put your `NTFY_TOPIC` (and any tokens) in it.
2. Install the schedule (default: 09:15 ET, converted to your computer's local time automatically):
   - **Mac:** `bash scripts/install_mac.sh`
   - **Linux:** `bash scripts/install_linux.sh`
   - **Windows (PowerShell, in the bot folder):** `.\scripts\install_windows.ps1`
   Pass a different Eastern time as an argument, e.g. `bash scripts/install_mac.sh 09:10`. Re-run the installer after US/local daylight-saving changes if your country switches on different dates.
3. Every morning before that time: **log in to IB Gateway.** That step can't be automated by the bot (Gateway's daily login is a security feature; the community tool IBC can automate it if you want).
4. The computer has to be on and awake. The Mac script prints the `pmset` command to wake it; the Windows task is set to wake the computer.

Logs are in `logs/`. The Mac and Linux scripts were syntax-checked and the schedule file validated, but not run on real machines; the Windows script has not been run at all. If one misbehaves, run `scripts/run_bot.sh` (or `.ps1`) by hand and paste me the output.

## Tracking and the 2-week review
The bot records every decision automatically: `state.json` (each trade, its strikes, credit, exit and P&L, plus every no-trade day with the reason) and `history/*.csv` (spread price every poll). Nothing to switch on. After a couple of weeks:
```
python -m spxbot.backtest --refresh      # optional: lets the report compare against the model
spxbot -c config.toml --report           # everything recorded
spxbot -c config.toml --report --days 14 # just the last two weeks
```
The report shows coverage (weekdays the bot never saw), every trade, win rate, average win/loss, worst day, losing streak, drawdown, and how the bot's results compare with the backtest model on the same days. Back up `state.json` and `history/`; they are your record.

## Sitting out risky days
All optional, in `[strategy]`, and they apply in both signal and trade mode. Skipped days send a "NO TRADE today" alert with the reason.
- `pause_after_losses` / `pause_days` (default 2 / 3): after 2 losing trades in a row, sit out the next 3 trading days (Mon-Fri; market holidays aren't known to the bot). Each further loss triggers another pause.
- `skip_dates`: days you list (Fed decisions, CPI, jobs report, anything else). The bot has no economic calendar, so you maintain this list from federalreserve.gov and bls.gov.
- `max_move_pct`: skip if SPX has already moved more than this % by entry. `max_gap_pct`: skip if the open gapped more than this % vs the prior close. Both off by default; no values are tuned to the backtest, so choose them deliberately.

None of these prevent losses; they only cut how many trades you take after trouble starts. In the 60-day backtest the pause never triggered for the follow-the-move rule (no two losses in a row), so it changed nothing there.

## Trade mode: order tracking and safety
A position is only recorded as open when the broker confirms the fill, and P&L uses the actual fill prices.

| Situation | What the bot does |
|---|---|
| Entry order unfilled | Drops the limit 0.05 every 30s (up to 3 times, never more than 0.15 below the signal mid or under `min_credit`), then **cancels at the end of the entry window**. No position, no trade that day. |
| Partial fill at cancel | Keeps the filled quantity as the position. |
| Profit-target exit unfilled | Starts at the mid, raises 0.05 every 30s up to 3 times, then goes to the natural price (short ask − long bid). |
| Time / stop / daily-loss / kill exit | Urgent: starts at the natural price and keeps re-quoting. |
| Exit order rejected or cancelled | Re-sends. After `max_exit_attempts` (5) it **freezes** and alerts you. |
| Every poll | Compares the state file with the broker's real positions. Position closed elsewhere → marked closed. Any other mismatch → **freeze** (no orders until you fix it and run `spxbot --clear-halt`). Runs at startup too, so a restart mid-trade resumes correctly. |
| 15:45 ET | Time exit (`force_close_time`). If the bot is still running after 16:00 it estimates settlement from SPX and marks the trade expired; confirm at your broker. |
| Max daily loss (`max_daily_loss`, default $500) | Flattens if today's P&L, valued at the mid, reaches that loss. Real fills will be worse than the mid, so set it below what you can afford to lose. |
| Kill switch | `touch KILL` (path in `kill_file`): cancels a pending entry, blocks new ones, flattens open positions. |
| `max_total_loss` (off by default) | Halts new entries once cumulative realized losses reach the limit. |

Known limits: the checks only run while the bot is running (if the machine or Gateway dies, working orders stay live at the broker); the mid-based daily loss can't protect against gaps; if the broker times out after accepting an order the bot may not know about it (the startup check catches the resulting position, not a still-working order).

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

## SPY 0/3 live signals (second model)
A separate, **signal-only** model built from the *SPY 0/3 – DTE Timing Guide*: buy SPY calls/puts at level reactions, 0DTE in the morning, 3DTE in the afternoon. It never places orders; it sends alerts and paper-tracks each signal at the option mid.
```
cp config.spy03.example.toml spy03.toml
spxbot -c spy03.toml --check   # data, today's map, 0DTE/3DTE contract picks, test alert
spxbot -c spy03.toml           # live signals (--until 16:10 to stop by itself)
```
| Guide rule | What the bot does |
|---|---|
| Clock first: 9:30–11:30 0DTE, 11:30–1:00 no trade, 1:00–4:00 3DTE | New signals only inside the two windows. Alerts at 11:30 (middle checkpoint, thesis status) and 1:00 (3DTE window open or closed, and why). |
| The map: prior-day high/low, pre-market high/low, whole-dollar zones; keep only a few | Builds the map once at the open, merges levels within $0.30, keeps your `manual_levels` plus the nearest few (`max_zones`), and sends it as the first alert. |
| A touch is not a trade | Needs a completed 5-min bar that touches the level and closes ≥ $0.10 past it with a decisive body (≥ 50% of the range). Wicks, chop around the level, moves already > $0.60 past it, and < 1.5:1 room to the next zone are **passed on**, and you get a "PASS" alert saying why (the guide's "trade I didn't take"). |
| Bullish → call, bearish → put; invalidation before entry | Invalidation = level ∓ $0.45 on a bar close; target = next zone. Both are in the entry alert. |
| Contract fits the window | Morning: 0DTE. Afternoon: first expiration ≥ 3 trading days out. Nearest-the-money strike with delta 0.40–0.60, bid/ask spread ≤ 10% of mid, premium $0.30–$5. |
| Risk: ~1% per idea, daily loss cap, no adding | Contracts = 1% of `account_size` ÷ (premium × 100 × 20% stop). Skips if 1 contract is over budget. One signal open at a time, 1 morning + 1 afternoon max, stops after `max_daily_loss`. |
| 3DTE is continuation; no revenge | Afternoon signals need an intact morning thesis in the same direction. A losing morning signal or a morning thesis that was invalidated closes the afternoon. |
| Exits: symmetric 20% target / 20% stop | Exits on option mid +20% / −20%, SPY closing through invalidation, SPY reaching the next zone, 0DTE at 12:00, 3DTE at 15:55 (`three_dte_exit_time = ""` to carry overnight). |

### Backtest (free data, model prices)
```
python -m spxbot.spy03_backtest --refresh            # SPY 5-min bars + VIX1D/VIX9D from Yahoo into data/
python -m spxbot.spy03_backtest -c spy03.toml        # test your own [spy03] settings
```
Replays the live engine bar by bar. Option prices are Black-Scholes estimates (VIX1D for 0DTE, VIX9D for 3DTE, $0.02 spread, $0.65/contract commission) and exits are checked on 5-minute closes, so fast moves overshoot the 20% stop. First run (59 days, Jul 8 - Sep 29 2026): 48 trades, 48% win rate, about breakeven after costs; stricter filters cut the trade count without raising the win rate. Treat the default rules as unproven.

### Using a Schwab account for the data (free, real-time)
1. Go to [developer.schwab.com](https://developer.schwab.com), sign up (a separate developer login), and create an app under *Dashboard → Apps → Create App*: API product **Accounts and Trading Production** (includes market data), Callback URL exactly `https://127.0.0.1`. Wait until its status is **Ready For Use** (Schwab reviews new apps; it can take a few days).
2. Put the app's key and secret in `.env`: `SCHWAB_APP_KEY=...`, `SCHWAB_APP_SECRET=...`, plus `SPXBOT_CONFIG=spy03.toml` and your `NTFY_TOPIC`.
3. `set -a; . ./.env; set +a` then `spxbot -c spy03.toml --schwab-login`: open the printed link, log in with your **brokerage** login, allow access, and paste back the address of the error page it lands on. The token is saved in `schwab_token.json` (keep it private).
4. `spxbot -c spy03.toml --check`, then run it (or install the weekday auto-start; it uses `SPXBOT_CONFIG`).

Schwab's login lasts **7 days** and can't be extended: repeat step 3 once a week. The bot alerts you when less than 1.5 days are left, and sends "can't reach the broker/data" if it has expired. The Schwab connection is read-only market data; the bot doesn't use any account or trading endpoints. It was tested against recorded responses, not a live Schwab session yet.

What's mine, not the guide's: the guide says "what counts as clean" is a live judgement call, so every number in the reaction rules (tolerances, body ratio, chase distance, invalidation buffer, 1.5:1 room) and the 12:00 0DTE cutoff are my mechanical stand-ins; tune them in `spy03.toml`. The pass/fail rules are unit-tested on made-up bars only, not backtested, and haven't run against live Tradier/IB data. The guide's 73% win rate and P&L figures are the author's claims and say nothing about how these rules will do. Tradier needs a production token (sandbox data is delayed); IB needs SPY + OPRA data and a `client_id` different from the SPX bot's.

## 0DTE stock option signals (small accounts)
Same level -> reaction rules as SPY 0/3, run on a watchlist of high-volume stocks and ETFs (default: SPY, QQQ, IWM, META, AAPL, TSLA, MSFT, NVDA, AMZN, AMD, GOOGL, PLTR; any share price). Every signal is **one 0DTE contract that costs less than $200** at the moment of the signal. Signal-only: alerts and paper tracking, no orders.
```
cp config.stocks.example.toml stocks.toml
spxbot -c stocks.toml --check   # per ticker: price, today's expiration, the call and put it would pick under $200
spxbot -c stocks.toml           # live (add --until 16:10 to stop by itself)
```
- **9:40 map alert:** every ticker's price, change vs yesterday and its levels (prior-day high/low/close, pre-market high/low). Tickers with no option expiring today are marked and skipped that day; which stocks have same-day expirations depends on the exchange listing that week.
- **Signal (9:40-11:30):** a decisive 5-min bar off or through a level; bullish = call, bearish = put. Distances scale with each stock's average 5-min range, so the same rules fit IWM and TSLA.
- **Strike:** the one nearest the money whose contract is under $200 right now, with a bid/ask spread under 10% of the price and delta 0.20-0.60. For expensive stocks this moves out of the money; if nothing fits, no signal.
- **Entry alert:** contract, strike, cost, bid/ask and spread, delta, stock price vs the open, why, take-profit and stop in both option and stock prices, time exit, most you can lose, the stock's levels.
- **Exits:** option +20% / -20%, the stock closing a 5-min bar back through the level, the stock reaching the next level, or 15:50. Exit alerts give price, P&L and minutes held.
- One signal open at a time, at most 2 a day.

Not backtested: there's no free history of single-stock option prices. The same rules on SPY backtested as roughly breakeven (see SPY 0/3 above), so paper trade these first. A 0DTE contract can go to $0 the same day; "under $200" is the most you can lose on one signal.

## Assumptions to check
- "Price movement" = SPX now vs. today's open at entry time. Set `reference = "prev_close"` for gap-inclusive, and `min_move_pct` to skip flat days.
- Defaults are 0DTE SPXW, $10 wide, 1 contract, min credit $0.50 — none of these were in your description, so adjust.
- No stop loss or time exit by default (you didn't mention one); `stop_loss_multiple` and `force_close_time` are available.
- Weekdays only; market holidays aren't checked (no chain → the day is skipped). Early-close days aren't special-cased.
- Trade mode has never run against a real broker (only fake ones in tests). Verify on a paper account first.
- Not financial advice; options can lose more than the credit received.
