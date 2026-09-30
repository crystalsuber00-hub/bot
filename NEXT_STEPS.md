# Where we left off (Sep 30 2026)

Branch: `claude/confident-clarke-uw3w4u`. Everything below is built, tested (85 tests) and pushed.

## Waiting on
1. **Schwab developer app approval** (Trader API - Individual, requested Sep 29; up to 2 business days).
   Then create the app: Callback URL `https://127.0.0.1`, include Market Data Production if listed. Wait for **Ready For Use**.
2. **Massive Options Starter** ($29/month) - you're signing up. Send the API key.

## When Schwab says Ready For Use
```
bash scripts/setup_mac.sh                  # installs; tells you what's missing; re-run after each step
open -e .env                               # NTFY_TOPIC, SCHWAB_APP_KEY, SCHWAB_APP_SECRET
. .venv/bin/activate && set -a && . ./.env && set +a
spxbot -c stocks.toml --schwab-login       # repeat once a week
spxbot -c stocks.toml --check              # must end with "[ok] ready." + a test alert on your phone
bash scripts/install_mac.sh                # auto-start every weekday 9:15 ET (Mac awake + plugged in)
```
Send the `--check` output back to Claude to confirm the connection.

## When you have the Massive key
```
export MASSIVE_API_KEY=...
python -m spxbot.massive_backtest --check                                     # what the plan includes
python -m spxbot.massive_backtest --start 2024-10-01 --end 2026-09-29         # real-price backtest
```
Open question: stock 5-minute bars may need Massive's Stocks plan too (`--check` shows it). Ask before paying more.

## What the bot does now (stocks.toml)
- 0DTE option alerts on SPY, QQQ, IWM, META, AAPL, TSLA, MSFT, NVDA, AMZN, AMD, GOOGL, PLTR.
- One contract **under $150**, only setups scoring **6/7+** on the conviction checklist (no forced daily signal).
- Exits: **+50%** take profit, **-35%** stop, stock breaking back through the level, or 15:50.
- Alerts: morning map, entry (with checklist), exit plan (Robinhood steps), near target/stop, EXIT NOW, daily summary.
- Silently tracks every lower-scoring setup too; `spxbot -c stocks.toml --report` shows results by score.
- Signal-only: never places orders. You trade in Robinhood.

## What testing showed (estimated option prices, not real)
- 59-day model test: all signals lost money after bid/ask costs; 6/7 signals with a -35% stop were roughly breakeven to slightly positive.
- Not proven. Paper trade (or run the Massive backtest) before risking the $200 account.
