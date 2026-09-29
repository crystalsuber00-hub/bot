"""Cross-sectional factors computed from prices only.

Each factor maps a price matrix ``px`` (T x N) to a score matrix (T x N) where row t only uses
data up to and including day t, and higher is always better (e.g. low_vol = -volatility).
NaN means "not enough history".

There is no fundamentals feed (the article's operating-cash-flow factor needs one), so the
"quality" slot is filled by price-based proxies: return consistency and shallow drawdowns.
"""
from __future__ import annotations

import numpy as np


def _returns(px: np.ndarray) -> np.ndarray:
    r = np.full_like(px, np.nan)
    r[1:] = px[1:] / px[:-1] - 1
    return r


def _lag_ratio(px: np.ndarray, near: int, far: int) -> np.ndarray:
    """px[t-near] / px[t-far] - 1."""
    out = np.full_like(px, np.nan)
    if len(px) > far:
        out[far:] = px[far - near:len(px) - near] / px[:len(px) - far] - 1
    return out


def _rolling(x: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Rolling sum and valid-count over the trailing w rows (inclusive), NaN-aware."""
    valid = ~np.isnan(x)
    cs = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(np.where(valid, x, 0.0), 0)])
    cn = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(valid, 0)])
    s = np.full_like(x, np.nan, dtype=float)
    n = np.zeros_like(x, dtype=float)
    if len(x) >= w:
        s[w - 1:] = cs[w:] - cs[:-w]
        n[w - 1:] = cn[w:] - cn[:-w]
    return s, n


def _rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    s, n = _rolling(x, w)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n >= w * 0.9, s / np.maximum(n, 1), np.nan)


def _rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    m = _rolling_mean(x, w)
    m2 = _rolling_mean(x * x, w)
    return np.sqrt(np.maximum(m2 - m * m, 0))


def mom_12_1(px, bench):
    return _lag_ratio(px, 21, 252)


def mom_6_1(px, bench):
    return _lag_ratio(px, 21, 126)


def mom_3m(px, bench):
    return _lag_ratio(px, 0, 63)


def reversal_1m(px, bench):
    return -_lag_ratio(px, 0, 21)


def trend_200d(px, bench):
    sma = _rolling_mean(px, 200)
    return px / sma - 1


def low_vol_3m(px, bench):
    return -_rolling_std(_returns(px), 63)


def _beta_and_resid_vol(px, bench, w):
    r = _returns(px)
    m = _returns(bench.reshape(-1, 1))
    m = np.broadcast_to(m, r.shape).copy()
    m[np.isnan(r)] = np.nan
    mr, mm = _rolling_mean(r, w), _rolling_mean(m, w)
    cov = _rolling_mean(r * m, w) - mr * mm
    var_m = _rolling_mean(m * m, w) - mm * mm
    var_r = _rolling_mean(r * r, w) - mr * mr
    with np.errstate(invalid="ignore", divide="ignore"):
        beta = cov / var_m
        resid = np.sqrt(np.maximum(var_r - beta * beta * var_m, 0))
    return beta, resid


def low_idio_vol(px, bench):
    return -_beta_and_resid_vol(px, bench, 63)[1]


def low_beta(px, bench):
    return -_beta_and_resid_vol(px, bench, 252)[0]


def consistency_12m(px, bench):
    """Trailing 12-month Sharpe-like ratio of daily returns (quality proxy)."""
    r = _returns(px)
    with np.errstate(invalid="ignore", divide="ignore"):
        return _rolling_mean(r, 252) / _rolling_std(r, 252)


def shallow_drawdown_6m(px, bench):
    """-(drawdown from the trailing 126-day high) (quality/defensiveness proxy)."""
    out = np.full_like(px, np.nan)
    for t in range(125, len(px)):
        win = px[t - 125:t + 1]
        with np.errstate(invalid="ignore"):
            out[t] = px[t] / np.nanmax(win, 0) - 1
    return out


FACTORS = {
    "mom_12_1": (mom_12_1, "12-month return skipping the last month (classic momentum)"),
    "mom_6_1": (mom_6_1, "6-month return skipping the last month"),
    "mom_3m": (mom_3m, "3-month return (short-term relative strength)"),
    "reversal_1m": (reversal_1m, "minus the last month's return (short-term mean reversion)"),
    "trend_200d": (trend_200d, "price vs its 200-day average"),
    "low_vol_3m": (low_vol_3m, "minus 3-month daily volatility"),
    "low_idio_vol": (low_idio_vol, "minus 3-month idiosyncratic (non-SPY) volatility"),
    "low_beta": (low_beta, "minus 12-month beta to SPY"),
    "consistency_12m": (consistency_12m, "12-month mean/std of daily returns (quality proxy)"),
    "shallow_drawdown_6m": (shallow_drawdown_6m, "closeness to the 6-month high (quality proxy)"),
}


def compute(name: str, px: np.ndarray, bench: np.ndarray) -> np.ndarray:
    return FACTORS[name][0](px, bench)


def cs_rank(x: np.ndarray) -> np.ndarray:
    """Cross-sectional percentile rank per row in (0, 1]; NaN stays NaN. Ties broken by column order (deterministic)."""
    out = np.full_like(x, np.nan)
    for t in range(len(x)):
        row = x[t]
        ok = ~np.isnan(row)
        k = ok.sum()
        if k == 0:
            continue
        order = np.argsort(row[ok], kind="stable")
        ranks = np.empty(k)
        ranks[order] = np.arange(1, k + 1)
        out[t, ok] = ranks / k
    return out
