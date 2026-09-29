import argparse
import json

from .backtest import run
from .data import get_candles
from .paper import loop
from . import robust


def main() -> None:
    p = argparse.ArgumentParser(prog="cryptobot", description="Crypto backtest and paper trading (no real orders).")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest", help="fetch data, then compare strategies out-of-sample against buy & hold")
    b.add_argument("--product", default="BTC-USD")
    b.add_argument("--days", type=int, default=730)
    b.add_argument("--no-refresh", action="store_true", help="use cached data only")
    sub.add_parser("robust", help="fixed-parameter breakout test across windows and coins (uses cached data)")
    t = sub.add_parser("paper", help="run the paper trader")
    t.add_argument("--product", default="BTC-USD")
    t.add_argument("--strategy", default="sma_cross", choices=["sma_cross", "breakout"])
    t.add_argument("--params", default="{}", help='JSON, e.g. \'{"fast":24,"slow":168}\'')
    t.add_argument("--cash", type=float, default=10000.0)
    t.add_argument("--once", action="store_true")
    a = p.parse_args()
    if a.cmd == "backtest":
        rows = get_candles(a.product, 3600, a.days, refresh=not a.no_refresh)
        print(f"{a.product}: {len(rows)} hourly bars")
        run([c for _, c in rows])
    elif a.cmd == "robust":
        robust.run()
    else:
        loop(a.product, a.strategy, json.loads(a.params), 3600, a.cash, a.once)


if __name__ == "__main__":
    main()
