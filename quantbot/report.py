"""Markdown report built from the tool log (the record), with Claude's memo appended as commentary."""
from __future__ import annotations

import json


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.2%}"


def render(run) -> str:
    tb = run.toolbox
    lines = [f"# Strategy research run - {run.out_dir.name}", ""]
    lines += [f"- Model: `{run.model}` (effort {run.effort})",
              f"- Research data: {tb.research.dates[0]} .. {tb.research.dates[-1]}; "
              f"validator data to {tb.full.dates[-1]} (held out: {len(tb.full.dates) - len(tb.research.dates)} days)",
              f"- Exploratory backtests: {tb.backtests}/{tb.max_backtests}; validation submissions: "
              f"{tb.submissions}/{tb.max_submissions}"]
    cost = run.usage.cost(run.model)
    lines.append(f"- API usage: {run.usage.calls} calls, {run.usage.input + run.usage.cache_read + run.usage.cache_write:,} input "
                 f"/ {run.usage.output:,} output tokens" + (f", about ${cost:.2f}" if cost is not None else ""))
    if run.stopped:
        lines.append(f"- Stopped early: {run.stopped}")
    lines += ["", "## Rejection log (from the validator)", ""]
    if not tb.candidates:
        lines.append("Nothing was submitted to the validator.")
    else:
        lines.append("| id | strategy | verdict | trailing return | SPY | Sharpe | max DD | failed checks |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for cid, c in tb.candidates.items():
            t = c["result"]["trailing"]
            lines.append(f"| {cid} | {c['spec']['name']} | **{c['result']['verdict']}** | {_pct(t['total_return'])} | "
                         f"{_pct(t['benchmark_return'])} | {t['sharpe']} | {_pct(t['max_drawdown'])} | "
                         f"{', '.join(c['result']['failed']) or '-'} |")
    passed = run.passed()
    lines += ["", "## Survivors", ""]
    if not passed:
        lines.append("None. No strategy met every acceptance criterion; nothing should be deployed from this run.")
    for cid, c in passed.items():
        lines += [f"### {cid}: {c['spec']['name']}", "", c["spec"].get("hypothesis", ""), ""]
        for chk in c["result"]["checks"]:
            lines.append(f"- {'PASS' if chk['passed'] else 'FAIL'} {chk['check']}: {chk['detail']}")
        lines += ["", "```json", json.dumps(c["spec"], indent=2), "```", ""]
        attacks = [e for e in tb.log if e["tool"] == "stress_test" and e["input"].get("candidate_id") == cid]
        if attacks:
            lines.append("Stress tests run by Claude:")
            for e in attacks:
                o = e["output"]
                if e["error"]:
                    lines.append(f"- {e['input'].get('test')}: error {o.get('error')}")
                elif "result" in o:
                    r = o["result"]
                    lines.append(f"- {e['input']}: {_pct(r['total_return'])} vs SPY {_pct(r['benchmark_return'])}, "
                                 f"Sharpe {r['sharpe']}, max DD {_pct(r['max_drawdown'])}")
                else:
                    for y in o.get("calendar_years", []):
                        lines.append(f"- {y['year']}: {_pct(y['return'])} vs SPY {_pct(y['spy'])} "
                                     f"(max DD {_pct(y['max_dd'])} vs {_pct(y['spy_max_dd'])})")
                    for n in o.get("top_n_neighborhood", []):
                        lines.append(f"- top_n={n['top_n']}: excess {_pct(n['excess'])}, Sharpe {n['sharpe']}")
            lines.append("")
        lines.append("A PASS is a candidate for human review, not a recommendation to trade. Paper-trade it first.")
    lines += ["", "## Claude's commentary", ""]
    for phase, text in run.transcript:
        lines += [f"**[{phase}]**", "", text, ""]
    return "\n".join(lines) + "\n"
