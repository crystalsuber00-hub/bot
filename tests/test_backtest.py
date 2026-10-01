import pytest

from polyscan.backtest import HistoryUS, signals, simulate

START = 1_790_000_000  # a simulated game starts here


class FakeData:
    def __init__(self, rows): self.rows = rows
    def trades(self, wallet, since, cap, end=None):
        return [r for r in self.rows if r["wallet"] == wallet and since <= r["timestamp"] <= end]


def fill(t, usd, side="BUY", asset="CHIEFS", px=0.5, wallet="w1", outcome="Chiefs"):
    return {"wallet": wallet, "timestamp": t, "side": side, "asset": asset, "usdcSize": usd, "size": usd / px,
            "outcome": outcome, "eventSlug": "nfl-kc-lv-2026-10-04", "slug": "nfl-kc-lv-2026-10-04", "title": "Chiefs vs. Raiders"}


def test_signals_add_up_fills_per_minute_and_drop_small_ones():
    rows = [fill(START - 3600, 600), fill(START - 3590, 600), fill(START - 1800, 300), fill(START - 600, 2000, side="SELL")]
    out = signals(FakeData(rows), [{"wallet": "w1", "name": "W"}], START - 7200, START, 1000)
    assert [(a["side"], round(a["usd"])) for a in out] == [("BUY", 1200), ("SELL", 2000)]
    assert out[0]["price"] == pytest.approx(0.5) and out[0]["name"] == "W"


class FakeUSHist:
    """Public Polymarket US data for one game: Chiefs (first side) priced 0.50 ask / 0.48 bid, Chiefs won."""
    def __init__(self, settle=1.0): self.settle_value = settle
    def event(self, slug):
        return {"slug": slug, "startDate": "2026-09-21T14:13:20Z", "markets": [
            {"slug": f"aec-{slug}", "sportsMarketType": "football_team_full_game_winner", "orderPriceMinTickSize": 0.01,
             "minimumTradeQty": 0.01, "marketSides": [
                 {"description": "Chiefs", "long": True, "team": {"name": "Kansas City Chiefs"}},
                 {"description": "Raiders", "long": False, "team": {"name": "Las Vegas Raiders"}}]}]}
    def public(self, path, **p):
        a = p["timestamp.startTimestamp"]
        return {"history": [{"timestamp": a + 60 * i, "longPrice": 0.50, "shortPrice": 0.52} for i in range(16)]}
    def settlement(self, m): return self.settle_value


def test_history_prices_and_settlement_timing():
    h = HistoryUS(FakeUSHist())
    h.event("nfl-kc-lv-2026-10-04")
    m = "aec-nfl-kc-lv-2026-10-04"
    h.now = START - 3600
    b = h.bbo(m)
    assert b["longQuote"]["value"] == 0.50 and b["bestBid"]["value"] == pytest.approx(0.48)
    assert h.settlement(m) is None                       # game not over yet
    h.now = START + 4 * 3600 + 1
    assert h.bbo(m)["state"] == "MARKET_STATE_EXPIRED" and h.settlement(m) == 1.0


def test_replay_buys_first_side_and_scores_win_and_loss():
    alerts = signals(FakeData([fill(START - 3600, 5000, px=0.49), fill(START - 3000, 5000, asset="RAIDERS", outcome="Raiders", px=0.52)]),
                     [{"wallet": "w1", "name": "W"}], START - 7200, START, 1000)
    h = HistoryUS(FakeUSHist(settle=1.0))
    r = simulate(alerts, h, 100, {}, settle_until=START + 10 * 3600)
    assert r["reasons"] == {"copied": 1, "second side (manual)": 1}
    assert r["trades"] == 1 and r["wins"] == 1 and r["pnl"] > 0
    r2 = simulate(alerts, HistoryUS(FakeUSHist(settle=1.0)), 100, {"paper_short": True}, settle_until=START + 10 * 3600)
    assert r2["trades"] == 2 and r2["wins"] == 1          # Raiders (second side) lost when the Chiefs won
    assert r2["pnl"] < r["pnl"]
