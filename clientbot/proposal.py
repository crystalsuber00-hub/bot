"""One-page proposal for a prospect after the call: their gaps, the fix, the math, the price, the guarantee."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .audit import gaps
from .copywriter import breakeven


def render(agency, lead: dict, missed_per_week: int = 10, recover_rate: float = 0.3) -> str:
    """missed_per_week / recover_rate are assumptions you should replace with what the owner told you on the call."""
    cfg, niche = agency.cfg, agency.niche(lead["niche"])
    price, a = agency.price(niche), json.loads(lead["audit"] or "{}")
    be = breakeven(price, niche)
    recovered = round(missed_per_week * 4.3 * recover_rate)
    value = recovered * niche.ticket
    e = html.escape
    found = "".join(f"<li>{e(g[0].upper() + g[1:])}.</li>" for g in gaps(a)) if a.get("ok") else ""
    found += f"<li>For {e(niche.label)}, {e(niche.pain)}.</li>"
    guarantee = cfg.offer.guarantee.format(breakeven=be, unit=niche.unit)
    setup = f"${cfg.offer.setup_fee:,} one-time setup, then " if cfg.offer.setup_fee else "No setup fee. "
    cal = (f'<p><a class="btn" href="{e(cfg.agency.calendar_url)}">Book the kickoff call</a></p>' if cfg.agency.calendar_url
           else "<p>Reply to my email to get started.</p>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(lead['name'])}: AI front desk proposal</title>
<style>
:root{{--bg:#fff;--fg:#1d2330;--mut:#5b6475;--acc:#2456d6;--card:#f4f6fa}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12151b;--fg:#e8ebf1;--mut:#a3abba;--acc:#7da2ff;--card:#1c212b}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:720px;margin:0 auto;padding:40px 16px}} h1{{font-size:28px;margin:0 0 4px}} h2{{font-size:19px;margin:32px 0 8px}}
.mut{{color:var(--mut)}} .card{{background:var(--card);border-radius:12px;padding:16px 20px;margin:12px 0}}
.num{{font-size:30px;font-weight:700}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.btn{{display:inline-block;background:var(--acc);color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:600}}
li{{margin:4px 0}}
</style></head><body><main>
<p class="mut">{e(cfg.agency.name)} &middot; prepared for {e(lead['name'])}</p>
<h1>Never miss another booking at {e(lead['name'])}</h1>
<p class="mut">An AI front desk that answers when your team can't, and hands anything tricky to you.</p>

<h2>What we noticed</h2><ul>{found}</ul>

<h2>What you get</h2>
<div class="card"><b>Missed-call text-back.</b> Every call you can't pick up gets a text from {e(lead['name'])} within seconds, so the caller doesn't dial your competitor.</div>
<div class="card"><b>24/7 texting assistant.</b> Answers questions about your services, prices, hours and policies by text, and sends your booking link. It says it's an assistant, and texts you whenever a person is needed.</div>
<div class="card"><b>Instant lead replies.</b> Website and ad enquiries get a personal text and email in under a minute, day or night.</div>
<div class="card"><b>Done for you.</b> We set it up in about a week, keep it updated when your prices or hours change, and send you a monthly report of calls caught and leads answered.</div>

<h2>The math</h2>
<div class="grid">
<div class="card"><div class="num">{recovered}</div><div class="mut">{e(niche.unit)}s a month recovered*</div></div>
<div class="card"><div class="num">${value:,}</div><div class="mut">a month in recovered revenue*</div></div>
<div class="card"><div class="num">{be}</div><div class="mut">{e(niche.unit)}s a month to break even</div></div>
</div>
<p class="mut">*Estimate: {missed_per_week} missed calls or leads a week, {recover_rate:.0%} of them booked by the assistant,
~${niche.ticket} per {e(niche.unit)}. Your real numbers will show in the first monthly report.</p>

<h2>Investment</h2>
<div class="card"><div class="num">${price:,}<span class="mut" style="font-size:16px"> / month</span></div>
<p>{e(setup)}Month to month, cancel any time with 30 days' notice. Phone number, texting and AI costs included.</p>
<p><b>Guarantee:</b> {e(guarantee)}</p></div>

<h2>Next steps</h2>
<ol><li>15-minute kickoff: your services, prices, hours, booking link.</li>
<li>We register your texting number with the carriers (required in the US; takes a few days) and build the assistant.</li>
<li>You test it from your own phone, then we switch it on.</li></ol>
{cal}
<p class="mut">{e(cfg.agency.owner_name)} &middot; {e(cfg.agency.name)}{' &middot; ' + e(cfg.agency.phone) if cfg.agency.phone else ''}</p>
</main></body></html>"""


def write(agency, lead_id: int, out_dir: str = "proposals", **assumptions) -> str:
    lead = agency.db.lead(lead_id)
    if lead is None:
        raise ValueError(f"no lead {lead_id}")
    path = Path(out_dir) / f"{lead_id}-{re.sub(r'[^a-z0-9]+', '-', lead['name'].lower()).strip('-')}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(agency, lead, **assumptions))
    if lead["stage"] in ("replied", "meeting"):
        agency.mark(lead_id, "proposal")
    return str(path)
