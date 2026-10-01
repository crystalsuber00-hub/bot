"""Pure functions that turn raw API rows into wallet statistics. No network here."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from statistics import median

DAY = 86400

SPORTS_PREFIX = re.compile(
    r"^(nfl|nba|wnba|mlb|nhl|cfb|cbb|ncaab|ncaaf|epl|lal|sea|bun|fl1|ser|ucl|uel|uecl|mls|liga|bra|arg|mex|por|ere|"
    r"tur|sco|bel|aus|jpn|kor|chn|efl|fac|elc|atp|wta|ufc|mma|box|pga|lpga|f1|nascar|ipl|cric|crint|csgo|cs2|lol|"
    r"dota2|val|rl|ow|cod|wc|euro|copa|afc|caf|conc|ita|esp|ger|fra|ned|kbo|npb|khl|shl|ahl|ten|golf|snk|dar|wwe)-")
# league-team-team-date, e.g. chi-zhe-wsz-2026-08-08, es2-bur-rso-2026-08-31
SPORTS_SLUG = re.compile(r"^[a-z0-9]{2,6}-[a-z0-9]{2,5}-[a-z0-9]{2,5}-\d{4}-\d{2}-\d{2}")
SPORTS_WORDS = re.compile(
    r"\b(vs\.?|exact score|fc|both teams to score|win on \d{4}|super bowl|nba finals|world series|stanley cup|champions league|premier league|"
    r"grand slam|open:|grand prix|ufc|o/u|spread:|over/under|playoffs|mvp|heisman|world cup|wimbledon|"
    r"tournament|masters)\b", re.I)
CRYPTO_WORDS = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|doge|dogecoin|crypto|bnb|hyperliquid|memecoin|"
    r"up or down|fdv|airdrop|token|stablecoin|microstrategy)\b", re.I)
POLITICS_WORDS = re.compile(
    r"\b(election|elect|president|presidential|senate|house|congress|governor|mayor|primary|nominee|"
    r"trump|biden|harris|vance|newsom|democrat|republican|gop|parliament|prime minister|chancellor|"
    r"cabinet|impeach|supreme court|minister|poll|tariff|executive order|nato|putin|zelensky|xi jinping|"
    r"ceasefire|war|invade|strike on|sanction)\b", re.I)
ECON_WORDS = re.compile(r"\b(fed|fomc|interest rate|rate cut|cpi|inflation|gdp|recession|unemployment|"
                        r"s&p|nasdaq|dow|stock|ipo|earnings|treasury|yield)\b", re.I)
CULTURE_WORDS = re.compile(r"\b(oscar|grammy|emmy|box office|album|song|spotify|youtube|tiktok|mrbeast|"
                           r"netflix|movie|tweet|elon musk tweet|# of tweets|billboard|taylor swift)\b", re.I)


def classify(slug: str = "", title: str = "") -> str:
    slug, title = (slug or "").lower(), title or ""
    if SPORTS_PREFIX.match(slug) or SPORTS_SLUG.match(slug) or SPORTS_WORDS.search(title):
        return "Sports"
    if CRYPTO_WORDS.search(title) or CRYPTO_WORDS.search(slug.replace("-", " ")):
        return "Crypto"
    if ECON_WORDS.search(title):
        return "Economy"
    if POLITICS_WORDS.search(title):
        return "Politics"
    if CULTURE_WORDS.search(title):
        return "Culture"
    return "Other"


def display_name(name: str, wallet: str) -> str:
    """Wallets without a username get '0xABC...-<ms>' on the leaderboard; show a short address instead."""
    if not name or re.fullmatch(r"0x[0-9a-fA-F]{40}(-\d+)?", name):
        return f"{wallet[:6]}…{wallet[-4:]}"
    return name


# -- P&L curve ------------------------------------------------------------------

def pnl_at(series: list[dict], t: float) -> float:
    """Cumulative P&L as of time t (last point at or before t; 0 before the wallet existed)."""
    v = 0.0
    for pt in series:
        if pt["t"] > t:
            break
        v = pt["p"]
    return float(v)


def series_metrics(series: list[dict], now: float, days: int) -> dict:
    series = sorted(series, key=lambda x: x["t"])
    start = now - days * DAY
    base = pnl_at(series, start)
    window = [(start, base)] + [(pt["t"], float(pt["p"])) for pt in series if start < pt["t"] <= now]
    end = window[-1][1]
    diffs = [b[1] - a[1] for a, b in zip(window, window[1:])]
    active = [d for d in diffs if abs(d) > 1e-6]
    gains = [d for d in diffs if d > 0]

    peak, max_dd = window[0][1], 0.0
    for _, p in window:
        peak = max(peak, p)
        max_dd = max(max_dd, peak - p)

    weeks = []
    for w in range(math.ceil(days / 7)):
        a, b = start + w * 7 * DAY, min(start + (w + 1) * 7 * DAY, now)
        weeks.append(pnl_at(series, b) - pnl_at(series, a))
    traded_weeks = [w for w in weeks if abs(w) > 1e-6]

    sharpe = 0.0
    if len(diffs) > 2:
        mu = sum(diffs) / len(diffs)
        sd = math.sqrt(sum((d - mu) ** 2 for d in diffs) / (len(diffs) - 1))
        sharpe = mu / sd * math.sqrt(365) if sd > 0 else 0.0

    first_t = series[0]["t"] if series else now
    return {
        "pnl": end - base,
        "pnl_30d": end - pnl_at(series, now - 30 * DAY),
        "pnl_7d": end - pnl_at(series, now - 7 * DAY),
        "pnl_all": end,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "active_days": len(active),
        "green_day_rate": (sum(d > 0 for d in active) / len(active)) if active else 0.0,
        "green_week_rate": (sum(w > 0 for w in traded_weeks) / len(traded_weeks)) if traded_weeks else 0.0,
        "best_day": max(diffs, default=0.0),
        "worst_day": min(diffs, default=0.0),
        "best_day_share": (max(gains) / sum(gains)) if gains else 0.0,
        "weekly": weeks,
        "age_days": (now - first_t) / DAY,
        "curve": [[int(t), round(p - base, 2)] for t, p in window],
    }


# -- positions -------------------------------------------------------------------

def _end_ts(p: dict) -> float | None:
    d = p.get("endDate")
    if not d:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromisoformat(d[:10]).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def resolved_positions(closed: list[dict], open_: list[dict], since: float, closed_capped: bool = False) -> list[dict]:
    """Closed positions plus resolved-but-unredeemed ones still sitting in /positions.

    Losers are rarely redeemed (there's nothing to collect), so counting only /closed-positions
    makes almost everyone look like a 90% winner. Those dead tokens show up in /positions at
    curPrice 0 and are folded back in here. When the closed list was cut off by the page cap it
    only covers the last few days, so unredeemed tokens are limited to that same span.
    """
    if closed_capped and closed:
        since = max(since, min(p.get("timestamp", since) for p in closed))
    out = [{"pnl": float(p.get("realizedPnl", 0)), "cost": float(p.get("avgPrice", 0)) * float(p.get("totalBought", 0)),
            "price": float(p.get("avgPrice", 0)), "slug": p.get("eventSlug") or p.get("slug", ""),
            "title": p.get("title", ""), "outcome": p.get("outcome", ""), "t": p.get("timestamp")} for p in closed]
    for p in open_:
        cur = float(p.get("curPrice", 0.5))
        end = _end_ts(p)
        if not (p.get("redeemable") or cur in (0.0, 1.0)) or (end is not None and end < since):
            continue
        out.append({"pnl": float(p.get("cashPnl", 0)) + float(p.get("realizedPnl", 0)),
                    "cost": float(p.get("initialValue", 0)), "price": float(p.get("avgPrice", 0)),
                    "slug": p.get("eventSlug") or p.get("slug", ""), "title": p.get("title", ""),
                    "outcome": p.get("outcome", ""), "t": end, "unredeemed": True})
    return out


def position_metrics(resolved: list[dict], open_: list[dict]) -> dict:
    wins = [r["pnl"] for r in resolved if r["pnl"] > 0]
    losses = [r["pnl"] for r in resolved if r["pnl"] < 0]
    cost = sum(r["cost"] for r in resolved)
    by_cat_pnl, by_cat_n = defaultdict(float), Counter()
    for r in resolved:
        c = classify(r["slug"], r["title"])
        by_cat_pnl[c] += r["pnl"]
        by_cat_n[c] += 1
    top = sorted(resolved, key=lambda r: r["pnl"], reverse=True)
    live = [p for p in open_ if not p.get("redeemable") and 0 < float(p.get("curPrice", 0)) < 1]
    return {
        "resolved": len(resolved),
        "win_rate": len(wins) / (len(wins) + len(losses)) if wins or losses else 0.0,
        # share of money staked on positions that won: held to resolution at one price p, a wallet
        # profits exactly when this beats p, so this is the number to hold against avg_entry
        "win_rate_usd": (sum(r["cost"] for r in resolved if r["pnl"] > 0) / cost) if cost > 0 else 0.0,
        "profit_factor": (sum(wins) / -sum(losses)) if losses else (float("inf") if wins else 0.0),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "realized": sum(wins) + sum(losses),
        "roi": (sum(wins) + sum(losses)) / cost if cost > 0 else 0.0,
        "avg_entry": (sum(r["price"] * r["cost"] for r in resolved) / cost) if cost > 0 else 0.0,
        "top_win_share": (top[0]["pnl"] / sum(wins)) if wins else 0.0,
        "unredeemed_losses": sum(1 for r in resolved if r.get("unredeemed") and r["pnl"] < 0),
        "cat_pnl": dict(by_cat_pnl),
        "cat_n": dict(by_cat_n),
        "top_positions": [{k: r.get(k, "") for k in ("title", "outcome", "pnl", "price", "slug")} for r in top[:5]],
        "worst_positions": [{k: r.get(k, "") for k in ("title", "outcome", "pnl", "price", "slug")} for r in top[::-1][:3]
                            if r["pnl"] < 0],
        "open_count": len(live),
        "open_value": sum(float(p.get("currentValue", 0)) for p in live),
        "open_positions": sorted(
            [{"title": p.get("title", ""), "outcome": p.get("outcome", ""), "value": float(p.get("currentValue", 0)),
              "avg": float(p.get("avgPrice", 0)), "cur": float(p.get("curPrice", 0)), "slug": p.get("eventSlug", "")}
             for p in live], key=lambda x: -x["value"])[:5],
    }


# -- trading behaviour -----------------------------------------------------------

def trade_metrics(trades: list[dict], capped: bool, now: float | None = None) -> dict:
    buys = [t for t in trades if t.get("side") == "BUY"]
    usd = [float(t.get("usdcSize", 0)) for t in trades]
    buy_usd = sum(float(t.get("usdcSize", 0)) for t in buys)
    ts = [t["timestamp"] for t in trades]
    span = max((max(ts) - min(ts)) / DAY, 1.0) if ts else 1.0

    def share(cond):
        return sum(float(t.get("usdcSize", 0)) for t in buys if cond(float(t.get("price", 0)))) / buy_usd if buy_usd else 0.0

    hours = Counter(int(t["timestamp"] // 3600 % 24) for t in trades)
    markets = len({t.get("conditionId") for t in trades})
    return {
        "trades": len(trades),
        "capped": capped,
        "sample_days": span,
        "trades_per_day": len(trades) / span,
        "volume_per_day": sum(usd) / span,
        "median_trade": median(usd) if usd else 0.0,
        "buy_price": (sum(float(t["price"]) * float(t.get("usdcSize", 0)) for t in buys) / buy_usd) if buy_usd else 0.0,
        "sell_ratio": (len(trades) - len(buys)) / len(trades) if trades else 0.0,
        "share_favorites": share(lambda p: p >= 0.90),
        "share_longshots": share(lambda p: p <= 0.15),
        "markets": markets,
        # fills/day overstates activity (one big order fills against dozens of makers);
        # distinct markets per active day is what separates a person from a machine
        "markets_per_day": markets / span,
        "days_since_trade": ((now - max(ts)) / DAY) if ts and now else None,
        "busiest_hour_utc": hours.most_common(1)[0][0] if hours else None,
    }


# -- scoring -----------------------------------------------------------------------

def flags(s: dict, p: dict, tr: dict) -> list[str]:
    out = []
    if s["best_day_share"] > 0.5:
        out.append("one-big-day")
    if p["resolved"] and p["top_win_share"] > 0.5:
        out.append("one-big-bet")
    if is_bot(tr):
        out.append("bot-speed")
    if dormant(tr):
        out.append("dormant")
    if tr["share_favorites"] > 0.6:
        out.append("favorite-scalper")
    if tr["share_longshots"] > 0.4:
        out.append("longshot-hunter")
    if s["age_days"] < 90:
        out.append("new-wallet")
    if s["pnl"] > 0 and s["max_drawdown"] > 0.5 * s["pnl"]:
        out.append("deep-drawdown")
    if p["resolved"] < 15:
        out.append("small-sample")
    if s["pnl_30d"] < 0 < s["pnl"]:
        out.append("cooling-off")
    return out


def style(p: dict, tr: dict) -> str:
    n = p["cat_n"]
    total = sum(n.values())
    lead = max(n, key=n.get) if n else "Other"
    focus = f"{lead} specialist" if total and n[lead] / total >= 0.6 else "Generalist"
    if is_bot(tr):
        return f"{focus} · high-frequency"
    if tr["share_favorites"] > 0.6:
        return f"{focus} · favorites"
    if tr["share_longshots"] > 0.4:
        return f"{focus} · longshots"
    return focus


def is_bot(tr: dict) -> bool:
    return tr.get("markets_per_day", 0) > 25


def dormant(tr: dict, days: float = 14) -> bool:
    d = tr.get("days_since_trade")
    return d is None or d > days


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def copy_score(s: dict, p: dict, tr: dict, profit_pct: float) -> dict:
    """0-100: how much this wallet looks like repeatable, followable skill rather than luck or a machine.

    profit_pct is the wallet's 90-day P&L percentile within the analysed cohort (0-1).
    """
    parts = {
        "profit": profit_pct,
        "consistency": 0.5 * _clip(s["green_week_rate"]) + 0.5 * _clip(s["sharpe"] / 6),
        "breadth": _clip(1 - s["best_day_share"]) * _clip(p["resolved"] / 40),
        "risk": 1 - _clip(s["max_drawdown"] / max(s["pnl"], 1)) if s["pnl"] > 0 else 0.0,
        "edge": _clip(p["roi"] / 0.25) if p["roi"] > 0 else 0.0,
        # can a human (or a minute-level copier) realistically follow it?
        "followable": 1 - 0.7 * _clip((tr.get("markets_per_day", 0) - 10) / 40)
                      - 0.3 * _clip((tr["share_favorites"] - 0.5) / 0.4),
    }
    w = {"profit": 0.2, "consistency": 0.2, "breadth": 0.15, "risk": 0.15, "edge": 0.15, "followable": 0.15}
    base = 100 * sum(parts[k] * w[k] for k in w)
    # a wallet that stopped trading can't be followed, however good its record
    active = not dormant(tr)
    return {"score": round(base * (1.0 if active else 0.6), 1),
            "parts": {**{k: round(v, 3) for k, v in parts.items()}, "active": 1.0 if active else 0.0}}
