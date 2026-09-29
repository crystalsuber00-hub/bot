"""Tools Claude can call. Exploration tools only see prices up to the research cutoff; the
held-out tail is used only by the validator, so Claude can't tune on the data that judges it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

from . import backtest as bt
from . import factors as F
from .data import Panel
from .universe import BENCHMARK, SECTOR_ETFS, SECTOR_STOCKS
from .validate import Criteria, stress_suite, validate

SPEC_SCHEMA = {
    "type": "object",
    "description": "A long-only, cross-sectional factor strategy.",
    "properties": {
        "name": {"type": "string"},
        "hypothesis": {"type": "string", "description": "Why the edge might exist, in one or two sentences."},
        "universe": {"description": "A sector name from list_universe, or an explicit list of tickers.",
                     "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
        "factors": {"type": "array", "description": "Factors and weights; ranks are blended by weight.",
                    "items": {"type": "object", "properties": {
                        "name": {"type": "string", "enum": list(F.FACTORS)},
                        "weight": {"type": "number"}}, "required": ["name", "weight"]}},
        "top_n": {"type": "integer", "description": "Number of names held."},
        "weighting": {"type": "string", "enum": ["equal", "inverse_vol", "score"]},
        "max_weight": {"type": "number", "description": "Per-name weight cap, 0-1; leftover stays in cash."},
        "rebalance": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
        "band": {"type": "integer", "description": "No-trade band: keep a holding until its rank drops below top_n + band."},
        "regime_filter": {"type": "string", "enum": ["none", "spy_above_200d"],
                          "description": "spy_above_200d: go to cash when SPY is below its 200-day average."},
        "commission_bps": {"type": "number", "description": "Per side, default 5. Don't set below 5."},
        "slippage_bps": {"type": "number", "description": "Per side, default 5. Don't set below 5."},
    },
    "required": ["name", "hypothesis", "universe", "factors"],
}

TOOLS = [
    {"name": "sector_scan",
     "description": "Relative strength of the 11 SPDR sector ETFs vs SPY: 1/3/6/12-month returns, excess vs SPY, "
                    "volatility, drawdown, and whether each is above its 200-day average.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_universe",
     "description": "Tickers available for a sector (omit sector to list all sectors and their sizes).",
     "input_schema": {"type": "object", "properties": {"sector": {"type": "string"}}}},
    {"name": "asset_stats",
     "description": "Per-ticker 1/3/6/12-month returns, volatility, beta to SPY, max drawdown and history length.",
     "input_schema": {"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}},
                      "required": ["symbols"]}},
    {"name": "list_factors",
     "description": "The factor library usable in strategy specs.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_backtest",
     "description": "Exploratory backtest on research data only (ends at the research cutoff). Use it to iterate. "
                    "It cannot approve anything.",
     "input_schema": {"type": "object", "properties": {
         "spec": SPEC_SCHEMA,
         "window_days": {"type": "integer", "description": "Trading days before the cutoff to evaluate (default 252)."}},
         "required": ["spec"]}},
    {"name": "submit_for_validation",
     "description": "Send a finished spec to the independent validator, which runs every acceptance criterion on the "
                    "full data including the held-out period. Submissions are limited and all are logged; don't "
                    "submit variants you haven't reasoned about.",
     "input_schema": {"type": "object", "properties": {"spec": SPEC_SCHEMA}, "required": ["spec"]}},
    {"name": "stress_test",
     "description": "Attack a strategy that PASSED validation (by candidate_id). Tests: cost_multiplier (value), "
                    "remove_symbols (symbols), period (start, end as YYYY-MM-DD), top_n (value), "
                    "rebalance_offset (value), standard_suite (calendar years + top_n neighborhood).",
     "input_schema": {"type": "object", "properties": {
         "candidate_id": {"type": "string"},
         "test": {"type": "string", "enum": ["cost_multiplier", "remove_symbols", "period", "top_n",
                                              "rebalance_offset", "standard_suite"]},
         "value": {"type": "number"},
         "symbols": {"type": "array", "items": {"type": "string"}},
         "start": {"type": "string"}, "end": {"type": "string"}},
         "required": ["candidate_id", "test"]}},
]

PHASE_TOOLS = {
    "research": {"sector_scan", "list_universe", "asset_stats", "list_factors"},
    "hypotheses": {"sector_scan", "list_universe", "asset_stats", "list_factors"},
    "backtest": {"sector_scan", "list_universe", "asset_stats", "list_factors", "run_backtest", "submit_for_validation"},
    "attack": {"list_factors", "stress_test"},
    "memo": set(),
}


def _pct(a, b):
    return float(a / b - 1) if np.isfinite(a) and np.isfinite(b) and b > 0 else None


def _stats(panel: Panel, sym: str) -> dict:
    p = panel.col(sym)
    bench = panel.col(BENCHMARK)
    ok = ~np.isnan(p)
    hist = int(ok.sum())
    out = {"symbol": sym, "history_days": hist, "last": round(float(p[-1]), 2) if ok[-1] else None}
    for label, d in (("1m", 21), ("3m", 63), ("6m", 126), ("12m", 252)):
        r = _pct(p[-1], p[-1 - d]) if len(p) > d else None
        b = _pct(bench[-1], bench[-1 - d]) if len(p) > d else None
        out[f"ret_{label}"] = None if r is None else round(r, 4)
        out[f"excess_{label}"] = None if r is None or b is None else round(r - b, 4)
    w = p[-252:]
    w = w[~np.isnan(w)]
    if len(w) > 60:
        r = np.diff(w) / w[:-1]
        br = np.diff(bench[-len(w):]) / bench[-len(w):-1]
        out["vol_12m"] = round(float(r.std(ddof=1) * np.sqrt(252)), 4)
        out["beta_12m"] = round(float(np.cov(r, br)[0, 1] / br.var(ddof=1)), 3)
        out["max_dd_12m"] = round(float((w / np.maximum.accumulate(w) - 1).min()), 4)
        sma = p[-200:].mean() if hist >= 200 else np.nan
        out["above_200d"] = bool(p[-1] > sma) if np.isfinite(sma) else None
    return out


@dataclass
class Toolbox:
    full: Panel
    research: Panel
    criteria: Criteria
    max_submissions: int = 8
    max_backtests: int = 80
    log: list = field(default_factory=list)
    candidates: dict = field(default_factory=dict)   # id -> {"spec", "result"}
    submissions: int = 0
    backtests: int = 0
    phase: str = "research"

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """Returns (json text, is_error)."""
        try:
            if name not in PHASE_TOOLS[self.phase]:
                raise PermissionError(f"{name} is not available in the {self.phase} phase")
            out = getattr(self, "_" + name)(**(args or {}))
            err = False
        except (ValueError, KeyError, TypeError, PermissionError) as e:
            out, err = {"error": str(e)}, True
        self.log.append({"phase": self.phase, "tool": name, "input": args, "output": out, "error": err})
        return json.dumps(out, default=str), err

    # -- tools --------------------------------------------------------------
    def _sector_scan(self):
        rows = []
        for sector, etf in SECTOR_ETFS.items():
            if etf in self.research.symbols:
                s = _stats(self.research, etf)
                s["sector"] = sector
                rows.append(s)
        rows.sort(key=lambda r: -(r.get("excess_3m") or -9))
        return {"as_of": str(self.research.dates[-1]), "benchmark": BENCHMARK, "sectors": rows}

    def _list_universe(self, sector: str | None = None):
        if not sector:
            return {s: len(v) for s, v in SECTOR_STOCKS.items()}
        if sector not in SECTOR_STOCKS:
            raise ValueError(f"unknown sector; choose from {list(SECTOR_STOCKS)}")
        names = SECTOR_STOCKS[sector]
        return {"sector": sector, "symbols": [s for s in names if s in self.research.symbols],
                "no_data": [s for s in names if s not in self.research.symbols]}

    def _asset_stats(self, symbols):
        return {"as_of": str(self.research.dates[-1]),
                "stats": [_stats(self.research, s.upper()) for s in symbols[:40] if s.upper() in self.research.symbols],
                "unknown": [s for s in symbols if s.upper() not in self.research.symbols]}

    def _list_factors(self):
        return {k: v[1] for k, v in F.FACTORS.items()}

    def _run_backtest(self, spec, window_days: int = 252):
        if self.backtests >= self.max_backtests:
            raise PermissionError(f"backtest budget of {self.max_backtests} used up")
        s = bt.Spec.from_dict(spec)
        self.backtests += 1
        start = str(self.research.dates[max(len(self.research.dates) - int(window_days), 0)])
        r = bt.public(bt.run(s, self.research, start=start))
        r["backtests_used"] = f"{self.backtests}/{self.max_backtests}"
        return r

    def _submit_for_validation(self, spec):
        if self.submissions >= self.max_submissions:
            raise PermissionError(f"validation budget of {self.max_submissions} used up")
        s = bt.Spec.from_dict(spec)
        self.submissions += 1
        cid = f"v{self.submissions}"
        res = validate(s, self.full, self.criteria)
        self.candidates[cid] = {"spec": s.to_dict(), "result": res}
        out = {"candidate_id": cid, "verdict": res["verdict"], "failed": res["failed"], "checks": res["checks"],
               "submissions_used": f"{self.submissions}/{self.max_submissions}"}
        return out

    def _stress_test(self, candidate_id, test, value=None, symbols=None, start=None, end=None):
        c = self.candidates.get(candidate_id)
        if not c:
            raise ValueError(f"unknown candidate_id {candidate_id!r}")
        if c["result"]["verdict"] != "PASS":
            raise PermissionError("stress tests are only for candidates that passed validation")
        s = bt.Spec.from_dict(c["spec"])
        trail = str(self.full.dates[max(len(self.full.dates) - self.criteria.trailing_days, 0)])
        if test == "standard_suite":
            return stress_suite(s, self.full, self.criteria)
        if test == "cost_multiplier":
            r = bt.run(s, self.full, start=trail, cost_mult=float(value or 2))
        elif test == "remove_symbols":
            s.exclude = [*s.exclude, *[x.upper() for x in symbols or []]]
            r = bt.run(s, self.full, start=trail)
        elif test == "period":
            r = bt.run(s, self.full, start=start, end=end)
        elif test == "top_n":
            s.top_n = int(value)
            r = bt.run(s, self.full, start=trail)
        elif test == "rebalance_offset":
            s.rebalance_offset = int(value)
            r = bt.run(s, self.full, start=trail)
        else:
            raise ValueError(f"unknown test {test}")
        return {"test": test, "result": bt.public(r)}
