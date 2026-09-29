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

## Assumptions to check
- "Price movement" = SPX now vs. today's open at entry time. Set `reference = "prev_close"` for gap-inclusive, and `min_move_pct` to skip flat days.
- Defaults are 0DTE SPXW, $10 wide, 1 contract, min credit $0.50 — none of these were in your description, so adjust.
- No stop loss or time exit by default (you didn't mention one); `stop_loss_multiple` and `force_close_time` are available.
- Weekdays only; market holidays aren't checked (no chain → the day is skipped). Early-close days aren't special-cased.
- Trade mode has never run against a real broker (only fake ones in tests). Verify on a paper account first.
- Not financial advice; options can lose more than the credit received.

---

# cryptobot (paper trading only)

A separate, honest crypto tool. It places **no real orders**; nothing here can spend money.

```
python -m cryptobot backtest                       # ~2 years of hourly BTC-USD from Coinbase, cached in data/
python -m cryptobot paper --strategy breakout --params '{"entry":168,"exit":96}'   # loops hourly, state in crypto_state.json
```
- Strategies are long/flat (`sma_cross`, `breakout`). Parameters are picked on the first 60% of the data and scored on the unseen last 40%; trust only the OUT-OF-SAMPLE line. Costs are 0.4% fee + 0.05% slippage per side.
- Result on the 2026-09 run: buy & hold lost 10% out-of-sample; both strategies also lost (-9% and -4%) with much smaller drawdowns. **Neither made money.** That is the point of the tool: it shows this kind of strategy has no demonstrated edge, before you risk anything.
- Test other coins with `--product ETH-USD`. Don't go live unless a strategy is profitable out-of-sample across several coins and periods, and even then past results don't predict future ones.

`python -m cryptobot robust` runs the breakout rule with fixed parameters over four non-overlapping windows for five coins (20 tests, nothing tuned): 10 of 20 were profitable, i.e. a coin flip. It beat buy & hold mainly in the one crashing window, by being in cash. The SOL out-of-sample +17% did not hold up: the same rule lost 3% and 27% in two of SOL's four windows.

`python -m cryptobot gate` is a pre-registered validation gate (rules in `cryptobot/gate.py`, fixed before running): select on the first 75% of data across all five coins, log every rejected trial, and let only one survivor touch the last 25% once. Results so far: round 1 had 0 of 18 survivors; round 2 added vol_trend, regime_trend and dip_buy and had 0 of 30, so the holdout was never used and there is nothing to deploy. Don't loosen the rules to get a pass; add genuinely new strategy ideas instead.
