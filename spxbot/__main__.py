from __future__ import annotations

import argparse
import logging

from .config import load_config
from .engine import Engine
from .notify import Notifier
from .state import State
from .tradier import TradierClient


def main() -> None:
    ap = argparse.ArgumentParser(prog="spxbot")
    ap.add_argument("-c", "--config", help="path to config.toml")
    ap.add_argument("--once", action="store_true", help="run a single tick and exit (for cron)")
    ap.add_argument("--chart", nargs="?", const="latest", metavar="DATE",
                    help="write an HTML chart of a day's spread (YYYY-MM-DD, default latest) and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config)
    if args.chart:
        from .chart import make_chart
        print(make_chart(cfg, State(cfg.state_file), None if args.chart == "latest" else args.chart))
        return
    if not cfg.tradier.token:
        ap.error("set TRADIER_TOKEN (or tradier.token in config)")
    if cfg.mode == "trade" and not cfg.tradier.account_id:
        ap.error("trade mode needs TRADIER_ACCOUNT_ID")

    engine = Engine(cfg, TradierClient(cfg.tradier), Notifier(cfg.notify), State(cfg.state_file))
    engine.tick() if args.once else engine.run_forever()


if __name__ == "__main__":
    main()
