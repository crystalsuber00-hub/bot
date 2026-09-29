"""The judge. Acceptance criteria are fixed before any research starts; Claude cannot change
them and cannot declare a strategy successful - only ``validate`` can.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from . import backtest as bt
from .data import Panel


@dataclass
class Criteria:
    trailing_days: int = 252            # "trailing 1 year"
    long_window_days: int = 756         # "3-year" check (catches strategies that only worked recently)
    min_sharpe: float = 1.0
    max_drawdown: float = 0.30
    max_top_contributor_share: float = 0.40
    cost_stress_mult: float = 2.0
    require_long_window_beat: bool = True
    require_without_top_name: bool = True
    require_rebalance_offset: bool = True


def _start(panel: Panel, days: int) -> str:
    return str(panel.dates[max(len(panel.dates) - days, 0)])


def validate(spec: bt.Spec, panel: Panel, c: Criteria) -> dict:
    trail = _start(panel, c.trailing_days)
    base = bt.run(spec, panel, start=trail)
    again = bt.run(spec, panel, start=trail)
    stressed = bt.run(spec, panel, start=trail, cost_mult=c.cost_stress_mult)
    checks = []

    def check(name, passed, detail):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check("beats_spy_after_costs_trailing_1y", base["excess_return"] > 0,
          f"{base['total_return']:.2%} vs SPY {base['benchmark_return']:.2%}")
    check(f"beats_spy_with_{c.cost_stress_mult:g}x_costs", stressed["excess_return"] > 0,
          f"{stressed['total_return']:.2%} vs SPY {stressed['benchmark_return']:.2%}")
    check("max_drawdown_under_limit", abs(base["max_drawdown"]) < c.max_drawdown,
          f"{base['max_drawdown']:.2%} (limit -{c.max_drawdown:.0%})")
    check("sharpe_above_min", base["sharpe"] > c.min_sharpe, f"{base['sharpe']:.2f} (min {c.min_sharpe})")
    share = base["top_contributor_share"]
    check("no_single_name_dominates", share is not None and share <= c.max_top_contributor_share,
          f"{base['top_contributor']} = {share if share is None else f'{share:.0%}'} of returns "
          f"(max {c.max_top_contributor_share:.0%})")
    check("deterministic", base["equity_hash"] == again["equity_hash"],
          f"{base['equity_hash']} / {again['equity_hash']}")

    long_run = None
    if c.require_long_window_beat:
        long_run = bt.run(spec, panel, start=_start(panel, c.long_window_days))
        check("beats_spy_long_window", long_run["excess_return"] > 0,
              f"{long_run['window'][0]}..: {long_run['total_return']:.2%} vs SPY {long_run['benchmark_return']:.2%}, "
              f"max DD {long_run['max_drawdown']:.2%}")
        check("long_window_drawdown_under_limit", abs(long_run["max_drawdown"]) < c.max_drawdown,
              f"{long_run['max_drawdown']:.2%}")
    if c.require_without_top_name:
        top = base["top_contributor"]
        without = bt.run(replace(spec, exclude=[*spec.exclude, top]), panel, start=trail)
        check("beats_spy_without_top_name", without["excess_return"] > 0,
              f"without {top}: {without['total_return']:.2%} vs SPY {without['benchmark_return']:.2%}")
    if c.require_rebalance_offset and spec.rebalance != "daily":
        shift = 2 if spec.rebalance == "weekly" else 7
        off = bt.run(replace(spec, rebalance_offset=spec.rebalance_offset + shift), panel, start=trail)
        check("beats_spy_with_shifted_rebalance_day", off["excess_return"] > 0,
              f"offset +{shift} days: {off['total_return']:.2%} vs SPY {off['benchmark_return']:.2%}")

    failed = [x["check"] for x in checks if not x["passed"]]
    return {
        "verdict": "PASS" if not failed else "REJECTED",
        "failed": failed,
        "checks": checks,
        "trailing": bt.public(base),
        "long_window": bt.public(long_run) if long_run else None,
        "criteria": asdict(c),
    }


def calendar_years(spec: bt.Spec, panel: Panel) -> list[dict]:
    """Per-year excess return and drawdown vs SPY (a regime report, not a pass/fail gate)."""
    years = sorted({int(str(d)[:4]) for d in panel.dates})
    out = []
    for y in years:
        try:
            r = bt.run(spec, panel, start=f"{y}-01-01", end=f"{y}-12-31")
        except ValueError:
            continue
        out.append({"year": y, "return": r["total_return"], "spy": r["benchmark_return"],
                    "excess": r["excess_return"], "max_dd": r["max_drawdown"], "spy_max_dd": r["benchmark_max_drawdown"]})
    return out


def neighborhood(spec: bt.Spec, panel: Panel, c: Criteria) -> list[dict]:
    """Nearby parameter settings: a real edge shouldn't vanish when top_n moves by one or two."""
    trail = _start(panel, c.trailing_days)
    out = []
    for n in sorted({max(1, spec.top_n - 2), max(1, spec.top_n - 1), spec.top_n + 1, spec.top_n + 2} - {spec.top_n}):
        if n > len(spec.universe):
            continue
        r = bt.run(replace(spec, top_n=n), panel, start=trail)
        out.append({"top_n": n, "excess": r["excess_return"], "sharpe": r["sharpe"]})
    return out


def stress_suite(spec: bt.Spec, panel: Panel, c: Criteria) -> dict:
    return {"calendar_years": calendar_years(spec, panel), "top_n_neighborhood": neighborhood(spec, panel, c)}

