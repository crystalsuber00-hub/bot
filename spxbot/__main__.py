from __future__ import annotations

import argparse
import logging
import socket

from .config import load_config
from .engine import Engine
from .notify import Notifier
from .state import State
from .tradier import TradierClient


def single_instance(port: int):
    """Hold a localhost port for the life of the process; returns None if another bot already holds it."""
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        return None
    return sock


def main() -> None:
    ap = argparse.ArgumentParser(prog="spxbot")
    ap.add_argument("-c", "--config", help="path to config.toml")
    ap.add_argument("--once", action="store_true", help="run a single tick and exit (for cron)")
    ap.add_argument("--until", metavar="HH:MM",
                    help="stop by itself at this time (in schedule.timezone, e.g. 16:10); used by the daily auto-start")
    ap.add_argument("--check", action="store_true",
                    help="test connection, data, strike selection and alerts (places no orders), then exit")
    ap.add_argument("--report", action="store_true",
                    help="summarize recorded signals/trades and compare with the backtest model, then exit")
    ap.add_argument("--days", type=int, help="with --report: only the last N calendar days")
    ap.add_argument("--clear-halt", action="store_true",
                    help="clear a freeze/halt after you've fixed the position at your broker, then exit")
    ap.add_argument("--chart", nargs="?", const="latest", metavar="DATE",
                    help="write an HTML chart of a day's spread (YYYY-MM-DD, default latest) and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config)
    if args.report:
        from .report import build_report
        print(build_report(cfg, State(cfg.state_file), args.days))
        return
    if args.clear_halt:
        st = State(cfg.state_file)
        print("was:", st.halt_info())
        st.clear_halt()
        return
    if args.chart:
        from .chart import make_chart
        print(make_chart(cfg, State(cfg.state_file), None if args.chart == "latest" else args.chart))
        return
    if cfg.broker == "ibkr":
        from .ibkr import IBKRClient
        client = IBKRClient(cfg.ibkr, readonly=cfg.mode == "signal", underlying=cfg.symbol)
    else:
        if not cfg.tradier.token:
            ap.error("set TRADIER_TOKEN (or tradier.token in config)")
        if cfg.mode == "trade" and not cfg.tradier.account_id:
            ap.error("trade mode needs TRADIER_ACCOUNT_ID")
        client = TradierClient(cfg.tradier)

    if args.check:
        from .check import run_check
        raise SystemExit(0 if run_check(cfg, client, Notifier(cfg.notify)) else 1)

    engine = Engine(cfg, client, Notifier(cfg.notify), State(cfg.state_file))
    if args.once:
        return engine.tick()
    lock = single_instance(cfg.lock_port)
    if lock is None:
        raise SystemExit("spxbot is already running (lock port in use); not starting a second copy")
    engine.run_forever(args.until)


if __name__ == "__main__":
    main()
