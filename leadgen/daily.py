"""business/today.md: follow-ups due, then the best new leads, each with preview link and opener."""
from __future__ import annotations

from datetime import date

from .preview import slug
from .tracker import HOME, load


def preview_url(lead: dict, base: str) -> str:
    s = slug(lead["name"], lead["id"])
    return f"{base.rstrip('/')}/{s}/" if base else f"site/{s}/index.html (not published yet)"


def opener(lead: dict, you: dict) -> str:
    online = "your Facebook page" if "facebook" in lead["social"].lower() else (
        "your social page" if lead["social"] else "a website for you")
    found = f"I found {online}" if lead["social"] else "I couldn't find a website for you"
    return (f"Hi, is this the owner or manager of {lead['name']}? My name is {you['name']}, I'm local here. "
            f"{found}, so I went ahead and built you a free preview website. Could I text you the link? "
            f"No cost to look.")


def email_draft(lead: dict, you: dict, offer: dict) -> str:
    return f"""Subject: I built a free website preview for {lead['name']}

Hi {lead['name']} team,

I'm {you['name']} with {you['business']}, a local web designer. I couldn't find a website for {lead['name']}, so I made a free preview of one:

{preview_url(lead, offer['preview_base_url'])}

If you like it, I can put it live on your own web address within a week: ${offer['setup_price']} to set up, then ${offer['monthly_price']}/month for hosting and small updates. If not, no problem at all.

{you['name']}
{you['business']} | {you['phone']}
{you['mailing_address']}

Not interested? Reply "no thanks" and I won't email again.
"""


def write(cfg: dict) -> str:
    you, offer = cfg["you"], cfg["offer"]
    rows = load()
    today = date.today().isoformat()
    due = [r for r in rows if r["next_followup"] and r["next_followup"] <= today
           and r["status"] in ("called", "voicemail", "interested")]
    new = [r for r in rows if r["status"] == "new" and (r["phone"] or r["email"])][:max(0, offer["daily_calls"] - len(due))]
    lines = [f"# Today: {date.today():%A %B %d}", "",
             f"{len(due)} follow-ups, {len(new)} new calls. After each call run the `mark` command shown "
             "(statuses: called, voicemail, interested, won, lost, do_not_contact).", "",
             "Before each contact, search the name on Google for 30 seconds. The map data behind these leads "
             "misses some websites and chains; if they already have a real site, mark them `lost \"has site\"`.", ""]
    if due:
        lines += ["## Follow-ups due", ""]
        for r in due:
            lines += [f"### {r['name']} ({r['status']})", f"- Phone: {r['phone'] or '-'}  Email: {r['email'] or '-'}",
                      f"- Preview: {preview_url(r, offer['preview_base_url'])}", f"- Notes: {r['notes'] or '-'}",
                      f"- `python -m leadgen mark {r['id']} <status> \"note\"`", ""]
    lines += ["## New calls", ""]
    for r in new:
        lines += [f"### {r['name']} ({r['category']})",
                  f"- Phone: {r['phone'] or '-'}  Email: {r['email'] or '-'}",
                  f"- Address: {', '.join(x for x in (r['address'], r['city']) if x) or '-'}",
                  f"- Online now: {r['social'] or 'nothing found'}",
                  f"- Preview: {preview_url(r, offer['preview_base_url'])}",
                  f"- Say: \"{opener(r, you)}\"",
                  f"- `python -m leadgen mark {r['id']} <status> \"note\"`", ""]
    walk = [r for r in rows if r["status"] == "new" and not r["phone"] and not r["email"] and r["address"]]
    if walk:
        walk.sort(key=lambda r: r["address"].split(" ", 1)[-1])  # group by street so you can walk them
        lines += ["## Walk-ins (no phone listed; visit with the preview on your phone)", ""]
        for r in walk[:15]:
            lines += [f"- **{r['name']}** ({r['category']}), {r['address']}, {r['city']}: "
                      f"{preview_url(r, offer['preview_base_url'])} `{r['id']}`"]
        lines.append("")
    text = "\n".join(lines)
    (HOME / "today.md").write_text(text)
    return text
