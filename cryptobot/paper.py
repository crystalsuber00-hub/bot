"""Paper trader: once per bar close, recompute the signal and move a simulated position. No real orders."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .backtest import FEE, SLIPPAGE
from .data import get_candles
from .strategies import STRATEGIES

STATE = Path("crypto_state.json")


def load_state(cash: float) -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"cash": cash, "units": 0.0, "last_bar": 0, "trades": [], "start_equity": cash}


def tick(state: dict, product: str, strategy: str, params: dict, granularity: int) -> str:
    rows = get_candles(product, granularity, days=30)
    now = int(time.time())
    # drop the still-forming bar so signals only use closed candles
    rows = [r for r in rows if r[0] + granularity <= now]
    bar, price = rows[-1]
    if bar <= state["last_bar"]:
        return "no new closed bar"
    state["last_bar"] = bar
    closes = [c for _, c in rows]
    want = STRATEGIES[strategy][0](closes, **params)[-1]
    held = state["units"] > 0
    equity = state["cash"] + state["units"] * price
    if want and not held:
        cost = FEE + SLIPPAGE
        state["units"] = state["cash"] * (1 - cost) / price
        state["cash"] = 0.0
        state["trades"].append({"t": bar, "side": "buy", "price": price})
        msg = f"BUY  @ {price:.2f}"
    elif held and not want:
        state["cash"] = state["units"] * price * (1 - FEE - SLIPPAGE)
        state["units"] = 0.0
        state["trades"].append({"t": bar, "side": "sell", "price": price})
        msg = f"SELL @ {price:.2f}"
    else:
        msg = "hold" if held else "flat"
    equity = state["cash"] + state["units"] * price
    ret = equity / state["start_equity"] - 1
    return f"{datetime.fromtimestamp(bar, timezone.utc):%Y-%m-%d %H:%M}Z {msg}  equity ${equity:,.2f} ({ret:+.1%})"


def loop(product: str, strategy: str, params: dict, granularity: int, cash: float, once: bool) -> None:
    state = load_state(cash)
    while True:
        try:
            print(tick(state, product, strategy, params, granularity), flush=True)
            STATE.write_text(json.dumps(state, indent=1))
        except Exception as e:  # network blips shouldn't kill an unattended loop
            print(f"error: {e}", flush=True)
        if once:
            return
        time.sleep(300)
