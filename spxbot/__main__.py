from __future__ import annotations

import argparse
import logging
import os
import socket

from .config import load_config
from .engine import Engine
from .notify import Notifier
from .state import State
from .tradier import TradierClient


def load_dotenv(path: str = ".env") -> None:
    """Read NAME=value lines from .env into the environment (values already set in the shell win)."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.split(" #", 1)[0].strip().strip('"').strip("'")
        if name.isidentifier() and value and name not in os.environ:
            os.environ[name] = value


def _clipboard() -> str | None:
    """Mac clipboard text, or None where there's no pbpaste."""
    import shutil
    import subprocess
    if not shutil.which("pbpaste"):
        return None
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:
        return None


def wait_for_address(callback: str, clipboard=_clipboard, timeout: float = 600) -> str:
    """Take the 127.0.0.1 address the moment it's copied (Mac), or whatever is pasted + Return."""
    import select
    import sys
    import time
    start_clip = clipboard()
    watching = start_clip is not None
    print(">>> Just COPY the address in the browser (Cmd+A, Cmd+C) - it's picked up automatically."
          if watching else "", ">>> Or paste it here and press Return: ", sep="\n" if watching else "", end="", flush=True)
    end = time.time() + timeout
    while time.time() < end:
        if watching:
            clip = clipboard()
            if clip and clip != start_clip and "code=" in clip and clip.startswith(callback.rstrip("/")):
                print("\n[ok] got the address from the clipboard")
                return clip
        try:
            ready = select.select([sys.stdin], [], [], 0.3)[0] if watching else [sys.stdin]
        except (OSError, ValueError):  # stdin not selectable: plain input
            ready = [sys.stdin]
        if ready:
            line = sys.stdin.readline().strip()
            if line:
                return line
            if not watching:
                break
    raise SystemExit("No address received. Run the login command again.")


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
    ap.add_argument("--schwab-login", nargs="?", const="", default=None, metavar="ADDRESS",
                    help="log in to Schwab (once, then every 7 days). Optionally pass the 127.0.0.1 address in quotes")
    args = ap.parse_args()
    load_dotenv()  # so keys work without "set -a && . ./.env" in every new Terminal window
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
            ap.error("SCHWAB_APP_KEY and SCHWAB_APP_SECRET are missing. Put them in the .env file in this folder "
                     "(open -e .env), or run this from the bot folder (cd ~/bot)")
        client = SchwabClient(cfg.schwab)
        if args.schwab_login is not None:
            address = args.schwab_login
            if not address:
                import webbrowser
                link = auth_url(cfg.schwab)
                print("Opening the Schwab login page in your browser (if it doesn't open, copy this link):\n\n   "
                      + link + "\n\n1. Log in with your normal Schwab login and click Allow.\n"
                      f"2. The browser lands on a 'can't be reached' page at {cfg.schwab.callback_url} - that's expected.\n"
                      "3. Copy that page's WHOLE address (Cmd+A, Cmd+C), come back to THIS window,\n"
                      "   paste it below (Cmd+V) and press Return. Be quick: the code expires in about 30 seconds.\n")
                try:
                    webbrowser.open(link)
                except Exception:
                    pass
                address = wait_for_address(cfg.schwab.callback_url)
            client.login(address)
            print(f"Logged in. Token saved to {cfg.schwab.token_file}; valid for 7 days.")
            return
    elif args.schwab_login is not None:
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
            relogin = f".venv/bin/python -m spxbot -c {args.config} --schwab-login"
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
