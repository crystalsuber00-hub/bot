import json

import pytest

from polyscan import metrics as m
from polyscan import report
from polyscan.__main__ import write_csv
from polyscan.watch import Watcher, pick_watchlist

DAY = 86400
NOW = 1_790_000_000


def curve(daily):
    """Cumulative series from a list of daily changes, ending at NOW."""
    out, p = [], 0.0
    n = len(daily)
    for i, d in enumerate(daily):
        p += d
        out.append({"t": NOW - (n - 1 - i) * DAY, "p": p})
    return out


def test_series_window_pnl_ignores_history_before_window():
    s = curve([1_000_000] + [0] * 99 + [100] * 90)   # big win 189 days ago, then +100/day
    r = m.series_metrics(s, NOW, 90)
    assert r["pnl"] == pytest.approx(9000)
    assert r["pnl_30d"] == pytest.approx(3000)
    assert r["pnl_all"] == pytest.approx(1_009_000)
    assert r["green_day_rate"] == 1.0 and r["max_drawdown"] == 0


def test_series_new_wallet_counts_from_zero_and_flags_one_day():
    s = curve([5000, 0, 0, -1000, 200])
    r = m.series_metrics(s, NOW, 90)
    assert r["pnl"] == pytest.approx(4200)
    assert r["max_drawdown"] == pytest.approx(1000)
    assert r["best_day_share"] == pytest.approx(5000 / 5200)
    assert r["age_days"] < 90


def test_unredeemed_losers_are_counted():
    closed = [{"realizedPnl": 50, "avgPrice": 0.5, "totalBought": 100, "eventSlug": "nfl-a-b-2026-09-01", "title": "A vs. B",
               "outcome": "A", "timestamp": NOW - DAY}]
    open_ = [
        {"curPrice": 0, "redeemable": True, "cashPnl": -60, "realizedPnl": 0, "initialValue": 60, "avgPrice": 0.6,
         "endDate": "2026-09-10", "title": "Will Bitcoin hit 200k?", "slug": "btc-200k", "outcome": "Yes"},
        {"curPrice": 0.4, "redeemable": False, "cashPnl": 5, "currentValue": 40, "avgPrice": 0.35, "title": "Live market",
         "outcome": "Yes"},
    ]
    res = m.resolved_positions(closed, open_, NOW - 90 * DAY)
    assert len(res) == 2
    p = m.position_metrics(res, open_)
    assert p["win_rate"] == 0.5
    assert p["win_rate_usd"] == pytest.approx(50 / 110)
    assert p["realized"] == pytest.approx(-10)
    assert p["unredeemed_losses"] == 1
    assert p["open_count"] == 1 and p["open_value"] == 40
    assert p["cat_n"] == {"Sports": 1, "Crypto": 1}


@pytest.mark.parametrize("slug,title,cat", [
    ("epl-liv-ful-2026-09-12-liv", "Will Liverpool FC win on 2026-09-12?", "Sports"),
    ("chi-zhe-wsz-2026-08-08-more-ma", "Spread: Zhejiang Zhiye FC (-2.5)", "Sports"),
    ("btc-updown-15m", "Bitcoin Up or Down - Sept 30", "Crypto"),
    ("fed-decision-october", "Fed rate cut in October?", "Economy"),
    ("presidential-election-winner-2028", "Presidential Election Winner 2028", "Politics"),
    ("", "Will it rain in London tomorrow?", "Other"),
])
def test_classify(slug, title, cat):
    assert m.classify(slug, title) == cat


def test_trade_metrics_and_bot_flag():
    trades = [{"timestamp": NOW - i * 60, "side": "BUY", "price": 0.95, "usdcSize": 100, "conditionId": str(i % 70)}
              for i in range(400)]
    t = m.trade_metrics(trades, capped=False, now=NOW)
    assert t["trades_per_day"] == 400  # sample spans < 1 day
    assert t["share_favorites"] == 1.0 and t["markets"] == 70 and t["days_since_trade"] == 0
    s = m.series_metrics(curve([100] * 120), NOW, 90)
    p = m.position_metrics([{"pnl": 1, "cost": 10, "price": 0.95, "slug": "", "title": ""}] * 30, [])
    f = m.flags(s, p, t)
    assert "bot-speed" in f and "favorite-scalper" in f and "dormant" not in f
    sc = m.copy_score(s, p, t, 1.0)
    assert 0 <= sc["score"] <= 100 and sc["parts"]["followable"] < 0.5


def test_whale_with_many_fills_is_not_a_bot_and_quiet_wallet_is_dormant():
    # one market, 600 fills in a day: a big order split across makers, last fill 20 days ago
    trades = [{"timestamp": NOW - 20 * DAY - i * 100, "side": "BUY", "price": 0.55, "usdcSize": 5000, "conditionId": "c1"}
              for i in range(600)]
    t = m.trade_metrics(trades, capped=False, now=NOW)
    assert t["trades_per_day"] > 150 and t["markets_per_day"] < 2
    assert not m.is_bot(t) and m.dormant(t)
    s = m.series_metrics(curve([100] * 120), NOW, 90)
    p = m.position_metrics([{"pnl": 1, "cost": 10, "price": 0.5, "slug": "", "title": ""}] * 30, [])
    assert m.copy_score(s, p, t, 1.0)["parts"]["active"] == 0.0


