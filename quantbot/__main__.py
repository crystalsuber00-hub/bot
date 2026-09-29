from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import backtest as bt
from .data import load_panel, refresh
from .tools import Toolbox, _stats
from .universe import SECTOR_ETFS, SECTOR_STOCKS
from .validate import Criteria, stress_suite, validate


def notifier():
    from spxbot.config import Notify
    from spxbot.notify import Notifier
    env = os.environ.get
    return Notifier(Notify(telegram_bot_token=env("TELEGRAM_BOT_TOKEN", ""), telegram_chat_id=env("TELEGRAM_CHAT_ID", ""),
                           discord_webhook_url=env("DISCORD_WEBHOOK_URL", ""), webhook_url=env("WEBHOOK_URL", ""),
                           ntfy_topic=env("NTFY_TOPIC", ""), ntfy_token=env("NTFY_TOKEN", "")))


def load_spec(path: str) -> bt.Spec:
    with open(path) as f:
        return bt.Spec.from_dict(json.load(f))


def sector_table(panel) -> list[dict]:
    rows = []
    for sector, etf in SECTOR_ETFS.items():
        if etf in panel.symbols:
            rows.append({"sector": sector, **_stats(panel, etf)})
    return sorted(rows, key=lambda r: -(r.get("excess_3m") or -9))


def make_run(args, sector: str | None = None):
    from .agent import Run
    full = load_panel()
    research = full.slice(end=full.dates[-1 - args.holdout_days]) if args.holdout_days else full
    tb = Toolbox(full, research, Criteria(), max_submissions=args.max_submissions, max_backtests=args.max_backtests)
    return Run(tb, model=args.model, effort=args.effort, n_hypotheses=args.hypotheses)


def main() -> None:
    ap = argparse.ArgumentParser(prog="quantbot", description="Claude-driven strategy research with an independent backtest judge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="download/refresh the daily price cache (data/prices)")
    sub.add_parser("scan", help="sector relative strength vs SPY (no Claude)")
    for name in ("backtest", "validate"):
        p = sub.add_parser(name, help=f"{name} a strategy spec JSON file (no Claude)")
        p.add_argument("spec")
        if name == "backtest":
            p.add_argument("--days", type=int, default=252, help="trailing trading days to evaluate")
    for name in ("research", "weekly"):
        p = sub.add_parser(name, help="run the full Claude research loop" if name == "research"
                           else "scan sectors; research the strongest one only if it's leading SPY, alert only on a PASS")
        p.add_argument("--model", default="claude-opus-5-5")
        p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
        p.add_argument("--hypotheses", type=int, default=4 if name == "research" else 3)
        p.add_argument("--holdout-days", type=int, default=63, help="recent days hidden from exploration (0 = none)")
        p.add_argument("--max-submissions", type=int, default=8)
        p.add_argument("--max-backtests", type=int, default=80)
        if name == "weekly":
            p.add_argument("--threshold", type=float, default=0.10, help="3-month excess return vs SPY needed to trigger")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "fetch":
        failed = refresh()
        print("done" + (f"; failed: {failed}" if failed else ""))
        return
    if args.cmd == "scan":
        for r in sector_table(load_panel()):
            print(f"{r['sector']:<24} {r['symbol']:<5} 3m {r['ret_3m']:+.1%} (vs SPY {r['excess_3m']:+.1%})  "
                  f"6m {r['ret_6m']:+.1%}  12m {r['ret_12m']:+.1%}  above 200d: {r.get('above_200d')}")
        return
    if args.cmd == "backtest":
        panel = load_panel()
        r = bt.run(load_spec(args.spec), panel, start=str(panel.dates[-args.days]))
        print(json.dumps(bt.public(r), indent=2))
        return
    if args.cmd == "validate":
        panel = load_panel()
        spec, c = load_spec(args.spec), Criteria()
        res = validate(spec, panel, c)
        for chk in res["checks"]:
            print(f"{'PASS' if chk['passed'] else 'FAIL'}  {chk['check']}: {chk['detail']}")
        print(f"\nVERDICT: {res['verdict']}")
        print(json.dumps(stress_suite(spec, panel, c), indent=2))
        sys.exit(0 if res["verdict"] == "PASS" else 1)
    if args.cmd == "research":
        report = make_run(args).execute()
        print(f"report: {report}")
        return
    if args.cmd == "weekly":
        table = sector_table(load_panel())
        hot = [r for r in table if (r.get("excess_3m") or 0) > args.threshold]
        if not hot:
            logging.info("no sector leads SPY by more than %.0f%% over 3 months; nothing to research", args.threshold * 100)
            return
        top = hot[0]
        from .agent import PHASES
        run = make_run(args)
        evidence = json.dumps(top)
        phases = [("research",
                   f"Step 1 - this week's scan flagged {top['sector']} ({top['symbol']}) as the strongest sector: {evidence}. "
                   f"Its tradable names are {SECTOR_STOCKS[top['sector']]}. Check the evidence with the tools and say whether "
                   "the strength looks broad or driven by a few names. Do NOT propose a strategy yet."),
                  *PHASES[1:]]
        report = run.execute(phases)
        passed = run.passed()
        print(f"report: {report}")
        if passed:
            names = ", ".join(f"{cid} {c['spec']['name']}" for cid, c in passed.items())
            notifier().send(f"quantbot: strategy passed validation\n{top['sector']}: {names}. "
                            f"Review {report} before doing anything with it.", {"event": "research_pass"})
        return


if __name__ == "__main__":
    main()
