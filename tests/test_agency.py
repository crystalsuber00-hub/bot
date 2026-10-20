import json
import shutil
import subprocess
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import pytest

from clientbot import n8n, niches, proposal, scheduler, sources
from clientbot.__main__ import main
from clientbot.agency import Agency
from clientbot.audit import audit, gaps, pick_email, score
from clientbot.config import Config, Mailbox
from clientbot.copywriter import Copywriter, fallback_reply
from clientbot.outreach import Incoming, Mailer, footer, parse

# Tuesday 10:00 New York: inside the default sending window
T0 = datetime(2026, 10, 20, 10, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
DAY = 86400


class Mail(Mailer):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.out, self.fail = [], ""

    def send(self, box, to, subject, body, in_reply_to=""):
        if self.fail:
            return "" if self.fail == "down" else self.fail
        self.out.append({"box": box.address, "to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to})
        return f"<m{len(self.out)}@test>"


class FakeInbox:
    def __init__(self):
        self.mail = []

    def fetch(self, box, days=3):
        return list(self.mail)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


SITE = {
    "https://glow.test": '<title>Glow</title><meta name="viewport" content="x"><p>Call us to book an appointment</p>'
                         '<a href="mailto:Info@glow.test">mail</a> <img src="logo@2x.png"> owner@gmail.com',
    "https://booked.test": '<p>book</p><script src="https://www.vagaro.com/w.js"></script><p>hi@booked.test</p>',
    "https://chat.test": '<script src="https://widget.podium.com/x.js"></script><p>a@chat.test</p>',
    "https://hidden.test": "<p>Welcome</p>",
    "https://hidden.test/contact": "<p>Write to frontdesk@hidden.test</p>",
}


def fetch(url):
    url = url.rstrip("/")
    return (200, SITE[url], 0.5) if url in SITE else (404, "", 0.2)


def row(name, website="", phone="", email="", reviews=120, rating=4.6, niche="hvac", sid=None):
    return sources.lead("test", sid or name, name, niche, "Austin, TX", website=website, phone=phone, email=email,
                        reviews=reviews, rating=rating)


def make(tmp_path, **over):
    cfg = Config(db_path=str(tmp_path / "c.db"))
    cfg.agency.owner_name, cfg.agency.business_address = "Sam", "1 Main St, Austin, TX"
    cfg.agency.calendar_url = "https://cal.test/sam"
    cfg.mailboxes = [Mailbox(address="sam@agency.test", ramp_start=3, ramp_per_day=2, daily_limit=6)]
    cfg.copywriting.enabled = False
    for k, v in over.items():
        section, key = k.split("__")
        setattr(getattr(cfg, section), key, v)
    clock = Clock()
    ag = Agency(cfg, fetch=fetch, mailer=Mail(cfg), inbox=FakeInbox(), copywriter=Copywriter(enabled=False), clock=clock)
    ag.notifier.alerts = []
    ag.notifier.alert = lambda title, body="", urgent=False: ag.notifier.alerts.append((title, body, urgent))
    return ag, clock


def pipeline(ag, rows):
    ag.add_leads(rows)
    ag.audit(100)
    ag.write(100)
    ag.approve()


# ---- niches, sources ---------------------------------------------------------------------------------------------
def test_niche_overrides_do_not_leak():
    assert niches.get("hvac", {"hvac": {"ticket": 999}}).ticket == 999
    assert niches.get("hvac").ticket == 450
    assert niches.get("dog walker").label == "dog walker businesses"


def test_lead_normalises_phone_and_domain():
    r = sources.lead("x", "1", " Acme ", "hvac", "Austin", website="www.Acme.com/home", phone="+1 512-555-0100")
    assert r["website"] == "https://www.Acme.com/home" and r["domain"] == "acme.com" and r["phone"] == "(512) 555-0100"


def test_csv_import_maps_common_headers(tmp_path):
    p = tmp_path / "leads.csv"
    p.write_text("Company,Site,Phone_1,Email_1,Rating,Reviews Count,Full_Address\n"
                 "Cool Air LLC,coolair.com,512-555-0101,info@coolair.com,4.7,\"1,204\",1 Rd\n,,,,,,\n")
    rows = sources.read_csv(str(p), niche="hvac", city="Austin, TX")
    assert len(rows) == 1
    r = rows[0]
    assert (r["name"], r["domain"], r["email"], r["rating"], r["reviews"]) == ("Cool Air LLC", "coolair.com", "info@coolair.com", 4.7, 1204)
    (tmp_path / "bad.csv").write_text("foo,bar\n1,2\n")
    with pytest.raises(sources.SourceError):
        sources.read_csv(str(tmp_path / "bad.csv"))


def test_google_places_pages_and_skips_closed():
    class Resp:
        def __init__(self, data):
            self.data, self.ok, self.status_code, self.text = data, True, 200, ""

        def json(self):
            return self.data

    class Session:
        def __init__(self):
            self.bodies = []

        def post(self, url, json, timeout, headers):
            self.bodies.append(json)
            if "pageToken" not in json:
                return Resp({"places": [{"id": "a", "displayName": {"text": "A Air"}, "websiteUri": "https://a.test",
                                         "userRatingCount": 80, "rating": 4.5},
                                        {"id": "b", "displayName": {"text": "Gone"}, "businessStatus": "CLOSED_PERMANENTLY"}],
                             "nextPageToken": "t2"})
            return Resp({"places": [{"id": "c", "displayName": {"text": "C Air"}}]})

    cfg = Config()
    cfg.sources.google_api_key = "k"
    s = Session()
    got = sources.GooglePlaces(cfg, s).search(niches.get("hvac"), "Austin, TX")
    assert [g["name"] for g in got] == ["A Air", "C Air"]
    assert s.bodies[0]["textQuery"] == "hvac contractor in Austin, TX" and s.bodies[1]["pageToken"] == "t2"
    with pytest.raises(sources.SourceError):
        sources.GooglePlaces(Config())


# ---- audit -------------------------------------------------------------------------------------------------------
def test_audit_finds_email_booking_and_chat():
    a = audit("https://glow.test", fetch)
    assert a["email"] == "info@glow.test"           # own domain beats gmail; image filenames ignored
    assert a["booking"] == [] and a["chat"] == [] and a["call_to_book"] and a["mobile"]
    assert gaps(a)[0].startswith("there's no way to book online")
    assert audit("https://booked.test", fetch)["booking"] == ["Vagaro"]
    assert audit("https://chat.test", fetch)["chat"] == ["Podium"]
    assert audit("https://hidden.test", fetch)["email"] == "frontdesk@hidden.test"   # found on /contact
    dead = audit("https://dead.test", fetch)
    assert not dead["ok"] and "didn't load" in gaps(dead)[0]


def test_pick_email_ranking():
    assert pick_email({"noreply@x.com", "bob@gmail.com", "hello@x.com", "info@x.com"}, "x.com") == "info@x.com"
    assert pick_email({"bob@gmail.com"}, "x.com") == "bob@gmail.com"
    assert pick_email({"user@domain.com", "a@sentry.io"}, "x.com") == ""


def test_score_rewards_gaps_and_punishes_chains():
    n = niches.get("hvac")
    good, _ = score(row("Cool Air", email="a@b.c"), audit("https://glow.test", fetch), n, 15, 1500, 3.5, ["one hour heating"])
    chained, why = score(row("One Hour Heating & Air", email="a@b.c"), audit("https://glow.test", fetch), n, 15, 1500, 3.5,
                         ["one hour heating"])
    has_chat, _ = score(row("Cool Air", email="a@b.c"), audit("https://chat.test", fetch), n, 15, 1500, 3.5, [])
    assert good >= 90 and chained < 50 and "chain" in why[0] and has_chat < good


# ---- pipeline ----------------------------------------------------------------------------------------------------
def test_add_leads_dedupes_and_ignores_social_pages(tmp_path):
    ag, _ = make(tmp_path)
    assert ag.add_leads([row("A", website="https://glow.test"), row("A2", website="https://www.glow.test/x", sid="2"),
                         row("B", phone="512-555-0100"), row("B2", phone="(512) 555-0100", sid="3"),
                         row("C", website="https://facebook.com/c"), row("D", website="https://facebook.com/d")]) == (4, 2)
    assert ag.db.one("SELECT domain FROM leads WHERE name = 'C'")["domain"] == ""


def test_audit_sorts_leads_into_stages(tmp_path):
    ag, _ = make(tmp_path)
    ag.db.suppress("@booked.test", "manual")
    ag.add_leads([row("Glow", website="https://glow.test"), row("PhoneOnly", phone="5125550101"),
                  row("Tiny", website="https://hidden.test", reviews=3, rating=3.0, niche="hair salon"),
                  row("Booked", website="https://booked.test"), row("Ghost")])
    ag.audit()
    stages = {r["name"]: (r["stage"], r["stage_reason"]) for r in ag.db.q("SELECT * FROM leads")}
    assert stages["Glow"][0] == "audited"
    assert stages["PhoneOnly"] == ("call", "no email found")
    assert stages["Tiny"][0] == "skipped" and "score" in stages["Tiny"][1]
    assert stages["Booked"] == ("skipped", "on the do-not-contact list")
    assert stages["Ghost"][0] == "skipped"
    assert ag.db.one("SELECT email FROM leads WHERE name = 'Glow'")["email"] == "info@glow.test"


def test_write_needs_approval_unless_auto_send(tmp_path):
    ag, _ = make(tmp_path)
    ag.add_leads([row("Glow", website="https://glow.test"), row("PhoneOnly", phone="5125550101")])
    ag.audit()
    assert "wrote 1" in ag.write()
    lead = ag.db.one("SELECT * FROM leads WHERE name = 'Glow'")
    drafts = ag.db.q("SELECT * FROM drafts WHERE lead_id = ? ORDER BY step", (lead["id"],))
    assert lead["stage"] == "drafted" and len(drafts) == 4 and drafts[1]["subject"].startswith("Re: ")
    assert "no way to book online" in drafts[0]["body"] and "https://cal.test/sam" in drafts[2]["body"]
    assert ag.db.one("SELECT call_script FROM leads WHERE name = 'PhoneOnly'")["call_script"].startswith("Hi, is this the owner")
    assert ag.send(T0) == "nothing due"
    assert ag.approve() == 1
    ag2, _ = make(tmp_path / "b", outreach__auto_send=True)
    ag2.add_leads([row("Glow", website="https://glow.test")])
    ag2.audit()
    ag2.write()
    assert ag2.db.one("SELECT stage FROM leads")["stage"] == "queued"


def test_send_window_spacing_and_warmup(tmp_path):
    ag, clock = make(tmp_path)
    pipeline(ag, [row(f"Biz{i}", website="https://glow.test" if i == 0 else "", email=f"o@biz{i}.test", sid=str(i)) for i in range(6)])
    saturday = T0 + 4 * DAY
    assert ag.send(saturday) == "outside the sending window"
    assert ag.send(T0 - 3 * 3600) == "outside the sending window"   # 7am
    t = T0
    assert "email 1" in ag.send(t)
    assert ag.send(t + 10) == "nothing due"                          # min gap between sends
    for i in range(5):
        t += 200
        ag.send(t)
    assert len(ag.mailer.out) == 3                                   # warm-up: day one limit is ramp_start
    ag.send(t + DAY)
    assert len(ag.mailer.out) == 4                                   # day two allows 5
    assert ag.limit_today(ag.cfg.mailboxes[0], T0 + 10 * DAY) == 6   # capped at daily_limit


def test_follow_ups_thread_and_finish(tmp_path):
    ag, _ = make(tmp_path)
    pipeline(ag, [row("Glow", website="https://glow.test")])
    ag.send(T0)
    lead = ag.db.one("SELECT * FROM leads")
    assert lead["stage"] == "contacted" and lead["step"] == 1 and lead["next_send_at"] == T0 + 3 * DAY
    assert ag.send(T0 + DAY) == "nothing due"
    t = T0 + 3 * DAY
    ag.send(t)
    f1 = ag.mailer.out[1]
    assert f1["subject"].startswith("Re: ") and f1["in_reply_to"] == "<m1@test>"
    for wait in (4, 7):
        t += wait * DAY
        ag.send(t)
    lead = ag.db.one("SELECT * FROM leads")
    assert lead["step"] == 4 and lead["stage"] == "finished" and len(ag.mailer.out) == 4
    assert ag.send(t + 30 * DAY) == "nothing due"


def test_suppressed_and_refused_are_never_emailed(tmp_path):
    ag, _ = make(tmp_path)
    pipeline(ag, [row("Glow", website="https://glow.test"), row("Other", email="x@other.test")])
    ag.db.suppress("x@other.test", "manual")
    for i in range(2):
        ag.send(T0 + i * 300)
    assert [m["to"] for m in ag.mailer.out] == ["info@glow.test"]
    assert ag.db.one("SELECT stage FROM leads WHERE name = 'Other'")["stage"] == "unsubscribed"
    ag2, _ = make(tmp_path / "b")
    pipeline(ag2, [row("Glow", website="https://glow.test")])
    ag2.mailer.fail = "refused"
    ag2.send(T0)
    assert ag2.db.one("SELECT stage FROM leads")["stage"] == "bounced" and ag2.db.suppressed("info@glow.test")


def test_failed_send_requeues_and_alerts_after_three(tmp_path):
    ag, _ = make(tmp_path)
    pipeline(ag, [row("Glow", website="https://glow.test")])
    ag.mailer.fail = "down"
    for i in range(3):
        assert "failed" in ag.send(T0 + i * 300)
    assert ag.db.one("SELECT stage, mailbox FROM leads") == {"stage": "queued", "mailbox": ""}
    assert any("Can't send" in a[0] for a in ag.notifier.alerts)


def reply(sender, body, subject="Re: missed calls", auto=False, bounce=False, mentions=(), mid=None):
    reply.n = getattr(reply, "n", 0) + 1
    return Incoming(mid or f"<r{reply.n}@x>", sender, subject, body, auto, bounce, list(mentions))


def test_positive_reply_stops_sequence_and_alerts(tmp_path):
    ag, clock = make(tmp_path)
    pipeline(ag, [row("Glow", website="https://glow.test")])
    ag.send(T0)
    ag.inbox_reader.mail = [reply("info@glow.test", "Yes, send the video. How much?")]
    assert "interested" in ag.inbox()
    lead = ag.db.one("SELECT * FROM leads")
    assert lead["stage"] == "replied" and lead["next_send_at"] is None and "cal.test" in lead["suggested_reply"]
    assert ag.notifier.alerts[-1][2] is True
    assert ag.inbox() == "no new replies"                            # same message isn't handled twice
    assert ag.send(T0 + 3 * DAY) == "nothing due"


def test_colleague_reply_matches_by_domain(tmp_path):
    ag, _ = make(tmp_path)
    pipeline(ag, [row("Glow", website="https://glow.test")])
    ag.send(T0)
    ag.inbox_reader.mail = [reply("maria@glow.test", "I'm the owner, tell me more")]
    ag.inbox()
    assert ag.db.one("SELECT stage FROM leads")["stage"] == "replied"


def test_unsubscribe_no_and_out_of_office(tmp_path):
    ag, _ = make(tmp_path)
    pipeline(ag, [row("A", email="a@a.test"), row("B", email="b@b.test"), row("C", email="c@c.test")])
    for i in range(3):
        ag.send(T0 + i * 300)
    ag.inbox_reader.mail = [reply("a@a.test", "Please remove me from your list"),
                            reply("b@b.test", "No thanks, we're all set."),
                            reply("c@c.test", "I am out of the office until Monday", auto=True)]
    ag.inbox()
    st = {r["name"]: r for r in ag.db.q("SELECT * FROM leads")}
    assert st["A"]["stage"] == "unsubscribed" and ag.db.suppressed("a@a.test")
    assert st["B"]["stage"] == "lost" and ag.db.suppressed("someone-else@b.test")
    assert st["C"]["stage"] == "contacted" and st["C"]["next_send_at"] >= T0 + 3 * DAY


def test_bounces_mark_lead_and_pause_mailbox(tmp_path):
    ag, clock = make(tmp_path)
    ag.cfg.mailboxes[0].ramp_start = ag.cfg.mailboxes[0].daily_limit = 100
    pipeline(ag, [row(f"B{i}", email=f"o@b{i}.test", sid=str(i)) for i in range(25)])
    t = T0
    for _ in range(25):
        ag.send(t)
        t += 200
    clock.t = t
    ag.inbox_reader.mail = [reply("mailer-daemon@google.com", f"could not deliver to o@b{i}.test", subject="Undeliverable",
                                  bounce=True, mentions=[f"o@b{i}.test"]) for i in range(2)]
    assert "bounced" in ag.inbox()
    assert ag.db.one("SELECT stage FROM leads WHERE email = 'o@b0.test'")["stage"] == "bounced"
    assert ag.send(t) == "sam@agency.test: paused"
    assert any("Paused" in a[0] for a in ag.notifier.alerts)


def test_mark_won_counts_toward_goal_and_status(tmp_path):
    ag, _ = make(tmp_path)
    pipeline(ag, [row("Glow", website="https://glow.test"), row("Cuts", email="o@cuts.test", niche="hair salon")])
    assert ag.mark(1, "won") == "Glow -> won"
    ag.mark(2, "won")
    s = ag.status()
    assert "2 clients, $4,000 MRR" in s and "40%" in s                # hvac $2,500 + salon tier $1,500
    assert ag.mark(99, "won") == "no lead 99"


def test_call_list_and_digest(tmp_path):
    ag, clock = make(tmp_path)
    pipeline(ag, [row("PhoneOnly", phone="5125550101"), row("Glow", website="https://glow.test")])
    assert [r["name"] for r in ag.call_list()] == ["PhoneOnly"]
    clock.t = T0
    text = ag.digest()
    assert "Call today" in text and "PhoneOnly" in text and ag.digest() == "not due"


def test_refill_searches_next_combination(tmp_path):
    ag, _ = make(tmp_path, targeting__niches=["hvac", "plumber"], targeting__cities=["Austin, TX"])
    ag._source = sources.Demo()
    assert "hvac in Austin" in ag.refill()
    assert "plumber in Austin" in ag.refill()
    assert ag.refill() == "all searches done" and ag.notifier.alerts


def test_scheduler_runs_jobs_once_per_interval(tmp_path):
    ag, _ = make(tmp_path)
    ag._source = sources.Demo()
    assert "send" in scheduler.run_due(ag, now=T0)
    assert scheduler.run_due(ag, now=T0 + 1) == []


# ---- email -------------------------------------------------------------------------------------------------------
def test_footer_has_address_and_opt_out():
    cfg = Config()
    cfg.agency.business_address = "1 Main St"
    f = footer(cfg)
    assert "1 Main St" in f and "won't email you again" in f


def test_real_mailer_writes_outbox_with_threading(tmp_path):
    cfg = Config()
    cfg.agency.business_address = "1 Main St"
    cfg.outreach.outbox_dir = str(tmp_path / "out")
    mid = Mailer(cfg).send(Mailbox(address="me@a.test"), "you@b.test", "Re: hi", "Body", in_reply_to="<x@a.test>")
    raw = next((tmp_path / "out").iterdir()).read_text()
    assert mid.endswith("@a.test>") and "In-Reply-To: <x@a.test>" in raw and "List-Unsubscribe" in raw and "1 Main St" in raw


def test_parse_strips_quotes_and_detects_bounces():
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = "Bob <Bob@Glow.test>", "Re: missed calls", "<1@x>"
    m.set_content("Sounds good, call me.\n\nOn Tue, Oct 20, 2026 at 10:00 AM Sam <sam@agency.test> wrote:\n> Hi there")
    p = parse(bytes(m))
    assert p.sender == "bob@glow.test" and p.body == "Sounds good, call me." and not p.bounce
    b = EmailMessage()
    b["From"], b["Subject"] = "Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Delivery Status Notification (Failure)"
    b.set_content("Your message wasn't delivered to info@glow.test because the address couldn't be found.")
    p = parse(bytes(b))
    assert p.bounce and p.auto and p.mentions == ["info@glow.test"]
    o = EmailMessage()
    o["From"], o["Auto-Submitted"] = "x@y.test", "auto-replied"
    o.set_content("Away")
    assert parse(bytes(o)).auto


def test_fallback_reply_classification():
    ctx = {"calendar_url": "https://cal.test"}
    assert fallback_reply("Please stop emailing me", ctx)["kind"] == "unsubscribe"
    assert fallback_reply("Not interested, thanks", ctx)["kind"] == "not_interested"
    assert fallback_reply("How much does it cost?", ctx)["kind"] == "interested"
    assert fallback_reply("I'm no longer with the company", ctx)["kind"] == "wrong_person"


def test_claude_reply_cannot_override_opt_out():
    cw = Copywriter(enabled=False)
    cw._json = lambda *a, **k: {"kind": "interested", "summary": "", "suggested_reply": "Great!"}
    assert cw.reply({"name": "X"}, "", "Interested? No. Unsubscribe me.", {"owner_name": "Sam"})["kind"] == "unsubscribe"


def test_claude_sequence_falls_back_when_malformed():
    cw = Copywriter(enabled=False)
    cw._json = lambda *a, **k: {"subject": "s", "emails": ["only one"], "call_opener": "x"}
    lead = row("Glow", website="https://glow.test")
    ctx = {"owner_name": "Sam", "agency_name": "A", "calendar_url": "", "price": 2500, "breakeven": 6, "guarantee": "G"}
    seq = cw.sequence(lead, niches.get("hvac"), audit("https://glow.test", fetch), ctx)
    assert len(seq["emails"]) == 4


# ---- proposal ----------------------------------------------------------------------------------------------------
def test_proposal_has_math_and_escapes(tmp_path):
    ag, _ = make(tmp_path)
    pipeline(ag, [row("<b>Glow</b> & Co", website="https://glow.test")])
    ag.mark(1, "meeting")
    path = proposal.write(ag, 1, out_dir=str(tmp_path / "p"))
    page = open(path).read()
    assert "$2,500" in page and "&lt;b&gt;Glow" in page and "<b>Glow</b>" not in page
    assert ">6<" in page                                              # 2500 / 450 per job -> 6 to break even
    assert ag.db.lead(1)["stage"] == "proposal"


# ---- n8n deliverable -----------------------------------------------------------------------------------------------
def client_file(tmp_path, **over):
    base = {"name": "Bob's Plumbing & Drain", "niche": "plumber", "twilio_number": "+15125550199", "owner_mobile": "+15125550111",
            "main_phone": "+15125550100", "booking_url": "", "services": ["Drain clearing: $149"], "mode": "forward"}
    base.update(over)
    lines = ["[business]"] + [f"{k} = {json.dumps(v)}" for k, v in base.items()] + ["[n8n]", 'n8n_url = "https://n8n.test/"']
    p = tmp_path / "client.toml"
    p.write_text("\n".join(lines))
    return str(p)


def check_workflow(wf):
    names = [n["name"] for n in wf["nodes"]]
    assert len(names) == len(set(names))
    for src, c in wf["connections"].items():
        assert src in names and all(t["node"] in names for t in c["main"][0])
    return {n["name"]: n for n in wf["nodes"]}


def test_build_workflows(tmp_path):
    files = n8n.build(client_file(tmp_path), str(tmp_path / "out"))
    assert [f.rsplit("/", 1)[1] for f in files] == ["front-desk.json", "lead-reply.json", "SETUP.md"]
    fd = check_workflow(json.load(open(files[0])))
    assert fd["Call comes in"]["parameters"]["path"] == "bob-s-plumbing-drain-voice"
    assert fd["Send reply"]["parameters"]["from"] == "+15125550199"
    req_js = fd["Build conversation"]["parameters"]["jsCode"]
    assert "claude-opus-5-5" in req_js and "Bob's Plumbing" in req_js
    assert "two times that suit them" in req_js                       # no booking link -> collect details and hand off
    lr = check_workflow(json.load(open(files[1])))
    assert lr["New web lead"]["parameters"]["responseMode"] == "onReceived"
    setup = open(files[2]).read()
    assert "https://n8n.test/webhook/bob-s-plumbing-drain-sms" in setup and "10DLC" in setup


def test_dial_mode_and_validation(tmp_path):
    fd = n8n.front_desk(n8n.load_client(client_file(tmp_path, mode="dial")))
    answer = next(n for n in fd["nodes"] if n["name"] == "Answer call")["parameters"]["jsCode"]
    assert "<Dial" in answer and "https://n8n.test/webhook/bob-s-plumbing-drain-voice-done" in answer
    with pytest.raises(ValueError, match="twilio_number"):
        n8n.load_client(client_file(tmp_path, twilio_number=""))
    with pytest.raises(ValueError, match="mode"):
        n8n.load_client(client_file(tmp_path, mode="ring"))


NODE = shutil.which("node")
HARNESS = r"""
const nodes = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const store = {};
const outputs = {};
const run = (name, input) => {
  const fn = new Function('$input', '$', '$getWorkflowStaticData', nodes[name]);
  const items = input.map(j => ({ json: j }));
  const res = fn({ first: () => items[0], all: () => items }, n => ({ first: () => outputs[n][0] }), () => store);
  outputs[name] = res;
  return res;
};
const log = [];
const sms = (from, Body, claude) => {
  const built = run('Build conversation', [{ body: { From: from, Body } }]);
  if (!built.length) return log.push({ Body, skipped: true });
  log.push({ Body, messages: built[0].json.request.messages.length });
  log.push(run('Save and reply', [claude])[0].json);
};
run('Text back missed callers', [{ missed: true, caller: '+15550001111' }]);
const again = run('Text back missed callers', [{ missed: true, caller: '+15550001111' }]);
log.push({ textbacks_second_call: again.length });
const ok = { stop_reason: 'end_turn', content: [{ type: 'thinking', thinking: '', signature: 's' },
  { type: 'text', text: JSON.stringify({ reply: 'We have openings Tuesday.', handoff: false, handoff_reason: '' }) }] };
sms('+15550001111', 'Do you have time Tuesday?', ok);
sms('+15550001111', 'ok', { error: { message: 'timeout' } });
sms('+15550001111', 'STOP', ok);
sms('+15550001111', 'hello?', ok);
log.push({ stored: store.convos['+15550001111'].messages.length, firstRole: store.convos['+15550001111'].messages[0].role,
           kept: JSON.stringify(store.convos['+15550001111'].messages[3].content).includes('signature') });
console.log(JSON.stringify(log));
"""


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_front_desk_code_runs(tmp_path):
    wf = n8n.front_desk(n8n.load_client(client_file(tmp_path)))
    code = {n["name"]: n["parameters"]["jsCode"] for n in wf["nodes"] if n["type"] == "n8n-nodes-base.code"}
    out = subprocess.run([NODE, "-e", HARNESS], input=json.dumps(code), text=True, capture_output=True, check=True).stdout
    log = json.loads(out)
    assert log[0] == {"textbacks_second_call": 0}                    # one text-back per caller per 15 minutes
    assert log[1]["messages"] == 3                                    # seeded missed-call turn + their text
    assert log[2]["text"] == "We have openings Tuesday." and log[2]["handoff"] is False
    assert log[4]["handoff"] is True and "text you back" in log[4]["text"]   # API error -> safe reply + owner alert
    assert log[5] == {"Body": "STOP", "skipped": True} and log[6] == {"Body": "hello?", "skipped": True}
    assert log[7]["firstRole"] == "user" and log[7]["kept"] and log[7]["stored"] == 6


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_generated_js_parses_even_with_quotes_in_names(tmp_path):
    path = client_file(tmp_path, name='Jo\'s "Best" Spa </script>', booking_url="https://book.test/?a=1&b='2'")
    for wf in (n8n.front_desk(n8n.load_client(path)), n8n.lead_reply(n8n.load_client(path))):
        for n in wf["nodes"]:
            if n["type"] == "n8n-nodes-base.code":
                src = "async function f($input, $, $getWorkflowStaticData) {\n" + n["parameters"]["jsCode"] + "\n}"
                r = subprocess.run([NODE, "--check", "-"], input=src, text=True, capture_output=True)
                assert r.returncode == 0, (n["name"], r.stderr)


# ---- CLI ---------------------------------------------------------------------------------------------------------
def test_cli_demo_and_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["-c", "none.toml", "demo"]) == 0
    out = capsys.readouterr().out
    assert "5. Send:" in out and "Goal: 4 clients" in out
    assert main(["-c", "none.toml", "--db", "demo-clients.db", "status"]) == 0
    assert main(["-c", "none.toml", "--db", "demo-clients.db", "calls"]) == 0
    assert main(["-c", "none.toml", "--db", "demo-clients.db", "export", "leads.csv"]) == 0
    assert (tmp_path / "leads.csv").read_text().startswith("id,name")