def test_capped_closed_sample_limits_unredeemed_window():
    closed = [{"realizedPnl": 10, "avgPrice": 0.5, "totalBought": 20, "timestamp": NOW - DAY, "title": "", "slug": ""}]
    old_loser = {"curPrice": 0, "redeemable": True, "cashPnl": -100, "initialValue": 100, "avgPrice": 0.5,
                 "endDate": "2026-08-01", "title": ""}
    since = NOW - 90 * DAY
    assert len(m.resolved_positions(closed, [old_loser], since)) == 2
    assert len(m.resolved_positions(closed, [old_loser], since, closed_capped=True)) == 1


def test_display_name():
    w = "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563"
    assert m.display_name("0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465", w) == "0x2c33…0563"
    assert m.display_name("RN1", w) == "RN1"


def fake_scan():
    profiles = []
    for i, (pnl, tpd) in enumerate([(5e6, 500), (2e6, 10), (1e6, 5)]):
        s = m.series_metrics(curve([pnl / 90] * 90), NOW, 90)
        q = m.position_metrics([{"pnl": 10, "cost": 100, "price": 0.5, "slug": "nba-a-b-2026-09-01", "title": "A vs. B",
                                 "outcome": "A"}] * 25 + [{"pnl": -5, "cost": 50, "price": 0.5, "slug": "", "title": "x",
                                                          "outcome": "No"}] * 10, [])
        t = m.trade_metrics([{"timestamp": NOW - k * DAY / tpd, "side": "BUY", "price": 0.5, "usdcSize": 50,
                              "conditionId": str(k)} for k in range(tpd * 2)], False, now=NOW)
        p = {"wallet": f"0x{i:040x}", "name": f"w{i}", "x": "", "series": s, "positions": q, "trading": t,
             "flags": m.flags(s, q, t), "style": m.style(q, t), "rank": i + 1}
        p.update(m.copy_score(s, q, t, (3 - i) / 3))
        profiles.append(p)
    lb = [{"wallet": p["wallet"], "name": p["name"], "x": "", **{k: p["series"][k] for k in
          ("pnl", "pnl_30d", "pnl_7d", "pnl_all", "max_drawdown", "sharpe", "green_week_rate", "best_day_share", "age_days")}}
          for p in profiles]
    return {"generated": NOW, "days": 90, "elapsed_s": 1, "pool_size": 10, "ranked_size": 3, "leaderboard": lb,
            "profiles": profiles}


def test_report_and_csv_render(tmp_path):
    res = json.loads(json.dumps(fake_scan()))   # same shape as a scan.json round-trip
    html = report.render(res)
    assert "<title>Polymarket 90-Day Leaders</title>" in html and "__DATA__" not in html
    write_csv(res, tmp_path / "w.csv")
    assert (tmp_path / "w.csv").read_text().count("\n") == 4


def test_watchlist_skips_bots():
    wl = pick_watchlist(fake_scan(), top=5, min_score=0)
    assert [w["name"] for w in wl] == ["w1", "w2"] or "w0" not in [w["name"] for w in wl]


class FakeApi:
    def __init__(self):
        self.rows = {}

    def recent_activity(self, wallet, limit):
        return self.rows.get(wallet, [])


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text, payload=None):
        self.sent.append(payload)


def trade(ts, asset, usd, side="BUY", price=0.4):
    return {"type": "TRADE", "timestamp": ts, "asset": asset, "side": side, "usdcSize": usd, "size": usd / price,
            "title": "Will X happen?", "outcome": "Yes", "eventSlug": "will-x"}


def test_watcher_aggregates_fills_and_detects_consensus(tmp_path):
    import time
    now = int(time.time())
    api, n = FakeApi(), FakeNotifier()
    wl = [{"wallet": "a", "name": "Alice", "score": 80, "pnl": 1e6, "style": "s"},
          {"wallet": "b", "name": "Bob", "score": 70, "pnl": 5e5, "style": "s"}]
    w = Watcher(api, n, wl, str(tmp_path / "st.json"), min_usd=1000)
    api.rows = {"a": [trade(now - 100, "T1", 10)], "b": [trade(now - 100, "T9", 10)]}
    assert w.tick() == []                        # first sight seeds state, no history replay
    api.rows["a"] = [trade(now - 10, "T1", 600), trade(now - 9, "T1", 600), trade(now - 8, "T2", 50)] + api.rows["a"]
    out = w.tick()
    assert len(out) == 1 and out[0]["usd"] == 1200 and out[0]["price"] == pytest.approx(0.4)
    api.rows["b"] = [trade(now - 5, "T1", 3000)] + api.rows["b"]
    out = w.tick()
    assert [a["event"] for a in out] == ["entry", "entry"]
    assert out[1]["wallets"] == ["Alice", "Bob"]
    assert w.tick() == []                        # consensus is not repeated
    assert len(n.sent) == 3
    assert json.loads((tmp_path / "st.json").read_text())["seen"]["a"] == now - 8
