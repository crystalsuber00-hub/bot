"""Real-price evidence: Cboe option-strategy benchmark indexes (built from actual SPX option prices).

CNDR  iron condor: sells monthly ~20-delta put and call, buys ~5-delta wings, rest in 1-month T-bills
BFLY  iron butterfly: sells at-the-money put and call, buys out-of-the-money wings, rest in T-bills
PUT   sells a monthly at-the-money put, fully collateralized in T-bills
BXM   covered call: S&P 500 plus a monthly short at-the-money call
The indexes hold T-bills, so the options' own contribution is the return OVER T-bills. They include no
commissions or bid-ask costs, which a real trader pays.

python -m spyopts.cboe --refresh
"""
from __future__ import annotations

import argparse
import csv
import math
from datetime import date, datetime, timezone
from pathlib import Path

import requests

DATA = Path("data")
CBOE = ["CNDR", "BFLY", "PUT", "BXM"]


def refresh() -> None:
    DATA.mkdir(exist_ok=True)
    for s in CBOE:
        r = requests.get(f"https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{s}_History.csv",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        (DATA / f"cboe_{s}.csv").write_text(r.text)
    for name, sym in (("irx", "%5EIRX"), ("sp500tr", "%5ESP500TR")):
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                         params={"interval": "1d", "period1": 0, "period2": int(datetime.now(timezone.utc).timestamp())},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        with (DATA / f"{name}.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", name])
            for t, v in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
                if v is not None:
                    w.writerow([datetime.fromtimestamp(t, timezone.utc).date().isoformat(), v])


def _month_end(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    with path.open() as f:
        rows = list(csv.reader(f))[1:]
    for d, v in rows:
        if not v:
            continue
        iso = datetime.strptime(d, "%m/%d/%Y").date().isoformat() if "/" in d else d
        out[iso[:7]] = float(v)  # last value in each month wins (files are date-ordered)
    return out


def monthly() -> tuple[list[str], dict[str, list[float]]]:
    levels = {s: _month_end(DATA / f"cboe_{s}.csv") for s in CBOE}
    levels["S&P500"] = _month_end(DATA / "sp500tr.csv")
    irx = _month_end(DATA / "irx.csv")
    months = sorted(set.intersection(*(set(v) for v in levels.values())) & set(irx))
    rets = {k: [v[months[i]] / v[months[i - 1]] - 1 for i in range(1, len(months))] for k, v in levels.items()}
    rets["T-bills"] = [irx[months[i - 1]] / 100 / 12 for i in range(1, len(months))]
    return months[1:], rets


def stats(r: list[float], rf: list[float]) -> dict:
    n = len(r)
    growth = math.prod(1 + x for x in r)
    cagr = growth ** (12 / n) - 1
    rf_cagr = math.prod(1 + x for x in rf) ** (12 / n) - 1
    ex = [a - b for a, b in zip(r, rf)]
    m = sum(ex) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in ex) / n)
    eq = peak = 1.0
    dd = 0.0
    for x in r:
        eq *= 1 + x
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    roll = [math.prod(1 + x for x in r[i:i + 12]) - math.prod(1 + x for x in rf[i:i + 12]) for i in range(n - 11)]
    return {"cagr": cagr, "excess": cagr - rf_cagr, "sharpe": m / sd * math.sqrt(12) if sd else 0,
            "maxdd": dd, "worst": min(r), "beat_cash_12m": sum(x > 0 for x in roll) / len(roll) if roll else float("nan")}


def run() -> None:
    months, rets = monthly()
    print(f"Monthly data {months[0]} .. {months[-1]} ({len(months)} months). Real SPX option prices; NO trading costs.\n")
    now = date.fromisoformat(months[-1] + "-01")
    periods = [("all", 0)] + [(f"{y}y", len(months) - 12 * y) for y in (10, 5, 3, 1)]
    for label, start in periods:
        print(f"--- {label} (from {months[start]}) ---")
        print(f"{'':8s} {'CAGR':>7s} {'over cash':>9s} {'sharpe':>6s} {'maxDD':>6s} {'worst mo':>8s} {'12m>cash':>8s}")
        rf = rets["T-bills"][start:]
        for k in CBOE + ["S&P500", "T-bills"]:
            s = stats(rets[k][start:], rf)
            print(f"{k:8s} {s['cagr']:+7.1%} {s['excess']:+9.1%} {s['sharpe']:6.2f} {s['maxdd']:6.0%} "
                  f"{s['worst']:+8.1%} {s['beat_cash_12m']:8.0%}")
        print()
    _ = now


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="spyopts.cboe")
    ap.add_argument("--refresh", action="store_true")
    if ap.parse_args().refresh:
        refresh()
    run()
