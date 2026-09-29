"""Deterministic daily long-only factor backtester.

Timing: scores use closes up to day t; trades execute at the close of t+1 (so no same-close
look-ahead); the new weights earn returns from t+2. Costs are charged on traded notional:
``commission_bps + slippage_bps`` per unit of turnover.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

import numpy as np

from . import factors as F
from .data import Panel
from .universe import BENCHMARK, SECTOR_STOCKS

CAPITAL = 100_000.0
TRADING_DAYS = 252


@dataclass
class Spec:
    name: str
    universe: list[str]
    factors: dict[str, float]
    hypothesis: str = ""
    top_n: int = 5
    weighting: str = "equal"          # equal | inverse_vol | score
    max_weight: float = 0.25
    rebalance: str = "monthly"        # daily | weekly | monthly
    rebalance_offset: int = 0         # shift which day of the week/month rebalances (timing-luck test)
    band: int = 0                     # keep a holding until its rank falls below top_n + band
    regime_filter: str = "none"       # none | spy_above_200d
    commission_bps: float = 5.0
    slippage_bps: float = 5.0
    exclude: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Spec":
        d = dict(d)
        uni = d.get("universe")
        if isinstance(uni, str):
            if uni not in SECTOR_STOCKS:
                raise ValueError(f"unknown sector {uni!r}; choose from {list(SECTOR_STOCKS)}")
            uni = SECTOR_STOCKS[uni]
        if not uni:
            raise ValueError("universe must be a sector name or a non-empty list of tickers")
        d["universe"] = [str(s).upper() for s in uni]
        facs = d.get("factors")
        if isinstance(facs, list):
            facs = {f["name"]: float(f["weight"]) for f in facs}
        if not facs:
            raise ValueError("factors must be non-empty")
        bad = [f for f in facs if f not in F.FACTORS]
        if bad:
            raise ValueError(f"unknown factor(s) {bad}; choose from {list(F.FACTORS)}")
        if any(w < 0 for w in facs.values()) or sum(facs.values()) <= 0:
            raise ValueError("factor weights must be >= 0 and sum to > 0")
        d["factors"] = facs
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        s = cls(**known)
        if s.weighting not in ("equal", "inverse_vol", "score"):
            raise ValueError("weighting must be equal, inverse_vol or score")
        if s.rebalance not in ("daily", "weekly", "monthly"):
            raise ValueError("rebalance must be daily, weekly or monthly")
        if s.regime_filter not in ("none", "spy_above_200d"):
            raise ValueError("regime_filter must be none or spy_above_200d")
        if not 1 <= s.top_n <= len(s.universe):
            raise ValueError(f"top_n must be between 1 and {len(s.universe)}")
        if not 0 < s.max_weight <= 1:
            raise ValueError("max_weight must be in (0, 1]")
        if s.band < 0:
            raise ValueError("band must be >= 0")
        return s

    def to_dict(self) -> dict:
        return asdict(self)


def _rebalance_days(dates: np.ndarray, freq: str, offset: int) -> np.ndarray:
    n = len(dates)
    if freq == "daily":
        return np.ones(n, bool)
    if freq == "weekly":
        key = (dates.astype("datetime64[W]")).astype(int)
    else:
        key = (dates.astype("datetime64[M]")).astype(int)
    out = np.zeros(n, bool)
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], n]
    for s, e in zip(starts, ends):
        i = s + offset
        if i < e:
            out[i] = True
    return out


def _cap_weights(w: np.ndarray, cap: float) -> np.ndarray:
    """Cap each weight at `cap`, redistributing the excess; anything that can't be placed stays in cash."""
    w = w.copy()
    for _ in range(50):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        room = (w > 0) & (w < cap - 1e-12)
        if not room.any():
            break
        w[room] += excess * w[room] / w[room].sum()
    return w


def _max_dd(eq: np.ndarray) -> float:
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1).min())


