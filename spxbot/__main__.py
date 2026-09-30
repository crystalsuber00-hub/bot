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
    try:
        _main()
    except RuntimeError as e:
        if type(e).__name__ != "SchwabLoginNeeded":
            raise
        raise SystemExit(f"spxbot: {e}")


def _main() -> None:
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
    ap.add_argument("--schwab-login", action="store_true",
                    help="log in to Schwab (needed once, then every 7 days), then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config)
    spy03 = cfg.model in ("spy03", "stocks")
    if cfg.model == "stocks" and args.report:
        from .stocks import StocksState
        from .stocks_report import build_report as stocks_report
        print(stocks_report(StocksState(cfg.stocks.state_file), args.days))
        return
    if spy03 and (args.report or args.chart or args.clear_halt):
        ap.error("--report/--chart/--clear-halt are for the spx_credit model; spy03 history is in "
                 + cfg.spy03.state_file)
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
    if cfg.broker == "schwab":
        from . import schwab as _schwab
        from .schwab import SchwabClient, auth_url
        _schwab.CONFIG_HINT = args.config or "<config>"
        if not (cfg.schwab.app_key and cfg.schwab.app_secret):
            ap.error("set SCHWAB_APP_KEY and SCHWAB_APP_SECRET (from your app on developer.schwab.com)")
        client = SchwabClient(cfg.schwab)
        if args.schwab_login:
            print("1. Open this link, log in with your Schwab brokerage login, and allow access:\n\n   "
                  + auth_url(cfg.schwab) + "\n\n2. The browser then shows an error page on "
                  f"{cfg.schwab.callback_url} - that's expected. Copy the WHOLE address bar and paste it here.")
            client.login(input("\nPasted address: "))
            print(f"Logged in. Token saved to {cfg.schwab.token_file}; valid for 7 days.")
            return
    elif args.schwab_login:
        ap.error("--schwab-login needs broker = \"schwab\" in the config")
    elif cfg.broker == "ibkr":
        from .ibkr import IBKRClient
        client = IBKRClient(cfg.ibkr, readonly=spy03 or cfg.mode == "signal",
                            underlying=cfg.spy03.symbol if cfg.model == "spy03" else cfg.symbol)
    else:
        if not cfg.tradier.token:
            ap.error("set TRADIER_TOKEN (or tradier.token in config)")
        if cfg.mode == "trade" and not spy03 and not cfg.tradier.account_id:
            ap.error("trade mode needs TRADIER_ACCOUNT_ID")
        client = TradierClient(cfg.tradier)

    if cfg.model == "stocks":
        from .stocks import StocksEngine, StocksState
        if cfg.broker == "ibkr":
            ap.error("model 'stocks' needs broker 'schwab' or 'tradier'")
        engine = StocksEngine(cfg, client, Notifier(cfg.notify), StocksState(cfg.stocks.state_file))
        if cfg.broker == "schwab":
            left = client.refresh_days_left()
            relogin = f"spxbot -c {args.config} --schwab-login"
            if args.check:
                if left <= 0:
                    raise SystemExit(f"[!!] Not logged in to Schwab (or the 7-day login expired). Run:  {relogin}")
                print(f"[{'ok' if left > 1.5 else '!!'}] Schwab login valid for {left * 24:.0f} more hours"
                      + ("" if left > 1.5 else f" - log in again soon:  {relogin}"))
            elif left < 1.5:
                engine.notify.send(f"Schwab login expires in {max(left, 0) * 24:.0f} h. Run:  {relogin}",
                                   {"event": "login_expiring"})
        if args.check:
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo
            from .spy03 import pick_contract, pick_expiration
            from .stocks import rules_for
            k, today = cfg.stocks, _dt.now(ZoneInfo(cfg.schedule.timezone)).date()  # market date, not the Mac's
            failed = 0
            for sym in k.watchlist:
                try:
                    px = client.get_quote(sym).last
                    exp = pick_expiration(client.get_expirations(sym), today, 0)
                    if not exp:
                        print(f"[--] {sym} {px:.2f}: no option expiring today")
                        continue
                    picks = []
                    for right in ("call", "put"):
                        o, why = pick_contract(client.get_chain(sym, exp, sym, right, near=max(px * 0.05, 5)),
                                               right, px, rules_for(k, 1.0))
                        picks.append(f"{o.strike:g}{right[0].upper()} ${o.mid * 100:.0f} (delta {abs(o.delta):.2f})"
                                     if o else f"no {right} under ${k.max_contract_cost:g}")
                    print(f"[ok] {sym} {px:.2f}: 0DTE {exp} -> " + ", ".join(picks))
                except Exception as e:
                    failed += 1
                    print(f"[!!] {sym}: {e}")
            engine.notify.send("0DTE stock signals test alert: notifications work.", {"event": "test"})
            print("[ok] test alert sent - check your phone. No orders are ever placed by this model.")
            if failed:
                raise SystemExit(f"[!!] {failed} ticker(s) failed - see above.")
            print("[ok] ready.")
            return
        if args.once:
            return engine.tick()
        lock = single_instance(cfg.lock_port)  # keep the reference: the lock lasts while the socket is open
        if lock is None:
            raise SystemExit("spxbot is already running (lock port in use); not starting a second copy")
        return engine.run_forever(args.until)

    if spy03:
        from .spy03_engine import Spy03Engine, Spy03State
        engine = Spy03Engine(cfg, client, Notifier(cfg.notify), Spy03State(cfg.spy03.state_file))
        if cfg.broker == "schwab" and client.refresh_days_left() < 1.5 and not args.check:
            engine.notify.send(f"Schwab login expires in {max(client.refresh_days_left(), 0) * 24:.0f} h. "
                               "Run: spxbot -c <config> --schwab-login", {"event": "login_expiring"})
        if args.check:
            raise SystemExit(0 if engine.check() else 1)
        if args.once:
            return engine.tick()
        lock = single_instance(cfg.lock_port)
        if lock is None:
            raise SystemExit("spxbot is already running (lock port in use); not starting a second copy")
        return engine.run_forever(args.until)

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
