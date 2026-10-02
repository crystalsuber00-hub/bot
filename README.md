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

# polyscan: Polymarket top-wallet scanner

A second, separate tool in this repo. It finds the most profitable Polymarket wallets over a trailing window (default 90 days), profiles each one position by position, scores how copyable their results look, and can send you an alert when the best ones trade. It reads Polymarket's public data API only: no account, no keys, no orders.

```
pip install -e .
polyscan scan                      # ~10 min: writes polyscan_out/{index.html,wallets.csv,scan.json}
polyscan report                    # re-render index.html from the saved scan.json
polyscan watch --top 15            # poll the 15 best-scoring non-bot wallets every 60s and alert
polyscan watch --wallet 0xabc... --min-usd 5000 --once
```
`watch` uses the same alert channels as spxbot (`NTFY_TOPIC`, `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`, `WEBHOOK_URL`). It sends one alert per wallet, outcome and side per poll (fills of one order are added up), skips trades under `--min-usd`, and sends a **CONSENSUS** alert when two or more watched wallets buy the same outcome within 24 hours. The first poll only records where each wallet is; it never replays history.

## How a scan works
1. **Candidate pool.** Polymarket has day, week, month and all-time leaderboards but no 90-day one, so the scan pulls the all-time top 1,000, the monthly top 500 by P&L, the monthly and weekly leaders by volume and P&L, and the top 100 of nine categories (about 2,300 wallets).
2. **Trailing P&L.** For every wallet it reads the daily cumulative P&L series (realized plus mark-to-market, the curve on a Polymarket profile) and takes today minus 90 days ago. Drawdown, daily Sharpe, green-week rate and best-day share come from the same curve.
3. **Profiles.** For the top 100 it reads closed positions, current positions and trades in the window. Losing tokens that were never redeemed are counted as losses; most traders never redeem losers, so skipping them makes everyone look like a 90% winner.
4. **Copy score (0-100).** Profit percentile 20%, consistency 20%, breadth (not one lucky day, enough positions) 15%, drawdown versus profit 15%, realized ROI 15%, followability 15%. Followability penalises bot-speed trading (more than 25 different markets a day; fill counts are misleading because one big order fills against dozens of makers), buying near-certain favorites, and wallets that have not traded for 14 days. Bots and favorite-buyers are penalised because someone copying their trades minutes later gets a worse price.
5. **Flags.** `one-big-day`, `one-big-bet`, `bot-speed`, `favorite-scalper`, `longshot-hunter`, `new-wallet`, `deep-drawdown`, `small-sample`, `cooling-off` (up over 90 days, down over 30), `dormant` (no trade in 14 days; `watch` skips these).

Limits: high-frequency wallets are sampled (latest 5,000 fills, 3,000 closed positions), market categories come from slugs and titles, and the copy score ranks past results. A wallet can stop trading or change style at any time, and copying a trade later gets a worse price than they got. Not financial advice.

## Copy trading on Polymarket US (paper first, then live)
`polyscan watch --autotrade` copies the watched wallets' game-winner bets onto **Polymarket US**, the CFTC-regulated exchange for US residents. It is **paper trading** (real prices, no orders) unless you also pass `--live`.

```
pip install -e '.[trade]'
polyscan watch --top 15 --bankroll 100 --autotrade         # paper: logs what it would buy, scores it when games settle
polyscan trades                                             # paper results so far
POLYMARKET_KEY_ID=... POLYMARKET_SECRET_KEY=... \
  polyscan watch --top 15 --rank profit --size-from-account --autotrade --live  # real orders, sized from your balance
polyscan trades --live
```
API keys come from polymarket.us/developer after identity verification. Keep them in your environment or `.env`; never in code or chat. Revoke them there if they leak.

What it copies:
- Only **BUYs of a game's winner market**. The international event slug (e.g. `nfl-kc-lv-2026-10-04`) is the same on Polymarket US, and the team is matched by name. Spreads, totals, props, esports and games not listed in the US are skipped.
- Only when the copied team is the market's **first (long) side**. Buying the other team is a short on Polymarket US, and the API docs don't say clearly how that order is priced, so those come to you as an alert to buy by hand.
- Exits when the same wallet sells; otherwise holds to settlement.
- `--rank profit` watches the biggest 90-day earners instead of the highest copy scores. Wallets that stopped trading and high-frequency bots are left out either way, since there's nothing a copier can follow.
- `--size-from-account` sizes every bet and the daily stop from the account's current value: in live mode the Polymarket US balance (cash plus open positions), in paper mode `--bankroll` plus paper profit and loss. Bets grow as the account grows and shrink as it falls. The 30% halt is measured from the starting value.

