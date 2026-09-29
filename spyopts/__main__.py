import argparse

from .backtest import run

p = argparse.ArgumentParser(prog="spyopts", description="SPY option strategy backtest (model prices, no orders)")
p.add_argument("--refresh", action="store_true", help="download SPY and VIX daily history from Yahoo")
run(p.parse_args().refresh)
