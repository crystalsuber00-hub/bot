"""Monthly SPY option strategies, priced with Black-Scholes from the VIX. Research only.

PRE-REGISTERED RULES (written before any result was seen; do not edit after running):
  Trades: enter at the close every 21 trading days, ~30-day expiry, hold to expiry.
  Pricing: implied vol = VIX x iv_mult with a linear skew; results must hold at iv_mult 0.85 AND 1.00
    (real ATM vol usually sits a bit under the VIX, so this brackets it).
  Costs: $0.02/share half-spread + $0.65/contract per leg at entry, and again for legs closed in the money.
  Split: first 75% of trades = development, last 25% = holdout, used once.
  Development survival: total P&L > 0 in each of 3 dev windows, at both iv_mults AND with costs doubled.
  Only strategies a small account can hold (defined risk) are eligible; short_straddle is shown for
    reference only.
  The eligible survivor with the best dev Sharpe goes to the holdout once. It passes if holdout total P&L > 0
    at both iv_mults and with doubled costs, and a $2,000 account risking 10% of equity per trade keeps
    max drawdown under 40%.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import requests

DATA = Path("data")
LOG = Path("spy_gate_log.json")
HOLD = 21
HOLDOUT_FRAC = 0.25
SKEW = 0.8          # iv(K) = atm_iv - SKEW * ln(K/S): puts dearer, calls cheaper
HALF_SPREAD = 0.02
COMMISSION = 0.65
START_EQUITY = 2000.0
RISK_FRAC = 0.10


# --- data ------------------------------------------------------------------
def _yahoo_daily(symbol: str) -> dict[str, float]:
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                     params={"interval": "1d", "period1": 0, "period2": int(datetime.now(timezone.utc).timestamp())},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]  # unadjusted: what options are struck against
    return {datetime.fromtimestamp(t, timezone.utc).date().isoformat(): c
            for t, c in zip(res["timestamp"], closes) if c is not None}


def load(refresh: bool) -> list[tuple[str, float, float]]:
    path = DATA / "spy_vix_daily.csv"
    if refresh or not path.exists():
        spy, vix = _yahoo_daily("SPY"), _yahoo_daily("%5EVIX")
        DATA.mkdir(exist_ok=True)
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "spy", "vix"])
            for d in sorted(spy):
                if d in vix:
                    w.writerow([d, round(spy[d], 4), round(vix[d], 4)])
    with path.open() as f:
        return [(r["date"], float(r["spy"]), float(r["vix"])) for r in csv.DictReader(f)]


# --- pricing ---------------------------------------------------------------
def _n(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S: float, K: float, T: float, vol: float, right: str) -> float:
    if T <= 0:
        return max(S - K, 0) if right == "C" else max(K - S, 0)
    sd = vol * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    return S * _n(d1) - K * _n(d2) if right == "C" else K * _n(-d2) - S * _n(-d1)


def iv_at(S: float, K: float, atm: float) -> float:
    return max(atm * 0.5, atm - SKEW * math.log(K / S))


# --- strategies: legs are (qty, right, strike as fraction of spot) ---------
STRATS = {
    "long_straddle": [(1, "C", 1.00), (1, "P", 1.00)],
    "long_strangle": [(1, "C", 1.05), (1, "P", 0.95)],
    "iron_fly": [(-1, "C", 1.00), (-1, "P", 1.00), (1, "C", 1.05), (1, "P", 0.95)],
    "iron_condor": [(-1, "C", 1.03), (-1, "P", 0.97), (1, "C", 1.06), (1, "P", 0.94)],
    "short_straddle": [(-1, "C", 1.00), (-1, "P", 1.00)],
}
FILTERS = {"long_straddle_low_vix": ("long_straddle", lambda vix: vix < 15)}
ELIGIBLE = {"long_straddle", "long_strangle", "iron_fly", "iron_condor", "long_straddle_low_vix"}


def trade(legs, S0, S1, T, vix, iv_mult, cost_mult) -> tuple[float, float]:
    """(P&L per share, capital at risk per share) for one position held to expiry."""
    atm = vix / 100 * iv_mult
    strikes = [round(S0 * k) for _, _, k in legs]
    entry = sum(q * bs(S0, K, T, iv_at(S0, K, atm), r) for (q, r, _), K in zip(legs, strikes))
    payoff = sum(q * bs(S1, K, 0, 0, r) for (q, r, _), K in zip(legs, strikes))
    per_leg = HALF_SPREAD + COMMISSION / 100
    cost = per_leg * len(legs)
    cost += per_leg * sum(1 for (q, r, _), K in zip(legs, strikes) if bs(S1, K, 0, 0, r) > 0)
    pnl = payoff - entry - cost * cost_mult
    if entry > 0:  # net debit: risk is what you paid
        risk = entry + cost
    elif all(q < 0 for q, _, _ in legs):  # naked: approximate margin
        risk = 0.2 * S0
    else:  # defined-risk credit spread: widest wing minus credit
        calls = [K for (q, r, _), K in zip(legs, strikes) if r == "C"]
        puts = [K for (q, r, _), K in zip(legs, strikes) if r == "P"]
        risk = max(max(calls) - min(calls), max(puts) - min(puts)) + entry + cost
    return pnl, risk


def trades_for(name, rows, iv_mult=1.0, cost_mult=1.0) -> list[tuple[str, float]]:
    """(entry date, return on capital at risk) per trade; 0 on skipped months."""
    base, cond = FILTERS.get(name, (name, lambda v: True))
    legs = STRATS[base]
    out = []
    for i in range(0, len(rows) - HOLD, HOLD):
        d0, S0, vix = rows[i]
        d1, S1, _ = rows[i + HOLD]
        if not cond(vix):
            out.append((d0, 0.0))
            continue
        T = (date.fromisoformat(d1) - date.fromisoformat(d0)).days / 365
        pnl, risk = trade(legs, S0, S1, T, vix, iv_mult, cost_mult)
        out.append((d0, pnl / risk))
    return out


def stats(rets: list[float]) -> dict:
    eq, peak, dd = START_EQUITY, START_EQUITY, 0.0
    for r in rets:
        eq *= 1 + RISK_FRAC * r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    n = len(rets)
    m = sum(rets) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / n)
    taken = [r for r in rets if r != 0]
    return {"total": sum(rets), "mean": m, "sharpe": m / sd * math.sqrt(12) if sd else 0.0,
            "win": sum(r > 0 for r in taken) / len(taken) if taken else 0.0,
            "worst": min(rets), "equity": eq, "maxdd": dd}


def run(refresh: bool) -> None:
    rows = load(refresh)
    names = list(STRATS) + list(FILTERS)
    n_trades = len(trades_for("long_straddle", rows))
    split = int(n_trades * (1 - HOLDOUT_FRAC))
    print(f"SPY+VIX {rows[0][0]} .. {rows[-1][0]}; {n_trades} monthly trades; dev {split}, holdout {n_trades - split}")
    print("Dev results (per-trade return on capital at risk; account = $2,000 risking 10%/trade):\n")
    print(f"{'strategy':24s} {'win%':>5s} {'mean':>7s} {'worst':>7s} {'sharpe':>6s} {'acct $':>9s} {'maxDD':>6s}  3 windows / iv0.85 / 2x cost")
    survivors = []
    for name in names:
        scen = {k: [r for _, r in trades_for(name, rows, *a)][:split]
                for k, a in {"base": (1.0, 1.0), "iv85": (0.85, 1.0), "cost2": (1.0, 2.0)}.items()}
        s = stats(scen["base"])
        w = split // 3
        wins = [sum(scen["base"][j * w:(j + 1) * w]) > 0 for j in range(3)]
        ok = all(wins) and sum(scen["iv85"]) > 0 and sum(scen["cost2"]) > 0
        tag = ("SURVIVES" if ok else "rejected") + ("" if name in ELIGIBLE else " (reference only)")
        print(f"{name:24s} {s['win']:5.0%} {s['mean']:+7.1%} {s['worst']:+7.0%} {s['sharpe']:6.2f} "
              f"{s['equity']:9,.0f} {s['maxdd']:6.0%}  {''.join('+' if x else '-' for x in wins)} / "
              f"{sum(scen['iv85']):+.1f} / {sum(scen['cost2']):+.1f}  {tag}")
        if ok and name in ELIGIBLE:
            survivors.append((s["sharpe"], name))

    if LOG.exists():
        print("\nHoldout already used:\n" + json.loads(LOG.read_text())["summary"])
        return
    if not survivors:
        summary = "No eligible strategy survived development. Holdout not touched. Nothing to deploy."
        print("\n" + summary)
        return
    _, best = max(survivors)
    lines = [f"Selected on dev only: {best}. Holdout (used once):"]
    passed = True
    for label, a in {"base": (1.0, 1.0), "iv 0.85": (0.85, 1.0), "2x cost": (1.0, 2.0)}.items():
        h = [r for _, r in trades_for(best, rows, *a)][split:]
        s = stats(h)
        passed &= s["total"] > 0
        if label == "base":
            passed &= s["maxdd"] < 0.40
        lines.append(f"  {label:8s} win {s['win']:.0%}  mean {s['mean']:+.1%}  worst {s['worst']:+.0%}  "
                     f"$2,000 -> ${s['equity']:,.0f}  maxDD {s['maxdd']:.0%}")
    lines.append(f"VERDICT: {'PASS (paper trade it next)' if passed else 'FAIL. Nothing to deploy.'}")
    summary = "\n".join(lines)
    print("\n" + summary)
    LOG.write_text(json.dumps({"strategy": best, "passed": passed, "summary": summary}, indent=1))
