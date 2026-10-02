from __future__ import annotations

import argparse
import logging


def main(argv=None):
    ap = argparse.ArgumentParser(prog="memebot", description="Replay copying Solana memecoin wallets from public data.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="pick wallets on past data, then replay copying them")
    r.add_argument("--test-days", type=float, default=3, help="days to replay, ending now")
    r.add_argument("--select-days", type=float, default=4, help="days before that used to pick wallets")
    r.add_argument("--top", type=int, default=8, help="wallets to copy")
    r.add_argument("--candidates", type=int, default=25, help="wallets to examine")
    r.add_argument("--bankroll", type=float, default=100)
    r.add_argument("--max-tx-per-day", type=float, default=80, help="skip busier wallets (bots)")
    r.add_argument("--out", default="memebot_out")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from . import replay
    res = replay.run(a.test_days, a.select_days, a.top, a.candidates, a.bankroll, a.max_tx_per_day, a.out)
    print(replay.report(res))


if __name__ == "__main__":
    main()
