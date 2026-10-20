# clientbot: land local clients for an AI front desk (n8n + Claude)

One program for the whole business: it finds local businesses that lose bookings to missed calls, checks their
websites, writes each one a personal cold email sequence, sends it safely from your mailboxes, catches replies, and
writes the proposal. When you sign a client, it builds their n8n workflows (missed-call text-back, a 24/7 texting
assistant powered by Claude, and instant replies to web leads) from a one-page profile.

**Read [PLAYBOOK.md](PLAYBOOK.md) first.** It's the 30-day plan: offer, pricing, scripts, and the honest math on how many
businesses you need to contact to sign 4.

## What runs on its own

| Job | How | When |
|---|---|---|
| **Find businesses** | Google Places API (ratings, review counts, websites) or OpenStreetMap (free), per niche and city. Or import a CSV from Outscraper/Apollo. Drops chains and franchises; dedupes by website and phone. Searches the next niche x city when the pipeline runs low. | every 6 h, when needed |
| **Audit each website** | Can customers book online (detects Vagaro, Booksy, Jane, Zocdoc, Housecall Pro, Jobber, ServiceTitan and ~30 more)? Is there a chat or "text us" widget (Podium, Birdeye, Weave...)? Finds the best contact email (checks /contact pages). | every 30 min |
| **Score** | 0-100: reachable, has the gap, busy and independent (review count), high-value jobs. Each lead shows why. | with the audit |
| **Write** | 4-email sequence per lead, opening with something true from their site. Claude when `ANTHROPIC_API_KEY` is set (it's told never to invent facts or clients), templates otherwise. Plus a phone opener. | every 30 min |
| **Send** | From 1-3 mailboxes, weekdays in business hours, spaced 2.5 min apart, with a warm-up ramp (10/day rising to 40). Follow-ups thread under the first email. | continuous |
| **Replies** | Reads your inboxes over IMAP. Interested -> stops the sequence, alerts your phone with a suggested reply. "No" / "remove me" -> never emailed again (whole company). Out-of-office -> waits. Bounces -> marked, and the mailbox pauses itself if bounces pass 5%. | every 10 min |
| **Daily digest** | Pipeline, progress to $10k MRR, replies waiting, and today's call list. | 7am |
| **Proposals** | One-page HTML proposal per prospect: their gaps, the fix, ROI math, price, guarantee, calendar link. | `clientbot proposal ID` |
| **Delivery** | `clientbot agent clients/x.toml` -> `front-desk.json`, `lead-reply.json` and a step-by-step `SETUP.md` for that client. | per client |

### Safety rails
- **Legal cold email.** Every email carries your postal address and a reply-to-opt-out line, added by the code (not the AI),
  plus a `List-Unsubscribe` header. Opt-outs are permanent. US businesses only by default; don't use it for Canada (CASL)
  or the EU/UK (GDPR/PECR) without checking the rules.
- **No spam behaviour.** Low daily limits per mailbox, warm-up ramp, business hours only, auto-pause on bounces, plain
  text, no tracking pixels or links in the first email.
- **You stay in control.** `auto_send = false` (the default) holds every sequence until you approve it. Read the first
  20-30, fix the config if something's off, then let it run.
- **The AI can't make things up.** It only gets facts from the audit, is told to use nothing else, and website text is
  passed as data, not instructions.
- **For clients:** the assistant says it's automated, answers only from the facts in their profile, never confirms
  appointments or takes payment details, keeps medical clients' patients from sharing health details, honours STOP, and
  texts the owner whenever a person is needed.

## Try it (1 minute, nothing real)
```
pip install -e .
clientbot demo
```
Finds fake med spas and HVAC companies, audits fake websites, writes and "sends" sequences (to `outbox-demo/`),
simulates a reply, writes a proposal (`proposals-demo/`), and prints the pipeline.

## Going live

### 1. Accounts (day 1, about 2 hours)
| Service | What for | Cost |
|---|---|---|
| 1-2 extra domains (e.g. getfrontdeskai.com) | Cold email, kept away from your main domain | ~$12/yr each |
| Google Workspace or Microsoft 365 | 1-3 mailboxes on those domains. Set SPF, DKIM, DMARC (the provider walks you through it; `clientbot check` verifies). | ~$7/mailbox/mo |
| [Cal.com](https://cal.com) or Calendly | Your 15-minute call link | free |
| [Anthropic API](https://console.anthropic.com) | Personalised emails, reply triage, and your clients' assistants | pay as you go |
| Google Places API (optional) | Ratings and review counts, best coverage for trades | Google bills per request; check their pricing page |
| [ntfy](https://ntfy.sh) app (optional) | Reply alerts on your phone | free |
| n8n + Twilio | Delivery. Set up your own demo front desk in week 1 (see the playbook). | n8n cloud or ~$6-20/mo VPS; Twilio per number + per text |

**Warm up new mailboxes for 2 weeks before cold email** (send and reply to real conversations, or use a warm-up
service). The ramp settings start low either way.

### 2. Configure
```
cp config.agency.example.toml config.agency.toml    # your details, niches, city, mailboxes
cp .env.example .env                                # mailbox app passwords, API keys
pip install -e '.[agency-ai]'
set -a; . ./.env; set +a
clientbot check
```

### 3. First batch, by hand
```
clientbot find                  # niches x cities from the config
clientbot audit
clientbot write
clientbot drafts                # read them
clientbot approve               # or: clientbot approve 12 15 19
clientbot calls                 # phone list with what to say
```

### 4. Run it
```
clientbot run                   # worker: everything above on a schedule
```
Docker / a $5 VPS / your laptop all work; it only needs to run during your sending hours. Keep `data/clients.db` backed up.

### 5. Work the pipeline
```
clientbot status                # funnel, MRR vs goal, today's sending
clientbot leads --stage replied
clientbot lead 42               # audit, every email, their reply, suggested answer
clientbot mark 42 meeting --note "Thu 2pm, owner Maria"
clientbot proposal 42 --missed-per-week 15
clientbot mark 42 won --mrr 2500
```

### 6. Deliver
```
mkdir clients && cp client.example.toml clients/glow.toml   # fill in from the kickoff call
clientbot agent clients/glow.toml                          # -> builds/glow-med-spa/
```
Follow `builds/<client>/SETUP.md`: import into n8n, add the Anthropic/Twilio/SMTP credentials, point the Twilio number's
webhooks at n8n, turn on conditional call forwarding, register the number for A2P 10DLC, run the 5 tests.

## Commands
```
clientbot demo | check | run | status | digest
clientbot find [--niche hvac --city "Round Rock, TX" --source google|osm]
clientbot import leads.csv [--niche dental --city "Austin, TX"]
clientbot audit | write | drafts | approve [ids] | send | inbox
clientbot leads [--stage call] | lead ID | calls | mark ID meeting|proposal|won|lost [--mrr N --note ...]
clientbot proposal ID [--missed-per-week N --recover-rate 0.3]
clientbot agent clients/x.toml
clientbot suppress someone@x.com | @domain.com
clientbot resume                 # after a bounce pause, once you've cleaned the list
clientbot export leads.csv
```

## Limits (honest)
- OpenStreetMap has no reviews or ratings and misses many trades; use Google Places or a CSV for HVAC/plumbing/roofing.
- Website emails are often generic (info@). Fine for small businesses, where the owner reads it. If you buy a data tool
  (Apollo, Outscraper's email finder), import its CSV; verify emails before sending to keep bounces under 5%.
- The bot only sends from mailboxes you own and stops at the limits you set. It can't make a new domain trusted faster:
  that's the warm-up.
- The generated workflows were imported and run in n8n 2.41 with simulated Twilio calls/texts, a simulated web lead
  and a stand-in for the Claude API: text-backs, conversation memory, STOP, handoffs and lead replies all behaved as
  designed. Real Twilio and Claude calls weren't part of that run, so do the 5 tests in `SETUP.md` on each client
  before going live. Use n8n 1.x or newer (2.x tested). The texting assistant sends booking links; real-time calendar booking (Cal.com,
  Google Calendar, Jane, Vagaro APIs) is an upgrade you'd add per client.
- Dental and medical clients: their patients' texts can contain health information. Use Twilio's and Anthropic's
  HIPAA offerings (both need a signed BAA) and self-hosted n8n, or keep those clients on booking-only messages.
