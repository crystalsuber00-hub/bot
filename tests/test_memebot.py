import pytest

from memebot.data import STABLES, WSOL, parse_swap
from memebot.replay import realized_pnl, simulate

W = "Wallet1111"
MINT = "Meme1111pump"


def tx(sol_delta, tok_pre, tok_post, t=1000, err=None, wsol=None, usdc=None):
    pre = [{"owner": W, "mint": MINT, "uiTokenAmount": {"uiAmount": tok_pre}}] if tok_pre is not None else []
    post = [{"owner": W, "mint": MINT, "uiTokenAmount": {"uiAmount": tok_post}}]
    if usdc:
        u = sorted(STABLES)[0]
        pre.append({"owner": W, "mint": u, "uiTokenAmount": {"uiAmount": usdc[0]}})
        post.append({"owner": W, "mint": u, "uiTokenAmount": {"uiAmount": usdc[1]}})
    if wsol:
        pre.append({"owner": W, "mint": WSOL, "uiTokenAmount": {"uiAmount": wsol[0]}})
        post.append({"owner": W, "mint": WSOL, "uiTokenAmount": {"uiAmount": wsol[1]}})
    return {"blockTime": t, "transaction": {"signatures": ["sig"], "message": {"accountKeys": [{"pubkey": W}, {"pubkey": "pool"}]}},
            "meta": {"err": err, "preBalances": [10_000_000_000, 0], "postBalances": [10_000_000_000 + int(sol_delta * 1e9), 0],
                     "preTokenBalances": pre, "postTokenBalances": post}}


def test_parse_buy_sell_and_wrapped_sol():
    b = parse_swap(tx(-0.5, None, 1000), W, sol_usd=120)
    assert b["side"] == "buy" and b["tokens"] == 1000 and b["sol"] == pytest.approx(0.5) and b["usd"] == pytest.approx(60)
    u = parse_swap(tx(-0.0004, None, 1000, usdc=(20.0, 6.0)), W)   # paid $14 in USDC; SOL change is just the fee
    assert u["side"] == "buy" and u["usd"] == pytest.approx(14.0)
    s = parse_swap(tx(0.8, 1000, 0), W)
    assert s["side"] == "sell" and s["sol"] == pytest.approx(0.8)
    w = parse_swap(tx(0, None, 500, wsol=(1.0, 0.7)), W)          # paid with wrapped SOL
    assert w["side"] == "buy" and w["sol"] == pytest.approx(0.3)
    assert parse_swap(tx(-0.5, None, 1000, err={"x": 1}), W) is None
    assert parse_swap(tx(-0.5, None, 1000), "someone-else") is None


def test_realized_pnl_uses_cost_from_before_the_window():
    swaps = [{"t": 10, "mint": "A", "side": "buy", "tokens": 100, "usd": 1.0},
             {"t": 50, "mint": "A", "side": "sell", "tokens": 50, "usd": 1.0},     # +0.5 inside window
             {"t": 60, "mint": "B", "side": "buy", "tokens": 10, "usd": 1.0},
             {"t": 70, "mint": "B", "side": "sell", "tokens": 10, "usd": 0.2}]     # -0.8
    r = realized_pnl(swaps, 40, 100)
    assert r["usd"] == pytest.approx(-0.3) and r["tokens"] == 2 and r["wins"] == 1


class FakePrices:
    def __init__(self, path): self.path = path          # {mint: [(t, px), ...]}
    def at(self, mint, t, within=600):
        pts = [p for tt, p in self.path.get(mint, []) if tt >= t and tt - t <= within]
        return pts[0] if pts else None


def test_simulate_copies_exits_on_wallet_sell_and_counts_rugs_as_zero():
    sig = lambda t, side, mint, tokens: {"t": t, "side": side, "mint": mint, "tokens": tokens, "sol": 1, "wallet": "w"}
    prices = FakePrices({"UP": [(100, 1.0), (1000, 2.0)], "RUG": [(200, 1.0)]})
    r = simulate([sig(85, "buy", "UP", 10), sig(985, "sell", "UP", 10), sig(185, "buy", "RUG", 10)],
                 prices, bankroll=100, delay=15, slip=0.0, tx_fee=0.0, end=100000)
    by = {c["mint"]: c for c in r["closed"]}
    assert by["UP"]["pnl"] == pytest.approx(5.0)                 # $5 doubled
    assert by["RUG"]["why"].startswith("no price") and by["RUG"]["pnl"] == pytest.approx(-5.0)
    assert r["trades"] == 2 and r["rugs"] == 1 and r["pnl"] == pytest.approx(0.0)


def test_simulate_daily_stop():
    sig = lambda t, mint: {"t": t, "side": "buy", "mint": mint, "tokens": 1, "sol": 1, "wallet": "w"}
    prices = FakePrices({f"M{i}": [(i * 100 + 15, 1.0)] for i in range(10)})   # every coin rugs
    sigs = [sig(i * 100, f"M{i}") for i in range(10)]
    r = simulate(sigs, prices, bankroll=100, stake_pct=0.06, delay=15, slip=0, tx_fee=0, max_hold_h=0.01, end=5000)
    assert r["reasons"].get("daily stop", 0) > 0