def run(spec: Spec, panel: Panel, start: str | None = None, end: str | None = None,
        cost_mult: float = 1.0) -> dict:
    uni = [s for s in spec.universe if s in panel.symbols and s not in spec.exclude]
    missing = [s for s in spec.universe if s not in panel.symbols]
    if not uni:
        raise ValueError("no universe symbols have price data")
    idx = [panel.symbols.index(s) for s in uni]
    px = panel.px[:, idx]
    bench = panel.col(BENCHMARK)
    T, N = px.shape

    total_w = sum(spec.factors.values())
    score = np.zeros((T, N))
    for name, w in spec.factors.items():
        score = score + (w / total_w) * F.cs_rank(F.compute(name, px, bench))
    vol = -F.low_vol_3m(px, bench)
    regime_ok = np.ones(T, bool)
    if spec.regime_filter == "spy_above_200d":
        regime_ok = F.trend_200d(bench.reshape(-1, 1), bench)[:, 0] > 0

    rets = np.nan_to_num(px[1:] / px[:-1] - 1)  # rets[t-1] = return on day t
    rets = np.vstack([np.zeros((1, N)), rets])
    brets = np.r_[0.0, bench[1:] / bench[:-1] - 1]

    s0 = 0 if start is None else int(np.searchsorted(panel.dates, np.datetime64(start, "D")))
    s1 = T if end is None else int(np.searchsorted(panel.dates, np.datetime64(end, "D"), side="right"))
    if s1 - s0 < 40:
        raise ValueError("evaluation window too short")
    reb = _rebalance_days(panel.dates, spec.rebalance, spec.rebalance_offset)
    cost_rate = (spec.commission_bps + spec.slippage_bps) * cost_mult / 1e4
    comm_rate = spec.commission_bps * cost_mult / 1e4

    held = np.zeros(N)       # current drifted weights
    pending = None           # target decided yesterday, trades today
    eq = [1.0]
    port_r, contrib = [], np.zeros(N)
    turnover_total = commissions = 0.0
    n_trades_days = 0
    holdings_count = []
    # start deciding one day before the window so the first trade lands on day s0
    for t in range(max(s0 - 1, 1), s1):
        in_window = t >= s0
        # 1) today's return on yesterday's holdings
        if in_window:
            day_r = float(held @ rets[t])
            contrib += held * rets[t]
            gross = held * (1 + rets[t])
            tot = 1 + day_r
            held = gross / tot if tot > 0 else gross
        # 2) execute yesterday's decision at today's close
        cost = 0.0
        if pending is not None:
            trade = np.abs(pending - held).sum()
            if in_window:
                cost = trade * cost_rate
                turnover_total += trade
                commissions += trade * comm_rate * CAPITAL * eq[-1]
                if trade > 1e-9:
                    n_trades_days += 1
            held = pending
            pending = None
        if in_window:
            r = day_r - cost
            port_r.append(r)
            eq.append(eq[-1] * (1 + r))
            holdings_count.append(int((held > 1e-9).sum()))
        # 3) decide at today's close
        if reb[t]:
            row = score[t]
            ok = ~np.isnan(row)
            target = np.zeros(N)
            if ok.sum() >= 1 and regime_ok[t]:
                order = np.argsort(-np.where(ok, row, -np.inf), kind="stable")
                ranked = [i for i in order if ok[i]]
                rank_of = {i: r for r, i in enumerate(ranked)}
                keep = [i for i in np.flatnonzero(held > 1e-9) if i in rank_of and rank_of[i] < spec.top_n + spec.band]
                chosen = list(keep)
                for i in ranked:
                    if len(chosen) >= spec.top_n:
                        break
                    if i not in chosen:
                        chosen.append(i)
                chosen = chosen[:max(spec.top_n, len(keep))]
                if spec.weighting == "equal":
                    raw = np.ones(len(chosen))
                elif spec.weighting == "inverse_vol":
                    v = vol[t, chosen]
                    raw = np.where(np.isnan(v) | (v <= 0), np.nanmedian(v) if np.isfinite(np.nanmedian(v)) else 1, v)
                    raw = 1 / raw
                else:
                    raw = row[chosen] + 1e-6
                target[chosen] = raw / raw.sum()
                target = _cap_weights(target, spec.max_weight)
            pending = target

    port_r = np.array(port_r)
    eq = np.array(eq)
    b = brets[s0:s1]
    beq = np.r_[1.0, np.cumprod(1 + b)]
    years = len(port_r) / TRADING_DAYS
    std = port_r.std(ddof=1) if len(port_r) > 1 else 0.0
    total = float(eq[-1] - 1)
    btotal = float(beq[-1] - 1)
    var_b = b.var(ddof=1)
    beta = float(np.cov(port_r, b)[0, 1] / var_b) if var_b > 0 else float("nan")
    top_i = int(np.argmax(contrib))
    top_share = float(contrib[top_i] / contrib.sum()) if contrib.sum() > 0 else float("inf")
    by_name = sorted(((uni[i], round(float(contrib[i]) * 100, 2)) for i in range(N) if abs(contrib[i]) > 1e-6),
                     key=lambda kv: -kv[1])
    digest = hashlib.sha256(np.round(eq, 12).tobytes()).hexdigest()[:16]
    return {
        "window": [str(panel.dates[s0]), str(panel.dates[s1 - 1])],
        "days": len(port_r),
        "total_return": round(total, 4),
        "benchmark_return": round(btotal, 4),
        "excess_return": round(total - btotal, 4),
        "cagr": round(float(eq[-1] ** (1 / years) - 1), 4) if years > 0 and eq[-1] > 0 else None,
        "ann_vol": round(float(std * np.sqrt(TRADING_DAYS)), 4),
        "sharpe": round(float(port_r.mean() / std * np.sqrt(TRADING_DAYS)), 3) if std > 0 else 0.0,
        "benchmark_sharpe": round(float(b.mean() / b.std(ddof=1) * np.sqrt(TRADING_DAYS)), 3) if b.std() > 0 else 0.0,
        "max_drawdown": round(_max_dd(eq), 4),
        "benchmark_max_drawdown": round(_max_dd(beq), 4),
        "beta": round(beta, 3),
        "annual_turnover": round(turnover_total / years, 2) if years else None,
        "commissions_usd": round(commissions, 2),
        "total_costs_pct": round(float(turnover_total * cost_rate), 4),
        "avg_holdings": round(float(np.mean(holdings_count)), 2) if holdings_count else 0,
        "top_contributor": uni[top_i],
        "top_contributor_share": round(top_share, 3) if np.isfinite(top_share) else None,
        "contribution_pct_by_name": by_name[:10],
        "missing_symbols": missing,
        "equity_hash": digest,
    }


def public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}
