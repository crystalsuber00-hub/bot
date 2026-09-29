"""A small stand-in for the `xstrategy` API used in the article, so strategies written for it
run in quantbot's backtester unchanged.

A strategy subclasses ``XStrategy`` and implements ``alpha(d)``, returning one score per symbol in
``d.symbols`` (higher = more attractive, NaN = don't hold). Factor functions return today's
cross-section only, computed from prices up to today.
"""
from __future__ import annotations

import importlib.util
import warnings
from pathlib import Path

import numpy as np

from quantbot import factors as _F

__all__ = ["XStrategy", "combine", "cs_rank", "np", "load", "scores"]


class XStrategy:
    def alpha(self, d: "Data") -> np.ndarray:
        raise NotImplementedError


class Data:
    """What ``alpha`` sees on day ``t``: the symbols and factor values as of that day's close."""

    def __init__(self, symbols: list[str], px: np.ndarray, bench: np.ndarray):
        self.symbols = list(symbols)
        self._px, self._bench = px, bench
        self._cache: dict[str, np.ndarray] = {}
        self.t = 0

    def factor(self, name: str) -> np.ndarray:
        if name not in self._cache:
            self._cache[name] = _F.compute(name, self._px, self._bench)
        return self._cache[name][self.t]

    @property
    def close(self) -> np.ndarray:
        return self._px[self.t]


def cs_rank(x) -> np.ndarray:
    return _F.cs_rank(np.asarray(x, float).reshape(1, -1))[0]


def combine(*ranks, weights=None) -> np.ndarray:
    weights = weights or [1.0] * len(ranks)
    total = float(sum(weights))
    return sum((w / total) * np.asarray(r, float) for w, r in zip(weights, ranks))


_loaded: dict[tuple[str, float], type] = {}


def load(path: str) -> XStrategy:
    """Import a strategy file and return an instance of its ``Strategy`` class."""
    p = Path(path).resolve()
    key = (str(p), p.stat().st_mtime)
    if key not in _loaded:
        spec = importlib.util.spec_from_file_location(f"xstrategy_user_{abs(hash(key))}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "Strategy"):
            raise ValueError(f"{path} must define a class named Strategy")
        _loaded[key] = mod.Strategy
    return _loaded[key]()


def scores(path: str, px: np.ndarray, bench: np.ndarray, symbols: list[str]) -> np.ndarray:
    """Score matrix (T x N) from calling ``alpha`` on every day."""
    strat = load(path)
    d = Data(symbols, px, bench)
    out = np.full(px.shape, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for t in range(len(px)):
            d.t = t
            row = np.asarray(strat.alpha(d), float)
            if row.shape != (len(symbols),):
                raise ValueError(f"alpha() returned shape {row.shape}; expected ({len(symbols)},)")
            out[t] = row
    return out
