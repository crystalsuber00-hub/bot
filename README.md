# spxbot

> This repo also contains **quantbot**, a Claude-driven strategy research loop. See [quantbot](#quantbot-claude-strategy-research-loop) at the bottom.

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

## quantbot: Claude strategy research loop

A separate tool from spxbot. It runs the loop **research, hypothesis, strategy, backtest, fail, improve, stress-test, validate** with Claude Opus 5.5 as the researcher and a local, deterministic backtester as the judge. Claude proposes. The validator decides. Claude can't mark its own work as passed.

| Step | What happens |
|---|---|
| 1. Research | Claude reads the sector scan (11 SPDR sector ETFs against SPY) and per-stock stats, then ranks the opportunities. It isn't allowed to propose a strategy yet. |
| 2. Hypotheses | Claude writes N fundamentally different strategy specs (universe, factors and weights, top_n, weighting, rebalance, no-trade band, costs) and gives the reason each edge might exist. |
| 3. Backtest and iterate | Claude runs exploratory backtests (80 by default) and sends its best specs to the validator (8 submissions by default). Every submission goes into the rejection log. |
| 4. Attack | Every spec that passed gets attacked: 2x or higher costs, dropping its best stocks, bear markets and rising-rate periods, calendar years, changed parameters. |
| 5. Memo | `runs/<timestamp>/report.md` is generated from the tool log, not from Claude's claims. It contains the rejection log, the survivors with every check, the stress-test results, the API cost, and Claude's commentary. A passing spec is also saved as `candidate_*.json`. |

**Acceptance criteria** (fixed in `quantbot/validate.py`, `Criteria`): beat SPY after costs over the trailing year; still beat SPY with 2x costs; max drawdown under 30%; Sharpe above 1.0; no single stock more than 40% of returns; identical results on two runs. Three checks go beyond the article: it must also beat SPY over 3 years, beat SPY with its top-contributing stock removed, and beat SPY with the rebalance day shifted (to catch results that depend on timing luck).

**Held-out data:** by default the last 63 trading days are hidden from every exploration tool. Only the validator sees them, so Claude can't tune a strategy on the data that judges it. Change this with `--holdout-days` (0 turns it off).

### Run
```
pip install -e '.[research]'
export ANTHROPIC_API_KEY=...
quantbot fetch                                   # ~5 years of daily prices into data/prices (Yahoo, free)
quantbot scan                                    # sector strength vs SPY, no Claude
quantbot backtest specs/energy_momentum_lowvol.json
quantbot validate specs/energy_momentum_lowvol.json   # every acceptance check + stress suite, no Claude
quantbot research                                # the full Claude loop -> runs/<timestamp>/report.md
```
Options for `research`: `--effort` (default `high`), `--hypotheses 4`, `--max-backtests 80`, `--max-submissions 8`, `--holdout-days 63`, `--model`.

### Weekly automation
`quantbot weekly` scans the sectors. If no sector leads SPY by more than `--threshold` (default 10%) over 3 months, it stops without calling Claude, so it costs nothing. Otherwise it runs the loop on the strongest sector with 3 hypotheses. It sends an alert only when a strategy passes the validator, using the same `NTFY_TOPIC` / `TELEGRAM_*` / `DISCORD_WEBHOOK_URL` / `WEBHOOK_URL` variables as spxbot. For example, with cron every Sunday at 18:05:
```
5 18 * * 0  cd /path/to/bot && mkdir -p logs && set -a && . ./.env && set +a && quantbot fetch && quantbot weekly >> logs/quantbot.log 2>&1
```

### Strategy spec
A JSON file. Factors are ranked cross-sectionally and blended by weight, then the top `top_n` stocks are held:
`universe` (a sector name or a list of tickers), `factors` (`[{"name", "weight"}]`), `top_n`, `weighting` (`equal` / `inverse_vol` / `score`), `max_weight` (unfilled weight stays in cash), `rebalance` (`daily` / `weekly` / `monthly`), `band` (keep a stock until its rank drops below `top_n + band`, which cuts churn), `regime_filter` (`none` / `spy_above_200d`), `commission_bps`, `slippage_bps`. The factor library is in `quantbot/factors.py` (momentum 12-1 / 6-1 / 3m, 1-month reversal, 200-day trend, low volatility, low idiosyncratic volatility, low beta, and two price-based quality proxies).

Backtest timing: the signal uses the close on day t, the trade fills at the close on t+1, and returns count from t+2. Costs are charged on the traded notional.

### Differences from the article
- **No Minara.** Minara is a closed desktop app with no public API. This bot uses free Yahoo daily prices and its own backtester, so everything runs locally and you can read all of it.
- **No fundamentals.** The article's operating-cash-flow factor (`jkp_ocf_at`) needs a fundamentals feed. Here, "quality" is estimated from price behavior (`consistency_12m`, `shallow_drawdown_6m`). The article's recipe rebuilt with these substitutes, on the article's 19 names, **fails** the validator on current data: 3.7% vs SPY 16.7% over the trailing year, with 64% of returns from one stock (DNN). The same factors on the wider 28-name Energy list pass (`specs/energy_momentum_lowvol.json`). That's one hand-picked variant, so treat it as an example of the tooling, not as evidence.
- **Survivorship bias.** The universe lists in `quantbot/universe.py` are today's large caps. Backtests over them look better than reality would have.
- **No live trading.** A pass means "worth a human looking at it, then paper trading it". It is not an order.