How it trades and what stops it:
- **IOC limit orders** (fill now or cancel), never market orders. The price plus the taker fee (0.0695 × p × (1−p) per contract, about 1.7¢ at $0.50) must stay within 3¢ of the copied wallet's price. That fee is about the size of the leaders' typical edge, so most signals are skipped.
- **Bet size** 2% of `--bankroll` ($2 on $100; `--stake-pct`, or a fixed `--stake 2`), raised to the exchange minimum when needed, and never more than the bankroll in open bets. The defaults are sized for a $100 account: over 30-100 bets a week, normal luck swings about $10-20, which $2 bets can absorb.
- **Stops buying** for the rest of the day (ET) once down 10% of the bankroll that day (`--max-daily-loss`, $10 on $100) or after 5 losses (`--max-losses`), and halts completely once realized losses reach 30% (`--max-drawdown`, $30 on $100) until `polyscan trades --reset-halt`.
- **Kill switch:** create `polyscan_out/STOP` and it buys nothing more.

Limits: the live order path is tested against a fake exchange only. It has never placed a real order, because Polymarket US has no sandbox. Run paper mode for a day or two and check its picks against the app before using `--live`, then watch the first live orders. Results are not guaranteed; you can lose the whole bankroll.

## Replaying past weeks instead of watching live
`polyscan backtest` rebuilds what the copy trader would have done over a past window, so nothing has to run for weeks:
```
polyscan backtest --start 2026-09-03 --end 2026-10-01 --bankroll 100
polyscan backtest --start 2026-10-01 --end 2026-10-15 --watchlist data/polyscan_watchlist_2026-10-01.json
```
- **Wallets** are picked as `watch --rank profit` would have on the start date, using only data from before it (90-day P&L ending that day; bots and wallets idle for 14 days left out), or loaded from a saved `--watchlist`, so the test has no hindsight.
- **Alerts** are rebuilt from those wallets' trade history (fills added up per wallet, outcome, side and minute, $1,000+).
- **Prices** come from Polymarket US's minute-level price history one poll (60s) after each alert. **Results** come from its official settlements. The real `CopyTrader` makes every decision on a simulated clock, including the daily stop and the 30% halt.
- It compares four settings: as built (first side, 3¢ limit), plus the second side bought by hand, loose (10¢ limit, any price), and copy everything (no price limit).
- Limits: fills assume a $2 order fits at the best price (price history has no depth), and a game counts as finished 4 hours after its start.

### Result for Sept 3 - Oct 1, 2026 (`data/polyscan_backtest_2026-09-03_2026-10-01.json`)
| Setting | Trades | Won | P&L on $100 | Return on staked |
|---|---|---|---|---|
| As built: first side only, 3¢ limit | 95 | 40 | -$21.05 | -12.7% |
| Plus second side bought by hand | 165 | 73 | -$26.45 | -9.9% |
| Loose: both sides, 10¢ limit, any price | 192 | 90 | -$20.70 | -7.2% |
| Copy everything, no price limit | 194 | 91 | -$9.04 | -4.7% |

Copied prices matched the wallets' own (0.456 average against their 0.461), so the losses came from the bets themselves: the copied game-winner bets won 42% of the time at prices that needed about 46% to break even, before fees. These wallets' profits came mostly from markets Polymarket US doesn't list or the copier skips (spreads, totals, props, esports, smaller leagues). 1,685 of 3,258 buy alerts were spreads, totals or props, and 642 were games not listed on Polymarket US.

### Copying only the top 2 wallets (same window)
| Wallets | Picked | Trades (as built) | Won | P&L on $100 |
|---|---|---|---|---|
| vito3corleone, BreakTheBank | top 2 by profit on Sept 3 (fair test) | 9 | 6 | +$11.30 |
| ndb1, gmpm2 | top 2 by profit on Oct 1 (picked with hindsight) | 21 | 10 | +$0.56 |

All of the fair test's profit came from BreakTheBank; vito3corleone stopped trading. Nine trades is far too few to tell skill from luck: one or two games decide the result. Files: `data/polyscan_backtest_top2_*.json`.
