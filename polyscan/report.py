"""Render a saved scan as one self-contained HTML dashboard."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from statistics import median

CATS = ["Sports", "Politics", "Crypto", "Economy", "Culture", "Other"]


def _num(x):
    return None if x is None or (isinstance(x, float) and (math.isinf(x) or math.isnan(x))) else x


def slim(p: dict) -> dict:
    s, q, t = p["series"], p["positions"], p["trading"]
    return {
        "rank": p["rank"], "name": p["name"], "wallet": p["wallet"], "x": p.get("x", ""), "score": p["score"],
        "parts": p["parts"], "style": p["style"], "flags": p["flags"],
        "pnl": round(s["pnl"]), "pnl30": round(s["pnl_30d"]), "pnl7": round(s["pnl_7d"]), "pnlAll": round(s["pnl_all"]),
        "dd": round(s["max_drawdown"]), "sharpe": round(s["sharpe"], 2), "gw": round(s["green_week_rate"], 3),
        "bestDay": round(s["best_day"]), "bestShare": round(s["best_day_share"], 3), "age": round(s["age_days"]),
        "weekly": [round(w) for w in s["weekly"]], "curve": [c[1] for c in s["curve"]],
        "resolved": q["resolved"], "win": round(q["win_rate"], 3), "winUsd": round(q.get("win_rate_usd", q["win_rate"]), 3), "pf": _num(round(q["profit_factor"], 2)) if q["profit_factor"] != float("inf") else None,
        "roi": round(q["roi"], 4), "entry": round(q["avg_entry"], 3), "avgWin": round(q["avg_win"]), "avgLoss": round(q["avg_loss"]),
        "catPnl": {k: round(v) for k, v in q["cat_pnl"].items()}, "catN": q["cat_n"],
        "top": q["top_positions"], "worst": q["worst_positions"], "open": q["open_positions"],
        "openN": q["open_count"], "openVal": round(q["open_value"]),
        "tpd": round(t["trades_per_day"], 1), "mpd": round(t.get("markets_per_day", 0), 1),
        "idle": None if t.get("days_since_trade") is None else round(t["days_since_trade"], 1), "medTrade": round(t["median_trade"], 2), "capped": t["capped"],
        "fav": round(t["share_favorites"], 3), "long": round(t["share_longshots"], 3), "markets": t["markets"],
        "sellRatio": round(t["sell_ratio"], 3),
    }


def findings(res: dict) -> dict:
    P = res["profiles"]
    if not P:
        return {}
    tot = sum(p["series"]["pnl"] for p in P)
    top10 = sum(p["series"]["pnl"] for p in sorted(P, key=lambda p: -p["series"]["pnl"])[:10])
    bots = [p for p in P if "bot-speed" in p["flags"]]
    cat = {c: 0.0 for c in CATS}
    for p in P:
        for c, v in p["positions"]["cat_pnl"].items():
            cat[c] = cat.get(c, 0.0) + v
    sized = [p for p in P if p["positions"]["resolved"] >= 20]
    wr = lambda p: p["positions"].get("win_rate_usd", p["positions"]["win_rate"])
    edge = [p for p in sized if wr(p) > p["positions"]["avg_entry"]]
    margin = median(wr(p) - p["positions"]["avg_entry"] for p in sized) if sized else 0
    lb = res["leaderboard"]
    return {
        "total": tot, "top10_share": top10 / tot if tot else 0,
        "bots": len(bots), "bot_share": sum(p["series"]["pnl"] for p in bots) / tot if tot else 0,
        "cat_pnl": cat,
        "median_win": median(wr(p) for p in sized) if sized else 0,
        "median_entry": median(p["positions"]["avg_entry"] for p in sized) if sized else 0,
        "sized": len(sized), "edge": len(edge), "margin": margin,
        "one_day": sum("one-big-day" in p["flags"] for p in P),
        "cooling": sum("cooling-off" in p["flags"] for p in P),
        "new": sum("new-wallet" in p["flags"] for p in P),
        "cutoff": P[-1]["series"]["pnl"],
        "winners_in_pool": sum(1 for r in lb if r["pnl"] > 0),
        "faded": res.get("faded", 0), "losing": res.get("losing", 0),
        "dormant": sum("dormant" in p["flags"] for p in P),
    }


def render(res: dict) -> str:
    data = {
        "generated": res["generated"], "days": res["days"], "pool": res["pool_size"], "ranked": res["ranked_size"],
        "profiles": [slim(p) for p in res["profiles"]],
        "findings": findings(res),
        "asof": datetime.fromtimestamp(res["generated"], timezone.utc).strftime("%-d %b %Y, %H:%M UTC"),
    }
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", blob).replace("__DAYS__", str(res["days"]))


TEMPLATE = r"""<title>Polymarket 90-Day Leaders</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
/* Layout: a trading-desk ledger. Summary strip, three analytical panels, then the full book of wallets with drill-down rows. */
:root {
  --bg: #f3f4f6; --panel: #ffffff; --ink: #161a22; --ink-2: #4b5260; --ink-3: #7b8291; --rule: #dde0e6; --rule-2: #eceef2;
  --accent: #1f5fbf; --accent-soft: #e3ecf9; --gold: #a86400; --gold-soft: #fbf0dc;
  --up: #12805c; --down: #c23b3b; --warn: #a86400; --up-soft: #e2f3ec; --down-soft: #fbe7e7;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --s5: #e87ba4; --s6: #8a8f99;
  --display: "IBM Plex Sans Condensed", "Arial Narrow", system-ui, sans-serif;
  --body: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0f1218; --panel: #171b23; --ink: #eef0f4; --ink-2: #b4bac6; --ink-3: #848b99; --rule: #2a303b; --rule-2: #20252e;
  --accent: #6aa3f0; --accent-soft: #1a2a42; --gold: #e0a640; --gold-soft: #33291a;
  --up: #3fbf8f; --down: #ef6f6f; --warn: #e0a640; --up-soft: #15302a; --down-soft: #3a1d1f;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181; --s6: #6f7581; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #0f1218; --panel: #171b23; --ink: #eef0f4; --ink-2: #b4bac6; --ink-3: #848b99; --rule: #2a303b; --rule-2: #20252e;
  --accent: #6aa3f0; --accent-soft: #1a2a42; --gold: #e0a640; --gold-soft: #33291a;
  --up: #3fbf8f; --down: #ef6f6f; --warn: #e0a640; --up-soft: #15302a; --down-soft: #3a1d1f;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181; --s6: #6f7581; color-scheme: dark; }
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 var(--body); margin: 0; }
.wrap { max-width: 1240px; margin: 0 auto; padding-inline: 20px; padding-block: 28px 64px; display: grid; gap: 22px; }
h1, h2, h3 { font-family: var(--display); text-wrap: balance; margin: 0; letter-spacing: -0.01em; }
h1 { font-size: clamp(30px, 4.4vw, 46px); font-weight: 700; line-height: 1.05; }
h2 { font-size: 21px; font-weight: 600; }
h3 { font-size: 15px; font-weight: 600; }
.eyebrow { font: 500 11.5px/1 var(--mono); text-transform: uppercase; letter-spacing: 0.09em; color: var(--ink-3); }
.mono, .num { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.muted { color: var(--ink-2); }
.up { color: var(--up); } .down { color: var(--down); }
header { display: grid; gap: 10px; }
header .lede { max-width: 72ch; color: var(--ink-2); margin: 0; }
.strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); background: var(--panel); border: 1px solid var(--rule); border-radius: 6px; }
.strip > div { padding: 14px 16px; border-right: 1px solid var(--rule-2); display: grid; gap: 4px; min-width: 0; }
.strip > div:last-child { border-right: 0; }
.strip .v { font: 600 24px/1.1 var(--display); font-variant-numeric: tabular-nums; }
.strip .k { font-size: 12.5px; color: var(--ink-2); }
.panel { background: var(--panel); border: 1px solid var(--rule); border-radius: 6px; padding: 18px; display: grid; gap: 12px; align-content: start; min-width: 0; }
.grid2 { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 22px; }
.grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 22px; }
@media (max-width: 960px) { .grid2, .grid3 { grid-template-columns: minmax(0, 1fr); } }
.findings { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0; }
.finding { padding: 4px 18px 4px 0; display: grid; gap: 4px; align-content: start; }
.finding p { margin: 0; color: var(--ink-2); font-size: 14px; }
.finding b { color: var(--ink); font-weight: 600; }
.panel-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 6px 16px; }
.note { font-size: 12.5px; color: var(--ink-3); margin: 0; }
svg text { fill: var(--ink-2); font: 11px var(--mono); }
.chart { overflow-x: auto; } .chart > svg { min-width: 480px; display: block; }
svg .axis { stroke: var(--rule); } svg .grid { stroke: var(--rule-2); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12.5px; color: var(--ink-2); }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
#tip { position: fixed; pointer-events: none; z-index: 20; background: var(--panel); color: var(--ink); border: 1px solid var(--rule); border-radius: 6px; padding: 8px 10px; font-size: 12.5px; box-shadow: 0 6px 20px rgb(0 0 0 / 0.12); max-width: 280px; }
#tip .t { font-weight: 600; margin-bottom: 2px; }
.controls { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center; }
.controls input[type=search], .controls select { font: 14px var(--body); color: var(--ink); background: var(--bg); border: 1px solid var(--rule); border-radius: 5px; padding: 7px 10px; min-width: 0; }
.controls input[type=search] { width: min(260px, 100%); }
.controls label { font-size: 13.5px; color: var(--ink-2); display: inline-flex; gap: 6px; align-items: center; cursor: pointer; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.tablebox { overflow-x: auto; border: 1px solid var(--rule); border-radius: 6px; background: var(--panel); }
table { border-collapse: collapse; width: 100%; min-width: 1060px; font-size: 13.5px; }
th { font: 500 11px/1.2 var(--mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink-3); text-align: right; padding: 10px 10px; border-bottom: 1px solid var(--rule); background: var(--panel); position: sticky; top: 0; white-space: nowrap; cursor: pointer; user-select: none; }
th.l, td.l { text-align: left; }
th[aria-sort="descending"]::after { content: " ↓"; color: var(--accent); }
th[aria-sort="ascending"]::after { content: " ↑"; color: var(--accent); }
td { padding: 8px 10px; border-bottom: 1px solid var(--rule-2); text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; vertical-align: middle; }
tr.row { cursor: pointer; }
tr.row:hover td { background: var(--accent-soft); }
tr.row[aria-expanded="true"] td { background: var(--accent-soft); border-bottom-color: transparent; }
td.who { font-family: var(--body); max-width: 210px; overflow: hidden; text-overflow: ellipsis; }
td.who b { font-weight: 600; }
td.who small { display: block; color: var(--ink-3); font: 11px var(--mono); }
.score { display: inline-flex; align-items: center; gap: 6px; }
.score .bar { width: 44px; height: 6px; border-radius: 3px; background: var(--rule-2); overflow: hidden; }
.score .bar i { display: block; height: 100%; background: var(--gold); border-radius: 3px; }
.chip { display: inline-block; font: 500 10.5px/1 var(--mono); padding: 4px 6px; border-radius: 3px; margin: 1px 2px 1px 0; background: var(--rule-2); color: var(--ink-2); white-space: nowrap; }
.chip.warn { background: var(--gold-soft); color: var(--warn); }
.chip.bad { background: var(--down-soft); color: var(--down); }
.chip.style { background: var(--accent-soft); color: var(--accent); }
td.flags { text-align: left; white-space: normal; min-width: 170px; }
tr.detail td { padding: 0 10px 16px; text-align: left; font-family: var(--body); white-space: normal; background: var(--accent-soft); }
.dgrid { position: sticky; left: 10px; width: min(1160px, calc(100vw - 92px)); display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; background: var(--panel); border: 1px solid var(--rule); border-radius: 6px; padding: 16px; }
.dgrid > div { min-width: 0; display: grid; gap: 6px; align-content: start; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 3px 12px; font-size: 13px; }
.kv dt { color: var(--ink-3); } .kv dd { margin: 0; text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
.plist { list-style: none; padding: 0; margin: 0; display: grid; gap: 5px; font-size: 13px; }
.plist li { display: flex; justify-content: space-between; gap: 10px; }
.plist li span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.plist li span:last-child { font-family: var(--mono); flex: none; }
a { color: var(--accent); }
.watch { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; }
.wcard { border: 1px solid var(--rule); border-radius: 6px; padding: 12px 14px; display: grid; gap: 4px; background: var(--panel); }
.wcard .n { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
pre.cmd { background: var(--bg); border: 1px solid var(--rule); border-radius: 5px; padding: 10px 12px; overflow-x: auto; font: 12.5px var(--mono); margin: 0; }
.method { columns: 2 320px; column-gap: 32px; font-size: 14px; color: var(--ink-2); }
.method p { margin: 0 0 10px; break-inside: avoid; }
.method b { color: var(--ink); }
@media (prefers-reduced-motion: no-preference) { tr.row td { transition: background .12s; } }
</style>

<div class="wrap">
  <header>
    <span class="eyebrow" id="asof"></span>
    <h1>Polymarket 90-Day Leaders</h1>
    <p class="lede" id="lede"></p>
  </header>

  <section class="strip" id="strip" aria-label="Summary"></section>

  <section class="panel" aria-labelledby="f-h">
    <h2 id="f-h">What the leaders have in common</h2>
    <div class="findings" id="findings"></div>
  </section>

  <div class="grid2">
    <section class="panel" aria-labelledby="c1-h">
      <div class="panel-head"><h2 id="c1-h">Top 25 by __DAYS__-day P&amp;L</h2><span class="note">bar = P&amp;L · tint = copy score</span></div>
      <div id="bars" class="chart"></div>
    </section>
    <section class="panel" aria-labelledby="c2-h">
      <div class="panel-head"><h2 id="c2-h">Win rate against entry price</h2></div>
      <p class="note">On Polymarket the price you pay is the win rate you need to break even. Both axes are weighted by dollars staked, so dots above the diagonal won more of their money than their prices implied. Wallets with fewer than 20 resolved positions are hidden.</p>
      <div id="scatter" class="chart"></div>
      <div class="legend" id="scatter-legend"></div>
    </section>
  </div>

  <div class="grid2">
    <section class="panel" aria-labelledby="c3-h">
      <div class="panel-head"><h2 id="c3-h">Where the realized profit came from</h2><span class="note">top 100, positions closed in the window</span></div>
      <div id="cats" class="chart"></div>
    </section>
    <section class="panel" aria-labelledby="c4-h">
      <div class="panel-head"><h2 id="c4-h">Who to follow</h2><span class="note">highest copy scores, still active, bots excluded</span></div>
      <div class="watch" id="watch"></div>
      <pre class="cmd">polyscan watch --top 15 --min-usd 1000   # alerts via ntfy / Telegram / Discord</pre>
    </section>
  </div>

  <section class="panel" aria-labelledby="t-h">
    <div class="panel-head"><h2 id="t-h">The book</h2><span class="note" id="count"></span></div>
    <div class="controls">
      <input type="search" id="q" placeholder="Search name or address" aria-label="Search wallets">
      <select id="cat" aria-label="Filter by focus"><option value="">All focuses</option></select>
      <label><input type="checkbox" id="nobots"> Hide high-frequency bots</label>
      <label><input type="checkbox" id="clean"> Hide luck flags</label>
    </div>
    <div class="tablebox"><table id="tbl"><thead><tr>
      <th data-k="rank" aria-sort="ascending">#</th><th class="l" data-k="name">Wallet</th><th data-k="score">Copy score</th>
      <th data-k="pnl">__DAYS__d P&amp;L</th><th data-k="curve" class="l">Curve</th><th data-k="pnl30">30d</th><th data-k="pnl7">7d</th>
      <th data-k="dd">Max DD</th><th data-k="gw">Green wks</th><th data-k="win">Win rate</th><th data-k="entry">Avg entry</th>
      <th data-k="roi">ROI</th><th data-k="mpd">Mkts/day</th><th class="l" data-k="flags">Style &amp; flags</th>
    </tr></thead><tbody></tbody></table></div>
    <p class="note">Click a row for positions, category mix and weekly P&amp;L. Data comes from Polymarket's public data API.</p>
  </section>

  <section class="panel" aria-labelledby="m-h">
    <h2 id="m-h">How this was measured</h2>
    <div class="method" id="method"></div>
  </section>
</div>
<div id="tip" hidden></div>

<script>
const D = __DATA__;
const P = D.profiles, F = D.findings, DAYS = D.days;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const usd = (v, d = 1) => { const a = Math.abs(v), s = v < 0 ? "−" : "";
  return a >= 1e6 ? `${s}$${(a/1e6).toFixed(d)}M` : a >= 1e3 ? `${s}$${(a/1e3).toFixed(a >= 1e5 ? 0 : d)}k` : `${s}$${a.toFixed(0)}`; };
const pct = (v, d = 0) => `${(v*100).toFixed(d)}%`;
const cls = v => v > 0 ? "up" : v < 0 ? "down" : "";
const CATS = ["Sports","Politics","Crypto","Economy","Culture","Other"];
const CATVAR = {Sports:"--s1",Politics:"--s2",Crypto:"--s3",Economy:"--s4",Culture:"--s5",Other:"--s6"};
const FLAG = {"one-big-day":["one big day","warn"],"one-big-bet":["one big bet","warn"],"bot-speed":["bot speed","bad"],"dormant":["dormant","bad"],
  "favorite-scalper":["favorite scalper","warn"],"longshot-hunter":["longshot hunter",""],"new-wallet":["new wallet",""],
  "deep-drawdown":["deep drawdown","bad"],"small-sample":["small sample","warn"],"cooling-off":["cooling off","bad"]};
const LUCK = ["one-big-day","one-big-bet","small-sample"];

$("#asof").textContent = `Trailing ${DAYS} days · as of ${D.asof}`;
$("#lede").textContent = `${D.pool.toLocaleString()} wallets pulled from every Polymarket leaderboard (all-time, monthly, weekly, volume and nine categories), each re-measured over exactly ${DAYS} days from its daily P&L history. The top ${P.length} by ${DAYS}-day profit were profiled position by position and scored on how copyable their results look.`;

const tip = $("#tip");
function showTip(e, html) { tip.innerHTML = html; tip.hidden = false; moveTip(e); }
function moveTip(e) { const r = tip.getBoundingClientRect(); let x = e.clientX + 14, y = e.clientY + 14;
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14; if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 14;
  tip.style.left = x + "px"; tip.style.top = y + "px"; }
function hideTip() { tip.hidden = true; }

// summary strip
const leader = P[0];
$("#strip").innerHTML = [
  [usd(F.total), `combined ${DAYS}-day P&L, top ${P.length}`],
  [pct(F.top10_share), "of that made by the top 10"],
  [usd(F.cutoff), `needed to make the top ${P.length}`],
  [`${F.edge}/${F.sized}`, "beat their entry-price breakeven"],
  [`${F.bots}`, `trade at bot speed (${pct(F.bot_share)} of P&L)`],
].map(([v,k]) => `<div><span class="v num">${v}</span><span class="k">${k}</span></div>`).join("");

// findings
(function () {
  const cp = F.cat_pnl, tot = Object.values(cp).filter(v => v > 0).reduce((a,b) => a+b, 0) || 1;
  const lead = Object.entries(cp).sort((a,b) => b[1]-a[1])[0];
  const items = [
    ["Concentrated at the top", `<b>${leader.name}</b> alone made ${usd(leader.pnl)}. The top 10 took <b>${pct(F.top10_share)}</b> of everything the top ${P.length} earned, and #${P.length} cleared only ${usd(F.cutoff)}.`],
    [`${lead[0]} pays the most`, `<b>${pct(lead[1]/tot)}</b> of the cohort's realized profit came from ${lead[0].toLowerCase()} markets. ${CATS.filter(c => c !== lead[0] && cp[c] > 0).slice(0,2).map(c => `${c} added ${usd(cp[c])}`).join(", ")}.`],
    ["The edge is a few cents", `${F.edge} of ${F.sized} leaders with a real sample win more of their stakes than their prices imply, but the typical margin is only <b>${(F.margin*100).toFixed(0)} points</b> (${pct(F.median_win)} won at a ${F.median_entry.toFixed(2)} average entry). Copying a few minutes late at a worse price can erase it. The rest profit from exits before resolution, rebates, or positions still open.`],
    ["Much of it is machines", `<b>${F.bots}</b> of the top ${P.length} trade more than 25 different markets a day and booked ${pct(F.bot_share)} of the cohort's P&L. You can study them but you can't follow them by hand.`],
    ["Luck is common", `<b>${F.one_day}</b> leaders made over half their gains on a single day, <b>${F.new}</b> wallets are under ${DAYS} days old, and <b>${F.cooling}</b> are in the red over the last 30 days.`],
    ["Many have already stopped", `<b>${F.dormant}</b> of the top ${P.length} have not traded in two weeks, and ${F.faded} wallets with over $1M of lifetime profit are <b>losing money</b> over the last ${DAYS} days. A leaderboard rank says little about who is good now.`],
  ];
  $("#findings").innerHTML = items.map(([h,p]) => `<div class="finding"><h3>${h}</h3><p>${p}</p></div>`).join("");
})();

// chart: top 25 bars
(function () {
  const rows = P.slice(0, 25), W = 640, rowH = 22, padL = 140, padR = 64, H = rows.length * rowH + 24;
  const max = Math.max(...rows.map(r => r.pnl)), x = v => padL + (W - padL - padR) * Math.max(0, v) / max;
  const ticks = niceTicks(max, 4);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Top 25 wallets by ${DAYS}-day profit">`;
  ticks.forEach(t => s += `<line class="grid" x1="${x(t)}" x2="${x(t)}" y1="0" y2="${H-20}"/><text x="${x(t)}" y="${H-6}" text-anchor="middle">${usd(t,0)}</text>`);
  rows.forEach((r, i) => {
    const y = i * rowH + 3, op = 0.35 + 0.65 * r.score / 100;
    s += `<g class="hit" data-i="${i}"><rect x="0" y="${y-2}" width="${W}" height="${rowH}" fill="transparent"/>
      <text x="${padL-8}" y="${y+12}" text-anchor="end" style="font-family:var(--body);fill:var(--ink)">${esc(r.name.length > 16 ? r.name.slice(0,15)+"…" : r.name)}</text>
      <rect x="${padL}" y="${y+2}" width="${Math.max(2, x(r.pnl)-padL)}" height="${rowH-8}" rx="3" fill="var(--accent)" fill-opacity="${op.toFixed(2)}"/>
      <text x="${x(r.pnl)+6}" y="${y+12}">${usd(r.pnl)}</text></g>`;
  });
  $("#bars").innerHTML = s + "</svg>";
  $("#bars").querySelectorAll(".hit").forEach(g => {
    const r = rows[+g.dataset.i];
    g.addEventListener("mousemove", e => showTip(e, `<div class="t">#${r.rank} ${esc(r.name)}</div>${usd(r.pnl)} in ${DAYS}d · ${usd(r.pnl30)} last 30d<br>Copy score ${r.score} · ${esc(r.style)}`));
    g.addEventListener("mouseleave", hideTip);
  });
})();

function niceTicks(max, n) { const raw = max / n, mag = 10 ** Math.floor(Math.log10(raw)), step = [1,2,2.5,5,10].map(m => m*mag).find(s => s >= raw);
  const out = []; for (let v = 0; v <= max + 1e-9; v += step) out.push(v); return out; }

// chart: win rate vs entry
(function () {
  const pts = P.filter(p => p.resolved >= 20 && p.entry > 0), W = 460, H = 360, pl = 44, pr = 14, pt = 12, pb = 38;
  const x = v => pl + (W-pl-pr) * v, y = v => H - pb - (H-pt-pb) * v;
  pts.forEach(p => p.w = p.winUsd);
  const lead = p => { const n = p.catN, t = Object.values(n).reduce((a,b)=>a+b,0); const k = Object.keys(n).sort((a,b)=>n[b]-n[a])[0] || "Other"; return k; };
  const maxP = Math.max(...pts.map(p => Math.abs(p.pnl)), 1), rad = p => 3.5 + 8 * Math.sqrt(Math.abs(p.pnl) / maxP);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Win rate versus average entry price">`;
  [0,.25,.5,.75,1].forEach(t => { s += `<line class="grid" x1="${x(t)}" x2="${x(t)}" y1="${pt}" y2="${H-pb}"/><line class="grid" x1="${pl}" x2="${W-pr}" y1="${y(t)}" y2="${y(t)}"/>
    <text x="${x(t)}" y="${H-pb+16}" text-anchor="middle">${t.toFixed(2)}</text><text x="${pl-6}" y="${y(t)+4}" text-anchor="end">${pct(t)}</text>`; });
  s += `<line x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}" stroke="var(--ink-3)" stroke-dasharray="4 4" stroke-width="1.5"/>
    <text x="${x(0.97)}" y="${y(0.97)+16}" text-anchor="end">breakeven</text>
    <text x="${(pl+W-pr)/2}" y="${H-4}" text-anchor="middle">average entry price</text>`;
  const used = new Set();
  pts.sort((a,b) => Math.abs(b.pnl) - Math.abs(a.pnl)).forEach((p, i) => { const c = lead(p); used.add(c);
    s += `<circle data-i="${P.indexOf(p)}" cx="${x(p.entry).toFixed(1)}" cy="${y(p.w).toFixed(1)}" r="${rad(p).toFixed(1)}" fill="var(${CATVAR[c]})" fill-opacity="0.8" stroke="var(--panel)" stroke-width="2"/>`; });
  $("#scatter").innerHTML = s + "</svg>";
  $("#scatter-legend").innerHTML = CATS.filter(c => used.has(c)).map(c => `<span><i style="background:var(${CATVAR[c]})"></i>${c} focus</span>`).join("") + `<span class="muted">dot size = ${DAYS}d P&amp;L</span>`;
  $("#scatter").querySelectorAll("circle").forEach(c => { const p = P[+c.dataset.i];
    c.addEventListener("mousemove", e => showTip(e, `<div class="t">#${p.rank} ${esc(p.name)}</div>${pct(p.w)} of stakes won at avg entry ${p.entry.toFixed(2)} (${p.w > p.entry ? "+" : ""}${((p.w-p.entry)*100).toFixed(0)} pts)<br>${pct(p.win)} of ${p.resolved} positions won · ${usd(p.pnl)} ${DAYS}d`));
    c.addEventListener("mouseleave", hideTip); });
})();

// chart: category realized P&L
(function () {
  const cp = F.cat_pnl, rows = CATS.map(c => [c, cp[c] || 0]).filter(r => r[1] !== 0).sort((a,b) => b[1]-a[1]);
  const W = 640, rowH = 30, padL = 84, padR = 74, H = rows.length * rowH + 8;
  const max = Math.max(...rows.map(r => Math.abs(r[1]))), min = Math.min(0, ...rows.map(r => r[1])) * 1.05;
  const x = v => padL + (W-padL-padR) * (v - min) / (max - min);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Realized profit by market category">`;
  s += `<line class="axis" x1="${x(0)}" x2="${x(0)}" y1="0" y2="${H}"/>`;
  rows.forEach(([c, v], i) => { const y = i*rowH + 6, a = x(Math.min(0, v)), b = x(Math.max(0, v)), n = P.reduce((t,p) => t + (p.catN[c]||0), 0);
    s += `<g class="hit" data-c="${c}"><rect x="0" y="${y-3}" width="${W}" height="${rowH}" fill="transparent"/>
      <text x="${padL-10}" y="${y+14}" text-anchor="end" style="font-family:var(--body);fill:var(--ink)">${c}</text>
      <rect x="${a}" y="${y}" width="${Math.max(2,b-a)}" height="${rowH-10}" rx="3" fill="var(${CATVAR[c]})"/>
      <text x="${Math.max(b, x(0))+6}" y="${y+14}">${usd(v)}</text></g>`;
    });
  $("#cats").innerHTML = s + "</svg>";
  $("#cats").querySelectorAll(".hit").forEach(g => { const c = g.dataset.c, n = P.reduce((t,p) => t + (p.catN[c]||0), 0), w = P.filter(p => (p.catN[c]||0) > 0).length;
    g.addEventListener("mousemove", e => showTip(e, `<div class="t">${c}</div>${usd(cp[c])} realized · ${n.toLocaleString()} positions<br>${w} of the top ${P.length} traded it`));
    g.addEventListener("mouseleave", hideTip); });
})();

// follow list
(function () {
  const picks = [...P].filter(p => !p.flags.includes("bot-speed") && !p.flags.includes("dormant")).sort((a,b) => b.score - a.score).slice(0, 6);
  $("#watch").innerHTML = picks.map(p => `<div class="wcard"><span class="n">${esc(p.name)}</span>
    <span class="num ${cls(p.pnl)}">${usd(p.pnl)} <span class="muted" style="font-size:12px">${DAYS}d</span></span>
    <span style="font-size:12.5px" class="muted">Score ${p.score} · won ${pct(p.winUsd)} of stakes at ${p.entry.toFixed(2)} · ${p.gw ? pct(p.gw) : "–"} green weeks</span>
    <span><span class="chip style">${esc(p.style)}</span></span></div>`).join("");
})();

// table
const sel = $("#cat");
[...new Set(P.map(p => p.style))].sort().forEach(s => sel.insertAdjacentHTML("beforeend", `<option>${esc(s)}</option>`));
let sortK = "rank", sortDir = 1, open = null;
try { const v = JSON.parse(localStorage.getItem("polyscan-view") || "{}"); if (v.nobots) $("#nobots").checked = true; if (v.clean) $("#clean").checked = true; } catch (e) {}

function spark(c) { const w = 90, h = 24, n = c.length; if (n < 2) return "";
  const lo = Math.min(0, ...c), hi = Math.max(0, ...c), sy = v => h - 2 - (h-4) * (v - lo) / ((hi - lo) || 1);
  const d = c.map((v,i) => `${(i*(w-4)/(n-1)+2).toFixed(1)},${sy(v).toFixed(1)}`).join(" "), end = c[n-1];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><line x1="2" x2="${w-2}" y1="${sy(0)}" y2="${sy(0)}" class="grid"/><polyline points="${d}" fill="none" stroke="var(--accent)" stroke-width="1.5"/><circle cx="${w-2}" cy="${sy(end)}" r="2.5" fill="var(${end >= 0 ? "--up" : "--down"})"/></svg>`; }

function chips(p) { return `<span class="chip style">${esc(p.style)}</span>` + p.flags.map(f => { const [t, k] = FLAG[f] || [f, ""]; return `<span class="chip ${k}">${t}</span>`; }).join(""); }

function detail(p) {
  const cat = Object.entries(p.catPnl).sort((a,b) => b[1]-a[1]);
  const wk = p.weekly, wmax = Math.max(1, ...wk.map(Math.abs)), W = 260, H = 64;
  const bw = W / wk.length;
  const weeks = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Weekly P&L"><line class="axis" x1="0" x2="${W}" y1="${H/2}" y2="${H/2}"/>` +
    wk.map((v,i) => { const h = Math.abs(v) / wmax * (H/2 - 2); return `<rect x="${i*bw+1}" y="${v >= 0 ? H/2-h : H/2}" width="${bw-2}" height="${Math.max(h,1)}" rx="1.5" fill="var(${v >= 0 ? "--up" : "--down"})"><title>Week ${i+1}: ${usd(v)}</title></rect>`; }).join("") + "</svg>";
  const pl = xs => xs.length ? `<ul class="plist">${xs.map(x => `<li><span title="${esc(x.title)}">${esc(x.title)} · ${esc(x.outcome)}</span><span class="${cls(x.pnl ?? x.value)}">${x.pnl !== undefined ? usd(x.pnl) : usd(x.value)}</span></li>`).join("")}</ul>` : `<p class="note">None</p>`;
  return `<div class="dgrid">
    <div><h3>Record</h3><dl class="kv">
      <dt>Lifetime P&amp;L</dt><dd class="${cls(p.pnlAll)}">${usd(p.pnlAll)}</dd><dt>Best day</dt><dd>${usd(p.bestDay)} (${pct(p.bestShare)} of gains)</dd>
      <dt>Sharpe (daily, ann.)</dt><dd>${p.sharpe}</dd><dt>Stakes won ($-weighted)</dt><dd>${pct(p.winUsd)} vs entry ${p.entry.toFixed(2)}</dd><dt>Resolved positions</dt><dd>${p.resolved.toLocaleString()}</dd>
      <dt>Avg win / loss</dt><dd>${usd(p.avgWin)} / ${usd(p.avgLoss)}</dd><dt>Profit factor</dt><dd>${p.pf ?? "∞"}</dd>
      <dt>Median trade</dt><dd>${usd(p.medTrade)}</dd><dt>Buys at ≥ 0.90</dt><dd>${pct(p.fav)}</dd><dt>Buys at ≤ 0.15</dt><dd>${pct(p.long)}</dd>
      <dt>Markets a day</dt><dd>${p.mpd} (${p.tpd.toLocaleString()} fills)</dd><dt>Last trade</dt><dd>${p.idle == null ? "–" : p.idle < 1 ? "today" : Math.round(p.idle) + " days ago"}</dd><dt>Open now</dt><dd>${p.openN} · ${usd(p.openVal)}</dd><dt>Wallet age</dt><dd>${p.age} days</dd></dl>
      <a href="https://polymarket.com/profile/${p.wallet}" target="_blank" rel="noopener">Open profile on Polymarket ↗</a>
      <span class="mono note">${p.wallet}</span></div>
    <div><h3>Weekly P&amp;L, last ${DAYS} days</h3>${weeks}<h3>Realized by category</h3>
      <ul class="plist">${cat.map(([c,v]) => `<li><span><i style="display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;background:var(${CATVAR[c]})"></i>${c} · ${p.catN[c]} pos.</span><span class="${cls(v)}">${usd(v)}</span></li>`).join("")}</ul>
      <h3>Score breakdown</h3><ul class="plist">${Object.entries(p.parts).map(([k,v]) => `<li><span>${k}</span><span>${(v*100).toFixed(0)}</span></li>`).join("")}</ul></div>
    <div><h3>Biggest wins</h3>${pl(p.top)}<h3>Biggest losses</h3>${pl(p.worst)}<h3>Largest open positions</h3>${pl(p.open)}
      ${p.capped ? `<p class="note">Trade stats use a sample of the most recent fills.</p>` : ""}</div></div>`;
}

function rows() {
  const q = $("#q").value.trim().toLowerCase(), c = sel.value, nb = $("#nobots").checked, cl = $("#clean").checked;
  try { localStorage.setItem("polyscan-view", JSON.stringify({nobots: nb, clean: cl})); } catch (e) {}
  let r = P.filter(p => (!q || p.name.toLowerCase().includes(q) || p.wallet.includes(q)) && (!c || p.style === c)
    && (!nb || !p.flags.includes("bot-speed")) && (!cl || !p.flags.some(f => LUCK.includes(f))));
  const key = sortK === "curve" ? "pnl" : sortK === "flags" ? "style" : sortK;
  r.sort((a,b) => (a[key] > b[key] ? 1 : a[key] < b[key] ? -1 : 0) * sortDir);
  $("#count").textContent = `${r.length} of ${P.length} wallets`;
  $("#tbl tbody").innerHTML = r.map(p => `<tr class="row" tabindex="0" data-w="${p.wallet}" aria-expanded="${open === p.wallet}">
    <td>${p.rank}</td><td class="l who"><b>${esc(p.name)}</b><small>${p.wallet.slice(0,6)}…${p.wallet.slice(-4)}</small></td>
    <td><span class="score"><span class="bar"><i style="width:${p.score}%"></i></span>${p.score.toFixed(0)}</span></td>
    <td class="${cls(p.pnl)}">${usd(p.pnl)}</td><td class="l">${spark(p.curve)}</td>
    <td class="${cls(p.pnl30)}">${usd(p.pnl30)}</td><td class="${cls(p.pnl7)}">${usd(p.pnl7)}</td><td>${usd(p.dd)}</td>
    <td>${pct(p.gw)}</td><td>${p.resolved ? pct(p.win) : "–"}</td><td>${p.entry ? p.entry.toFixed(2) : "–"}</td>
    <td class="${cls(p.roi)}">${p.resolved ? pct(p.roi, 1) : "–"}</td><td>${p.mpd.toLocaleString()}</td>
    <td class="flags">${chips(p)}</td></tr>` + (open === p.wallet ? `<tr class="detail"><td colspan="14">${detail(p)}</td></tr>` : "")).join("");
}
$("#tbl thead").addEventListener("click", e => { const th = e.target.closest("th"); if (!th) return; const k = th.dataset.k;
  sortDir = k === sortK ? -sortDir : (["rank","name","entry","dd"].includes(k) ? 1 : -1); sortK = k;
  document.querySelectorAll("#tbl th").forEach(t => t.removeAttribute("aria-sort")); th.setAttribute("aria-sort", sortDir > 0 ? "ascending" : "descending"); rows(); });
function toggle(tr) { const w = tr.dataset.w; open = open === w ? null : w; rows(); }
$("#tbl tbody").addEventListener("click", e => { const tr = e.target.closest("tr.row"); if (tr && !e.target.closest("a")) toggle(tr); });
$("#tbl tbody").addEventListener("keydown", e => { const tr = e.target.closest("tr.row"); if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggle(tr); } });
["#q", "#cat", "#nobots", "#clean"].forEach(s => $(s).addEventListener("input", rows));
rows();

$("#method").innerHTML = [
  `<b>Candidate pool.</b> Polymarket publishes leaderboards for a day, week, month and all time, but not for ${DAYS} days. The scan pulls the all-time top 1,000, the monthly top 500 by profit, the monthly and weekly leaders by volume and profit, and the top 100 of each category, then measures every wallet itself. That gave ${D.pool.toLocaleString()} wallets.`,
  `<b>${DAYS}-day P&amp;L.</b> Each wallet's daily cumulative P&amp;L series (realized plus mark-to-market, the same numbers Polymarket's profile chart uses) is read at today and ${DAYS} days ago. Drawdown, Sharpe and green weeks come from the same curve.`,
  `<b>Positions.</b> Resolved positions are closed positions plus losing tokens still sitting unredeemed in the wallet. Most traders never redeem losers, so ignoring them inflates win rates badly. Win rate, ROI and average entry are cost-weighted over positions resolved in the window.`,
  `<b>Copy score (0–100).</b> profit percentile 20%, consistency (green weeks and Sharpe) 20%, breadth (not one big day, enough positions) 15%, drawdown versus profit 15%, realized ROI 15%, and followability 15%, which penalises bot-speed trading (over 25 markets a day), buying near-certain favorites, and wallets that have not traded in 14 days.`,
  `<b>Limits.</b> High-frequency wallets are sampled (latest 5,000 fills and 3,000 closed positions). Categories come from market slugs and titles. Past ${DAYS}-day profit is not a forecast, and a wallet you copy can stop trading or change style at any time.`,
].map(t => `<p>${t}</p>`).join("");
</script>
"""
