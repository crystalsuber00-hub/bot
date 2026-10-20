"""The client-acquisition pipeline: find businesses -> audit their sites -> write -> send -> catch replies -> close."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import niches, sources
from .audit import Fetch, audit, gaps, http_fetch, score
from .config import Config, Mailbox
from .copywriter import Copywriter, breakeven, fallback_sequence
from .db import DB, DONE, jdump
from .outreach import Inbox, Mailer, Notifier

log = logging.getLogger("clientbot")

SOCIAL = ("facebook.com", "instagram.com", "yelp.com", "linktr.ee", "google.com", "business.site", "nextdoor.com",
          "booksy.com", "vagaro.com", "fresha.com", "styleseat.com", "square.site", "wixsite.com")
FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "live.com", "msn.com", "me.com"}
SEQUENCE_LEN = 4
# Rough cold-email benchmarks for local-business outreach, used only until you have your own numbers.
BENCH = {"positive_reply": 0.015, "meeting": 0.6, "close": 0.3}


class Agency:
    def __init__(self, cfg: Config, db: DB | None = None, source=None, fetch: Fetch = http_fetch, mailer: Mailer | None = None,
                 inbox: Inbox | None = None, copywriter: Copywriter | None = None, clock=time.time):
        self.cfg = cfg
        self.db = db or DB(cfg.db_path)
        self._source = source
        self.fetch = fetch
        self.mailer = mailer or Mailer(cfg)
        self.inbox_reader = inbox or Inbox()
        self.copywriter = copywriter or Copywriter(cfg.copywriting.enabled, cfg.copywriting.model)
        self.notifier = Notifier(cfg, self.mailer)
        self.clock = clock
        self.tz = ZoneInfo(cfg.agency.timezone)

    # ---- helpers -------------------------------------------------------------------------------------------------
    def niche(self, key: str) -> niches.Niche:
        return niches.get(key, self.cfg.niche_overrides)

    def price(self, niche: niches.Niche) -> int:
        return niche.price or self.cfg.offer.monthly_price

    def ctx(self, niche: niches.Niche) -> dict:
        price, a = self.price(niche), self.cfg.agency
        be = breakeven(price, niche)
        return {"owner_name": a.owner_name, "agency_name": a.name, "calendar_url": a.calendar_url, "price": price,
                "breakeven": be, "guarantee": self.cfg.offer.guarantee.format(breakeven=be, unit=niche.unit)}

    def source(self, name: str | None = None):
        if name or self._source is None:
            self._source = sources.provider(self.cfg, name)
        return self._source

    def local(self, ts: float | None = None) -> datetime:
        return datetime.fromtimestamp(ts if ts is not None else self.clock(), self.tz)

    def day_start(self, ts: float) -> float:
        return self.local(ts).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    # ---- 1. find -------------------------------------------------------------------------------------------------
    def add_leads(self, rows: list[dict]) -> tuple[int, int]:
        added = dupes = 0
        now = self.clock()
        for r in rows:
            if r["domain"] and any(r["domain"] == s or r["domain"].endswith("." + s) for s in SOCIAL):
                r["domain"] = ""      # a Facebook page or booking-app profile instead of a website
            dupe = self.db.one("SELECT id FROM leads WHERE source = ? AND source_id = ?", (r["source"], r["source_id"]))
            if not dupe and r["domain"]:
                dupe = self.db.one("SELECT id FROM leads WHERE domain = ?", (r["domain"],))
            if not dupe and r["phone"]:
                dupe = self.db.one("SELECT id FROM leads WHERE phone = ?", (r["phone"],))
            if dupe:
                dupes += 1
                continue
            cols = {**r, "created_at": now, "updated_at": now}
            self.db.x(f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
            added += 1
        return added, dupes

    def find(self, niche_keys: list[str] | None = None, cities: list[str] | None = None, provider: str | None = None) -> str:
        src = self.source(provider)
        out = []
        for key in niche_keys or self.cfg.targeting.niches:
            for city in cities or self.cfg.targeting.cities:
                try:
                    rows = src.search(self.niche(key), city)
                except (sources.SourceError, OSError, ValueError) as e:
                    out.append(f"{key} in {city}: {e}")
                    continue
                added, dupes = self.add_leads(rows)
                self.db.x("INSERT INTO searches (niche, city, provider, found, ts) VALUES (?, ?, ?, ?, ?) "
                          "ON CONFLICT(niche, city, provider) DO UPDATE SET found = excluded.found, ts = excluded.ts",
                          (key, city, src.name, added, self.clock()))
                out.append(f"{key} in {city}: {added} new, {dupes} already known")
        return "; ".join(out)

    def import_csv(self, path: str, niche: str = "", city: str = "") -> str:
        added, dupes = self.add_leads(sources.read_csv(path, niche, city))
        return f"imported {added} leads ({dupes} duplicates skipped)"

    def refill(self) -> str:
        """Search the next niche x city not searched yet, when the pipeline is running low."""
        waiting = self.db.one("SELECT COUNT(*) n FROM leads WHERE stage IN ('new', 'audited', 'drafted', 'queued')")["n"]
        if waiting >= self.cfg.outreach.leads_buffer:
            return f"{waiting} leads waiting; no search needed"
        name = self.source().name
        done = {(r["niche"], r["city"]) for r in self.db.q("SELECT niche, city FROM searches WHERE provider = ?", (name,))}
        for city in self.cfg.targeting.cities:
            for key in self.cfg.targeting.niches:
                if (key, city) not in done:
                    return self.find([key], [city])
        if self.db.get_setting("refill_warned") != str(int(waiting)):
            self.db.set_setting("refill_warned", str(int(waiting)))
            self.notifier.alert("Running out of leads", "Every niche x city in your config has been searched. Add cities "
                                "to [targeting] cities, or import a CSV (clientbot import leads.csv --niche hvac).")
        return "all searches done"

    # ---- 2. audit ------------------------------------------------------------------------------------------------
    def audit(self, limit: int = 25) -> str:
        t = self.cfg.targeting
        counts = {"audited": 0, "call": 0, "skipped": 0}
        for lead in self.db.q("SELECT * FROM leads WHERE stage = 'new' ORDER BY id LIMIT ?", (limit,)):
            website = lead["website"] if lead["domain"] else ""
            a = audit(website, self.fetch)
            if not website and lead["website"]:
                a["reason"] = "only a social/booking-app page, no website"
            email = lead["email"] or a.get("email", "")
            pts, why = score({**lead, "email": email}, a, self.niche(lead["niche"]), t.min_reviews, t.max_reviews,
                             t.min_rating, t.exclude_words)
            if email and self.db.suppressed(email):
                stage, reason = "skipped", "on the do-not-contact list"
            elif not email and not lead["phone"]:
                stage, reason = "skipped", "no way to contact"
            elif pts + (0 if email else 20) < t.min_score:   # judge phone-only leads on fit, not on the missing email
                stage, reason = "skipped", f"score {pts} < {t.min_score}"
            elif email:
                stage, reason = "audited", ""
            else:
                stage, reason = "call", "no email found"
            counts[stage] += 1
            self.db.update(lead["id"], audit=jdump(a), email=email, score=pts, score_reasons=jdump(why), stage=stage,
                           stage_reason=reason)
        return ", ".join(f"{v} {k}" for k, v in counts.items())

    # ---- 3. write ------------------------------------------------------------------------------------------------
    def write(self, limit: int = 20) -> str:
        n = 0
        for lead in self.db.q("SELECT * FROM leads WHERE stage IN ('audited', 'call') AND call_script = '' "
                              "ORDER BY score DESC LIMIT ?", (limit,)):
            niche, a = self.niche(lead["niche"]), json.loads(lead["audit"])
            if lead["stage"] == "call":   # phone-only leads get the template opener; no API cost
                self.db.update(lead["id"], call_script=fallback_sequence(lead, niche, a, self.ctx(niche))["call_opener"])
                continue
            seq = self.copywriter.sequence(lead, niche, a, self.ctx(niche))
            with self.db.conn() as c:
                c.execute("DELETE FROM drafts WHERE lead_id = ?", (lead["id"],))
                for step, body in enumerate(seq["emails"]):
                    c.execute("INSERT INTO drafts (lead_id, step, subject, body) VALUES (?, ?, ?, ?)",
                              (lead["id"], step, seq["subject"] if step == 0 else "Re: " + seq["subject"], body.strip()))
            self.db.update(lead["id"], subject=seq["subject"], call_script=seq["call_opener"],
                           stage="queued" if self.cfg.outreach.auto_send else "drafted")
            n += 1
        return f"wrote {n} sequences" + ("" if self.cfg.outreach.auto_send or not n else "; review with `clientbot drafts`, then `clientbot approve`")

    def approve(self, ids: list[int] | None = None) -> int:
        if ids:
            marks = ",".join("?" * len(ids))
            with self.db.conn() as c:
                return c.execute(f"UPDATE leads SET stage = 'queued' WHERE stage = 'drafted' AND id IN ({marks})", ids).rowcount
        with self.db.conn() as c:
            return c.execute("UPDATE leads SET stage = 'queued' WHERE stage = 'drafted'").rowcount

    # ---- 4. send -------------------------------------------------------------------------------------------------
    def limit_today(self, box: Mailbox, now: float) -> int:
        first = self.db.one("SELECT MIN(ts) t FROM messages WHERE mailbox = ? AND direction = 'out'", (box.address,))["t"]
        days = int((self.day_start(now) - self.day_start(first)) / 86400) if first else 0
        return min(box.daily_limit, box.ramp_start + box.ramp_per_day * days)

    def sent_since(self, box: Mailbox, since: float) -> int:
        return self.db.one("SELECT COUNT(*) n FROM messages WHERE mailbox = ? AND direction = 'out' AND ts >= ?",
                           (box.address, since))["n"]

    def check_bounces(self, box: Mailbox, now: float) -> bool:
        """Pause a mailbox whose bounce rate passes 5%: past that, providers start treating you as a spammer."""
        week = now - 7 * 86400
        sent = self.sent_since(box, week)
        bounced = self.db.one("SELECT COUNT(*) n FROM messages WHERE mailbox = ? AND kind = 'bounce' AND ts >= ?",
                              (box.address, week))["n"]
        if sent >= 20 and bounced / sent > 0.05:
            if self.db.get_setting(f"paused:{box.address}") != "1":
                self.db.set_setting(f"paused:{box.address}", "1")
                self.notifier.alert(f"Paused {box.address}", f"{bounced} bounces out of {sent} emails this week. Clean your lead "
                                    "list (verify emails), then run `clientbot resume`.", urgent=True)
            return True
        return False

    def send(self, now: float | None = None) -> str:
        now = now or self.clock()
        o, local = self.cfg.outreach, self.local(now)
        if local.weekday() not in o.send_weekdays or not (o.send_start_hour <= local.hour < o.send_end_hour):
            return "outside the sending window"
        out = []
        for box in self.cfg.mailboxes:
            if self.db.get_setting(f"paused:{box.address}") == "1" or self.check_bounces(box, now):
                out.append(f"{box.address}: paused")
                continue
            limit, sent = self.limit_today(box, now), self.sent_since(box, self.day_start(now))
            last = self.db.one("SELECT MAX(ts) t FROM messages WHERE mailbox = ? AND direction = 'out'", (box.address,))["t"]
            if sent >= limit or (last and now - last < o.min_gap_seconds):
                continue
            lead = (self.db.one("SELECT * FROM leads WHERE stage = 'contacted' AND mailbox = ? AND next_send_at <= ? "
                                "ORDER BY next_send_at LIMIT 1", (box.address, now))
                    or self.db.one("SELECT * FROM leads WHERE stage = 'queued' ORDER BY score DESC, id LIMIT 1"))
            if lead is None:
                continue
            out.append(self.send_one(box, lead, now))
        return "; ".join(out) or "nothing due"

    def send_one(self, box: Mailbox, lead: dict, now: float) -> str:
        if lead["stage"] == "queued" and not self.db.claim(
                "UPDATE leads SET mailbox = ?, stage = 'contacted' WHERE id = ? AND stage = 'queued'", (box.address, lead["id"])):
            return "lead taken"
        if self.db.suppressed(lead["email"]):
            self.db.update(lead["id"], stage="unsubscribed", next_send_at=None, stage_reason="on the do-not-contact list")
            return f"skipped {lead['email']} (do not contact)"
        step = lead["step"]
        draft = self.db.one("SELECT * FROM drafts WHERE lead_id = ? AND step = ?", (lead["id"], step))
        if draft is None:
            self.db.update(lead["id"], stage="finished", next_send_at=None)
            return f"{lead['name']}: sequence finished"
        mid = self.mailer.send(box, lead["email"], draft["subject"], draft["body"], in_reply_to=lead["thread_id"])
        if mid == "refused":
            self.db.suppress(lead["email"], "rejected by mail server")
            self.db.update(lead["id"], stage="bounced", next_send_at=None)
            return f"{lead['email']} refused"
        if not mid:
            if step == 0:
                self.db.update(lead["id"], stage="queued", mailbox="")
            fails = int(self.db.get_setting(f"fails:{box.address}", "0")) + 1
            self.db.set_setting(f"fails:{box.address}", str(fails))
            if fails == 3:
                self.notifier.alert(f"Can't send from {box.address}", "3 failures in a row; check the password / SMTP "
                                    "settings with `clientbot check`.", urgent=True)
            return f"send failed from {box.address}"
        self.db.set_setting(f"fails:{box.address}", "0")
        self.db.x("INSERT INTO messages (lead_id, direction, kind, mailbox, subject, body, message_id, ts) "
                  "VALUES (?, 'out', 'email', ?, ?, ?, ?, ?)", (lead["id"], box.address, draft["subject"], draft["body"], mid, now))
        step += 1
        waits = self.cfg.outreach.followup_days
        more = step < SEQUENCE_LEN and step - 1 < len(waits)
        self.db.update(lead["id"], step=step, thread_id=lead["thread_id"] or mid, stage="contacted" if more else "finished",
                       next_send_at=now + waits[step - 1] * 86400 if more else None)
        return f"email {step} to {lead['name']} <{lead['email']}>"

    # ---- 5. replies ----------------------------------------------------------------------------------------------
    def match_lead(self, sender: str) -> dict | None:
        lead = self.db.one("SELECT * FROM leads WHERE email = ? AND step > 0", (sender,))
        domain = sender.rsplit("@", 1)[-1]
        if lead is None and domain not in FREEMAIL:
            lead = self.db.one("SELECT * FROM leads WHERE (domain = ? OR email LIKE ?) AND step > 0 ORDER BY step DESC",
                               (domain, "%@" + domain))
        return lead

    def inbox(self) -> str:
        found = []
        for box in self.cfg.mailboxes:
            try:
                mail = self.inbox_reader.fetch(box)
            except Exception as e:   # imaplib.IMAP4.error, socket errors, bad passwords: report and try next time
                found.append(f"{box.address}: {e}")
                continue
            for m in mail:
                key = m.message_id or f"{m.sender}|{m.subject}|{hash(m.body)}"
                if not self.db.claim("INSERT OR IGNORE INTO seen_mail (message_id, ts) VALUES (?, ?)", (key, self.clock())):
                    continue
                found.append(self.handle(box, m))
        return "; ".join(x for x in found if x) or "no new replies"

    def handle(self, box: Mailbox, m) -> str:
        now = self.clock()
        if m.bounce:
            hits = []
            for addr in m.mentions:
                lead = self.db.one("SELECT * FROM leads WHERE email = ? AND step > 0", (addr,))
                if lead and lead["stage"] not in ("bounced",):
                    self.db.suppress(addr, "bounced")
                    self.db.update(lead["id"], stage="bounced", next_send_at=None)
                    self.db.x("INSERT INTO messages (lead_id, direction, kind, mailbox, subject, body, message_id, ts) "
                              "VALUES (?, 'in', 'bounce', ?, ?, '', ?, ?)", (lead["id"], box.address, m.subject, m.message_id, now))
                    hits.append(addr)
            return f"bounced: {', '.join(hits)}" if hits else ""
        lead = self.match_lead(m.sender)
        if lead is None:
            return ""
        self.db.x("INSERT INTO messages (lead_id, direction, kind, mailbox, subject, body, message_id, ts) VALUES (?, 'in', ?, ?, ?, ?, ?, ?)",
                  (lead["id"], "auto" if m.auto else "reply", box.address, m.subject, m.body, m.message_id, now))
        niche = self.niche(lead["niche"])
        last = self.db.one("SELECT body FROM messages WHERE lead_id = ? AND direction = 'out' ORDER BY ts DESC LIMIT 1", (lead["id"],))
        verdict = {"kind": "out_of_office"} if m.auto else self.copywriter.reply(lead, last["body"] if last else "", m.body, self.ctx(niche))
        kind = verdict["kind"]
        if kind == "out_of_office":
            if lead["stage"] == "contacted" and lead["next_send_at"]:
                self.db.update(lead["id"], next_send_at=max(lead["next_send_at"], now + 3 * 86400))
            return f"{lead['name']}: auto-reply"
        if lead["stage"] in ("won", "meeting", "proposal"):   # an ongoing conversation; you handle it
            self.notifier.alert(f"{lead['name']} replied", m.body[:500])
            return f"{lead['name']}: reply in an active deal"
        base = {"replied_at": now, "reply_kind": kind, "reply_text": m.body[:4000], "next_send_at": None,
                "suggested_reply": verdict.get("suggested_reply", "")}
        if kind == "unsubscribe":
            self.db.suppress(m.sender, "asked to stop")
            self.db.suppress(lead["email"], "asked to stop")
            self.db.update(lead["id"], stage="unsubscribed", **base)
            return f"{lead['name']}: unsubscribed"
        if kind == "not_interested":
            self.db.suppress(lead["email"], "not interested")
            domain = lead["domain"] or lead["email"].rsplit("@", 1)[-1]
            if domain not in FREEMAIL:   # don't email their colleagues either
                self.db.suppress("@" + domain, "not interested")
            self.db.update(lead["id"], stage="lost", stage_reason="said no", **base)
            return f"{lead['name']}: not interested"
        self.db.update(lead["id"], stage="replied", **base)
        self.notifier.alert(f"Reply from {lead['name']} ({kind})",
                            f"{lead['name']} <{m.sender}>: {verdict.get('summary') or m.body[:300]}\n\n"
                            f"Suggested reply:\n{verdict.get('suggested_reply', '')}\n\nPhone: {lead['phone']}\n"
                            f"Then: clientbot mark {lead['id']} meeting", urgent=True)
        return f"{lead['name']}: {kind}"

    # ---- 6. pipeline ---------------------------------------------------------------------------------------------
    def mark(self, lead_id: int, stage: str, mrr: float | None = None, note: str = "") -> str:
        lead = self.db.lead(lead_id)
        if lead is None:
            return f"no lead {lead_id}"
        cols = {"stage": stage, "next_send_at": None}
        if stage == "won":
            cols["mrr"] = mrr if mrr is not None else self.price(self.niche(lead["niche"]))
        if stage == "lost" and lead["email"]:
            self.db.suppress(lead["email"], "lost deal")
        if note:
            cols["notes"] = (lead["notes"] + "\n" if lead["notes"] else "") + f"{self.local().date()}: {note}"
        self.db.update(lead_id, **cols)
        self.db.log("pipeline", f"{lead['name']} -> {stage}")
        if stage == "won":
            mrr_now = self.db.one("SELECT COALESCE(SUM(mrr), 0) m FROM leads WHERE stage = 'won'")["m"]
            self.notifier.alert(f"New client: {lead['name']}", f"MRR is now ${mrr_now:,.0f} of ${self.cfg.offer.mrr_goal:,}")
        return f"{lead['name']} -> {stage}"

    def counts(self) -> dict:
        return {r["stage"]: r["n"] for r in self.db.q("SELECT stage, COUNT(*) n FROM leads GROUP BY stage")}

    def status(self) -> str:
        c, o = self.counts(), self.cfg.offer
        total = sum(c.values())
        g = lambda *ks: sum(c.get(k, 0) for k in ks)
        won = self.db.one("SELECT COUNT(*) n, COALESCE(SUM(mrr), 0) m FROM leads WHERE stage = 'won'")
        contacted = self.db.one("SELECT COUNT(*) n FROM leads WHERE step > 0")["n"]
        replied = self.db.one("SELECT COUNT(*) n FROM leads WHERE reply_kind IN ('interested', 'question', 'other', 'wrong_person')")["n"]
        meetings = g("meeting", "proposal", "won") + self.db.one(
            "SELECT COUNT(*) n FROM leads WHERE stage = 'lost' AND reply_kind != 'not_interested' AND replied_at IS NOT NULL")["n"]
        pct = min(1, won["m"] / o.mrr_goal) if o.mrr_goal else 0
        lines = [f"Goal: {o.target_clients} clients / ${o.mrr_goal:,} MRR.  Now: {won['n']} clients, ${won['m']:,.0f} MRR  "
                 f"[{'#' * round(pct * 20)}{'.' * (20 - round(pct * 20))}] {pct:.0%}",
                 f"Found {total} | emailable {g('audited', 'drafted', 'queued') + contacted} | call list {g('call')} | "
                 f"contacted {contacted} | positive replies {replied}"
                 + (f" ({replied / contacted:.1%})" if contacted else "")
                 + f" | meetings {meetings} | proposals {g('proposal')} | won {won['n']}",
                 f"Waiting: {g('new')} to audit, {g('drafted')} drafts to approve, {g('queued')} queued, "
                 f"{g('contacted')} in sequence, {g('replied')} replies to answer"]
        now = self.clock()
        for box in self.cfg.mailboxes:
            state = " PAUSED" if self.db.get_setting(f"paused:{box.address}") == "1" else ""
            lines.append(f"Mailbox {box.address}{f' (dry run: {self.cfg.outreach.outbox_dir}/)' if not box.smtp_host else ''}: "
                         f"{self.sent_since(box, self.day_start(now))}/{self.limit_today(box, now)} today{state}")
        need = max(0, o.target_clients - won["n"])
        if need:
            if won["n"] >= 2 and contacted:
                per = contacted / won["n"]
                basis = "your own numbers"
            else:
                per = 1 / (BENCH["positive_reply"] * BENCH["meeting"] * BENCH["close"])
                basis = "benchmarks: 1.5% positive replies, 60% of those take a call, 30% of calls close"
            lines.append(f"To land {need} more: ~{round(per * need):,} more businesses emailed ({basis}). Calls and "
                         f"walk-ins from the call list close faster.")
        return "\n".join(lines)

    def call_list(self, limit: int = 20) -> list[dict]:
        return self.db.q(
            "SELECT * FROM leads WHERE phone != '' AND (stage = 'call' OR (stage IN ('contacted', 'finished') AND step >= 2)) "
            "ORDER BY stage = 'call' DESC, score DESC LIMIT ?", (limit,))

    def digest(self, force: bool = False) -> str:
        """Morning summary: what happened yesterday and who to call today."""
        now = self.clock()
        local = self.local(now)
        key = local.strftime("%Y-%m-%d")
        if not force and (local.hour < 7 or self.db.get_setting("digest_day") == key):
            return "not due"
        self.db.set_setting("digest_day", key)
        since = now - 86400
        sent = self.db.one("SELECT COUNT(*) n FROM messages WHERE direction = 'out' AND ts >= ?", (since,))["n"]
        replies = self.db.q("SELECT id, name, reply_kind, phone FROM leads WHERE stage = 'replied'")
        calls = self.call_list(10)
        body = [self.status(), "", f"Last 24h: {sent} emails sent."]
        if replies:
            body += ["", "Replies waiting for you:"] + [f"  #{r['id']} {r['name']} ({r['reply_kind']}) {r['phone']}" for r in replies]
        if calls:
            body += ["", "Call today (best first):"] + [f"  #{r['id']} {r['name']} {r['phone']} - {r['niche']}" for r in calls]
        text = "\n".join(body)
        self.notifier.alert(f"Daily digest {key}", text)
        return text

    def lead_report(self, lead_id: int) -> str:
        lead = self.db.lead(lead_id)
        if lead is None:
            return f"no lead {lead_id}"
        a = json.loads(lead["audit"])
        out = [f"#{lead['id']} {lead['name']} [{lead['stage']}{': ' + lead['stage_reason'] if lead['stage_reason'] else ''}]",
               f"{lead['niche']} | {lead['city']} | {lead['phone']} | {lead['email']} | {lead['website']}",
               f"Google: {lead['rating']} stars, {lead['reviews']} reviews" if lead["reviews"] is not None else None,
               f"Score {lead['score']}: " + "; ".join(json.loads(lead["score_reasons"])),
               "Gaps: " + "; ".join(gaps(a)) if a else None,
               f"Books online with: {', '.join(a.get('booking', []))}" if a.get("booking") else None,
               f"Call opener: {lead['call_script']}" if lead["call_script"] else None]
        for d in self.db.q("SELECT * FROM drafts WHERE lead_id = ? ORDER BY step", (lead_id,)):
            sent = "sent" if d["step"] < lead["step"] else "draft"
            out += ["", f"--- email {d['step'] + 1} ({sent}) | {d['subject']}", d["body"]]
        for m in self.db.q("SELECT * FROM messages WHERE lead_id = ? AND direction = 'in' ORDER BY ts", (lead_id,)):
            out += ["", f"--- their {m['kind']} ({self.local(m['ts']):%b %d %H:%M})", m["body"][:1500]]
        if lead["suggested_reply"]:
            out += ["", "--- suggested reply", lead["suggested_reply"]]
        if lead["notes"]:
            out += ["", "Notes:", lead["notes"]]
        return "\n".join(x for x in out if x is not None)

    def active(self) -> list[dict]:
        return self.db.q(f"SELECT * FROM leads WHERE stage NOT IN ({','.join('?' * len(DONE))}) ORDER BY score DESC", DONE)
