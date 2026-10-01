from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

from .api import Client


def main(argv=None):
    ap = argparse.ArgumentParser(prog="polyscan", description="Rank and profile top Polymarket wallets; alert on their trades.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="rank wallets by trailing P&L and profile the leaders")
    s.add_argument("--days", type=int, default=90)
    s.add_argument("--depth-all", type=int, default=1000, help="all-time leaderboard rows to pull")
    s.add_argument("--depth-month", type=int, default=500, help="monthly leaderboard rows to pull")
    s.add_argument("--deep", type=int, default=100, help="how many leaders to profile in depth")
    s.add_argument("--trade-cap", type=int, default=5000, help="max trades read per wallet")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--out", default="polyscan_out")

    r = sub.add_parser("report", help="re-render the HTML dashboard from a saved scan")
    r.add_argument("--out", default="polyscan_out")

    w = sub.add_parser("watch", help="alert when the best wallets trade (signal only, no orders)")
    w.add_argument("--out", default="polyscan_out", help="folder holding scan.json")
    w.add_argument("--top", type=int, default=15, help="watch the N highest-scoring wallets")
    w.add_argument("--min-score", type=float, default=50)
    w.add_argument("--wallet", action="append", default=[], help="watch this address too (repeatable)")
    w.add_argument("--allow-bots", action="store_true", help="include high-frequency wallets")
    w.add_argument("--min-usd", type=float, default=1000, help="ignore trades smaller than this")
    w.add_argument("--no-sells", action="store_true")
    w.add_argument("--bankroll", type=float, default=0,
                   help="your account size in $: adds a live-price COPY/SKIP plan and stake to each alert")
    w.add_argument("--every", type=int, default=60, help="seconds between polls")
    w.add_argument("--autotrade", action="store_true",
                   help="copy game-winner buys onto Polymarket US; PAPER trades unless --live is also given")
    w.add_argument("--live", action="store_true",
                   help="with --autotrade: place REAL orders (needs POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY)")
    w.add_argument("--max-slip", type=float, default=0.03, help="max all-in price above the copied fill")
    w.add_argument("--max-losses", type=int, default=3, help="stop buying for the day after this many losses")
    w.add_argument("--once", action="store_true")
    w.add_argument("-v", "--verbose", action="store_true")

    t = sub.add_parser("trades", help="show copy-trading results (paper and live)")
    t.add_argument("--out", default="polyscan_out")
    t.add_argument("--live", action="store_true", help="show the live account's log instead of paper")
    t.add_argument("--bankroll", type=float, default=30)
    t.add_argument("--reset-halt", action="store_true", help="clear a drawdown halt so buying can resume")

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(a, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    out = Path(a.out)

    if a.cmd == "scan":
        from . import report, scan
        out.mkdir(parents=True, exist_ok=True)
        res = scan.run(days=a.days, depth_all=a.depth_all, depth_month=a.depth_month, deep=a.deep,
                       trade_cap=a.trade_cap, workers=a.workers)
        (out / "scan.json").write_text(json.dumps(res))
        write_csv(res, out / "wallets.csv")
        (out / "index.html").write_text(report.render(res))
        logging.info("wrote %s/{scan.json,wallets.csv,index.html} in %ss", out, res["elapsed_s"])
    elif a.cmd == "report":
        from . import report
        (out / "index.html").write_text(report.render(json.loads((out / "scan.json").read_text())))
        logging.info("wrote %s/index.html", out)
    elif a.cmd == "trades":
        from .ustrade import CopyTrader, USClient
        tr = CopyTrader(USClient(), str(out / ("trades_live.json" if a.live else "trades_paper.json")), a.bankroll)
        tr.live = a.live  # display only; this command never places orders
        if a.reset_halt:
            tr.state["halted"] = ""
            tr.save()
        tr.refresh()
        print(tr.summary())
    elif a.cmd == "watch":
        from .watch import Watcher, notifier_from_env, pick_watchlist
        scan_file = out / "scan.json"
        wl = pick_watchlist(json.loads(scan_file.read_text()), a.top, a.min_score, a.allow_bots) if scan_file.exists() else []
        wl += [{"wallet": x.lower(), "name": x[:10], "score": 0, "pnl": 0, "style": "manual"} for x in a.wallet]
        if not wl:
            raise SystemExit("nothing to watch: run `polyscan scan` first or pass --wallet")
        for x in wl:
            logging.info("watching %-24s score %5.1f  90d $%12s  %s", x["name"], x["score"], f"{x['pnl']:,.0f}", x["style"])
        out.mkdir(parents=True, exist_ok=True)
        trader = None
        if a.live and not a.autotrade:
            raise SystemExit("--live only makes sense with --autotrade")
        if a.autotrade:
            from .ustrade import CopyTrader, USClient
            if not a.bankroll:
                raise SystemExit("--autotrade needs --bankroll (e.g. --bankroll 30)")
            trader = CopyTrader(USClient.from_env(), str(out / ("trades_live.json" if a.live else "trades_paper.json")),
                                a.bankroll, live=a.live, max_slip=a.max_slip, max_losses_per_day=a.max_losses,
                                stop_file=str(out / "STOP"))
            logging.warning("%s copy trading on Polymarket US: $%.2f per bet, $%.0f max in open bets. "
                            "Create %s/STOP to stop buying.", trader.mode, trader.stake, a.bankroll, out)
        wt = Watcher(Client(), notifier_from_env(), wl, str(out / "watch_state.json"), min_usd=a.min_usd,
                     sells=not a.no_sells, bankroll=a.bankroll, trader=trader)
        if trader and trader.live:
            wt.n.send(f"LIVE copy trading started: ${trader.stake:.2f} per bet, ${a.bankroll:.0f} max",
                      {"event": "entry"})
        if a.once:
            wt.tick()
        else:
            wt.loop(a.every)


def write_csv(res: dict, path: Path):
    cols = ["rank", "name", "wallet", "score", "style", "pnl_90d", "pnl_30d", "pnl_7d", "pnl_all", "max_drawdown",
            "sharpe", "green_week_rate", "resolved", "win_rate", "profit_factor", "roi", "avg_entry", "markets_per_day", "fills_per_day", "days_since_trade",
            "median_trade", "open_value", "flags"]
    with path.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(cols)
        for p in res["profiles"]:
            s, q, t = p["series"], p["positions"], p["trading"]
            wr.writerow([p["rank"], p["name"], p["wallet"], p["score"], p["style"], round(s["pnl"]), round(s["pnl_30d"]),
                         round(s["pnl_7d"]), round(s["pnl_all"]), round(s["max_drawdown"]), round(s["sharpe"], 2),
                         round(s["green_week_rate"], 2), q["resolved"], round(q["win_rate"], 3),
                         round(q["profit_factor"], 2) if q["profit_factor"] != float("inf") else "inf",
                         round(q["roi"], 3), round(q["avg_entry"], 3), round(t.get("markets_per_day", 0), 1), round(t["trades_per_day"], 1),
                         "" if t.get("days_since_trade") is None else round(t["days_since_trade"], 1),
                         round(t["median_trade"], 2), round(q["open_value"]), " ".join(p["flags"])])


if __name__ == "__main__":
    main()
