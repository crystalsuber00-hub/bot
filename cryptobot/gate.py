"""Pre-registered validation gate.

Rules are fixed here, before any result is seen. Do not edit them after running.
  1. Data is split: first 75% = development, last 25% = holdout. Selection never sees the holdout.
  2. Every parameter combination of every strategy is a trial, and all are logged.
  3. A trial survives development only if, pooled over coins and 3 dev windows, mean return > 0 and
     >= 60% of (coin, window) pairs are profitable, net of costs.
  4. Only the single best survivor (by mean dev Sharpe) touches the holdout, once.
  5. It passes only if on the holdout, in >= 4 of 5 coins: return > 0, beats buy & hold,
     max drawdown < 30%, and return still > 0 with fees and slippage doubled.
The holdout verdict is stored; once the holdout is used, re-running prints it instead of re-testing.
Until then, new strategies can be screened in further rounds (counted in the log).
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

from .backtest import FEE, SLIPPAGE, simulate
from .data import get_candles
from .robust import COINS, windows
from .strategies import STRATEGIES, buy_and_hold

LOG = Path("crypto_gate_log.json")
HOLDOUT_FRAC = 0.25
DEV_WINDOWS = 3
MIN_WINDOW_WIN_RATE = 0.60
MAX_DD = 0.30
MIN_COINS = 4


def trials():
    for name, (fn, grid) in STRATEGIES.items():
        keys = list(grid)
        for combo in itertools.product(*grid.values()):
            params = dict(zip(keys, combo))
            if name == "sma_cross" and params["fast"] >= params["slow"]:
                continue
            yield name, fn, params


def run() -> None:
    prev = json.loads(LOG.read_text()) if LOG.exists() else {}
    if prev.get("verdict"):
        print("Holdout already used; stored verdict (delete the log only if you change the data or rules):\n")
        print(prev["summary"])
        return
    # the holdout is still unseen, so new ideas may be screened; earlier trials are counted so the
    # total number of things tried stays visible
    rounds = prev.get("rounds", 0) + 1

    data = {c: [x for _, x in get_candles(c, 3600, refresh=False)] for c in COINS}
    split = {c: int(len(v) * (1 - HOLDOUT_FRAC)) for c, v in data.items()}
    rejections, survivors = [], []

    for name, fn, params in trials():
        rets, sharpes = [], []
        for c, closes in data.items():
            dev = closes[:split[c]]
            pos = fn(dev, **params)
            for a, b in windows(dev, DEV_WINDOWS):
                rets.append(simulate(dev[a:b], pos[a:b]).total_return)
            sharpes.append(simulate(dev, pos).sharpe)
        mean_ret = sum(rets) / len(rets)
        win_rate = sum(r > 0 for r in rets) / len(rets)
        label = f"{name} {params}"
        info = {"trial": label, "dev_mean_return": mean_ret, "dev_win_rate": win_rate,
                "dev_sharpe": sum(sharpes) / len(sharpes)}
        if mean_ret <= 0:
            rejections.append({**info, "reason": "mean dev return <= 0"})
        elif win_rate < MIN_WINDOW_WIN_RATE:
            rejections.append({**info, "reason": f"only {win_rate:.0%} of dev windows profitable"})
        else:
            survivors.append((name, fn, params, info))

    n = len(rejections) + len(survivors)
    lines = [f"Round {rounds}. {n} trials this round; {len(rejections)} rejected in development, {len(survivors)} survived."]
    for r in sorted(rejections, key=lambda r: -r["dev_mean_return"])[:5]:
        lines.append(f"  rejected: {r['trial']}  dev {r['dev_mean_return']:+.0%}/window  ({r['reason']})")

    if not survivors:
        lines.append("\nVERDICT: no candidate survived development. Holdout not touched. Nothing to deploy.")
        _finish(lines, rejections, None, rounds)
        return

    name, fn, params, info = max(survivors, key=lambda s: s[3]["dev_sharpe"])
    lines.append(f"\nSelected on dev only: {name} {params} (dev Sharpe {info['dev_sharpe']:.2f})")
    lines.append("Holdout (used once):")
    good = 0
    for c, closes in data.items():
        hold = closes[split[c]:]
        pos = fn(closes, **params)[split[c]:]
        r = simulate(hold, pos)
        bh = simulate(hold, buy_and_hold(hold))
        stress = simulate(hold, pos, fee=FEE * 2, slippage=SLIPPAGE * 2)
        ok = r.total_return > 0 and r.total_return > bh.total_return \
            and r.max_drawdown < MAX_DD and stress.total_return > 0
        good += ok
        lines.append(f"  {c:8s} {r.total_return:+6.0%} (bh {bh.total_return:+5.0%}) maxDD {r.max_drawdown:4.0%} "
                     f"2x-cost {stress.total_return:+5.0%}  {'PASS' if ok else 'fail'}")
    verdict = "PASS" if good >= MIN_COINS else "FAIL"
    lines.append(f"\nVERDICT: {verdict} ({good}/{len(data)} coins passed; needed {MIN_COINS}). "
                 + ("Still only paper trade it." if verdict == "PASS" else "Nothing to deploy."))
    _finish(lines, rejections, verdict, rounds)


def _finish(lines: list[str], rejections: list[dict], verdict: str | None, rounds: int) -> None:
    text = "\n".join(lines)
    print(text)
    LOG.write_text(json.dumps({"summary": text, "verdict": verdict, "rounds": rounds, "rejections": rejections}, indent=1))
