# 30-day playbook: 4 clients at $2,500/month

The plan: one person sells and sets up an "AI front desk" for local businesses. It texts back every missed call,
answers questions by text 24/7, replies to web leads in under a minute, and hands anything tricky to the owner.
clientbot does the prospecting and most of the follow-up; you do the calls, demos and setups.

## The honest math first

- **Cold email needs volume.** Typical local-business cold email: 1-3% of businesses reply positively, about half of
  those take a call, and a quarter to a third of calls close. That's roughly **300-400 businesses emailed per client**,
  so ~1,500 for four. `clientbot status` shows this with your real numbers once you have them.
- **New mailboxes need ~2 weeks of warm-up** before they can send 40/day without landing in spam. Three mailboxes at
  40/day is about 120/day, or ~1,500 in the last two and a half weeks of the month. That's just enough, which is why
  you don't rely on email alone in month one.
- **The phone and the front door are faster.** Calling and walking in work from day one, and local owners buy from people
  they've spoken to. Plan on 40-60 calls a day in week one while the mailboxes warm up. The call list (`clientbot calls`)
  gives you the businesses, what's missing on their site, and an opener.
- **$2,500/month fits high-value niches**: med spas, dental, HVAC, plumbing, roofing, where one extra job a week covers
  it. Salons and nail studios rarely pay that; the bot defaults them to $1,500 (change it in config), or skip them
  in month one.
- **Margins:** Twilio number and texts, Claude API usage, and your share of an n8n server come to roughly $20-100 per
  client per month at small-business volumes, so 90%+ margins at $2,500. Your time is the real cost: ~6-10 hours to
  set up a client, then 1-2 hours a month.

Four clients in 30 days is possible but aggressive. Two signed and two in proposal by day 30 is a good month; the
pipeline keeps producing in month two.

## The offer

> **AI front desk for [niche].** Every missed call gets a text back in seconds. Customers can text questions any time
> and get answers about your services, prices and hours, plus your booking link. Website and ad leads get a reply in
> under a minute. Anything it can't handle goes straight to your phone. Done for you in about a week.
> **$2,500/month, month to month. If it hasn't recovered [break-even] jobs in 30 days, month two is free.**

- The guarantee uses break-even (price / average job value), which the bot computes per niche. Keep it, it's what closes.
- Optional for your first 1-2 clients: waive the setup fee or discount month one in exchange for a testimonial and
  permission to use their numbers as a case study. Don't discount the monthly price long-term.

## Week 1: set up and start calling

**Day 1 (setup, ~4 hours)**
1. Buy 1-2 extra domains; create 2-3 Google Workspace mailboxes; set SPF/DKIM/DMARC; start warming them up.
2. Cal.com 15-minute event. A one-page website on your main domain (what it does, price, guarantee, your face).
3. `cp config.agency.example.toml config.agency.toml`, fill it in, `clientbot check`.
4. **Build your own demo front desk.** Make `clients/demo.toml` for a fake "Demo Med Spa" (or your top niche) with
   your own mobile as `owner_mobile`, build it with `clientbot agent`, and set it up in n8n + Twilio. This is your sales
   tool: "Call this number and don't answer... see the text? Now ask it a question." Nothing sells it like that.
   Start the Twilio A2P 10DLC registration today (it takes days).

**Day 2**
```
clientbot find && clientbot audit && clientbot write
clientbot drafts      # read 20-30; fix niches/pain wording in config if needed
clientbot calls
```

**Days 2-5: calls and walk-ins (2-3 hours a day)**
Best times: 10-11:30am and 2-4pm, Tuesday to Thursday. Avoid Monday mornings and lunch.

Opener (the bot writes one per lead):
> "Hi, is this the owner? This is [name], I'm local and I help [med spas] stop losing bookings to missed calls. Can I
> take 30 seconds to explain, and you tell me if it's not for you?"

Then:
> "When you're with a client and the phone rings, what happens to that call? ... Most go to voicemail, and most people
> don't leave one; they call the next place. What I set up texts them back within seconds from your number, answers
> their questions and sends your booking link. Can I show you? Call this number and don't let anyone answer."

Goal of the call: a 15-minute demo (Zoom or in person), not a sale. Book it on the spot.

Walk-ins (salons, med spas, clinics): go mid-afternoon, ask for the owner or manager, show the demo on your phone,
leave a one-page printout of their proposal (`clientbot proposal ID`).

## Weeks 2-4: email on, demos, closes

- Day 8 onwards: `clientbot approve` and `clientbot run`. Sending ramps itself up. Set `auto_send = true` once the drafts
  are consistently good.
- **Replies come to your phone** with a suggested answer. Answer within an hour during business hours; speed matters
  as much for you as for them.
- **"Send me the video"**: record a 2-minute Loom the same day:
  1. Their website on screen: "This is [business]. Right now if someone calls while you're busy..."
  2. Call your demo number, let it ring out, show the text-back arriving.
  3. Text it a question a customer of theirs would ask; show the answer.
  4. "I'd set this up with your services, prices and booking link in about a week. $2,500 a month, and if it hasn't
     recovered [N] jobs in the first 30 days, month two is free. Here's my calendar."
- Keep working the call list every morning; follow up by phone with everyone who opened a conversation.

### The 15-minute demo call
1. **Their numbers (5 min).** "Roughly how many calls a day? What happens when nobody can pick up? After hours? What's a
   typical [job/treatment] worth? How do web enquiries get answered, and how fast?"
2. **Demo (5 min).** Live: missed call, text-back, a question, the booking link, a handoff to the owner's phone.
3. **Math and offer (5 min).** "At [their missed calls], if it saves even [break-even] a month it's paid for. Month to
   month, and the guarantee." Then: "Want me to start the setup this week?"
After the call: `clientbot proposal ID --missed-per-week N` with the numbers they gave you; send it within the hour.

### Objections
- **"We have a receptionist."** "Great, this covers the times they can't: lunch, busy hours, after 5, weekends. It
  only steps in when a call isn't answered."
- **"Customers will hate a robot."** "It says it's an assistant up front, it only answers what you've approved, and
  the moment someone wants a person it texts you. Want to try to trip it up?" (hand them your demo number)
- **"Too expensive."** "What's one [job] worth? ... So it pays for itself at [N] a month, and if it doesn't do that in
  30 days, the next month's free."
- **"Send me some info."** "Sure; I'll send a one-page proposal with your numbers. Can we put 10 minutes on Thursday to
  go over it?" (book it before hanging up)
- **"Let me think about it."** "Of course. What would you need to see to say yes?" Then offer to start with just the
  missed-call text-back, same guarantee.

## Closing and onboarding (days 1-7 of each client)
1. Send a simple agreement (scope, price, month to month, 30 days' notice, the guarantee) and a Stripe payment link or
   invoice for month one. Collect payment before setup.
2. Kickoff (30 min): services and prices, hours, policies, FAQ, booking link, who gets handoff texts. Fill in
   `clients/<name>.toml` during the call.
3. `clientbot agent clients/<name>.toml`, then follow `SETUP.md`: Twilio number + A2P registration, n8n import and
   credentials, call forwarding, web-form webhook, the 5 tests. `clientbot mark ID won`.
4. Go-live text to the owner with what to expect. Check every conversation for the first week; tighten the profile.

## Keeping them (this is where $10k/month is won)
- **Monthly report on the 1st**: missed calls caught, conversations, booking links sent, leads answered in under a
  minute, anything you improved. Numbers, not adjectives.
- **Ask for referrals at day 30** when the first report lands: owners know other owners.
- **Upsells later**: review requests after appointments, reactivation texts to old customers, real-time calendar booking.

## Rules that keep you out of trouble
- Cold email: US businesses, truthful subject and sender, your postal address, honour every opt-out (the bot enforces
  all of this). Don't buy scraped consumer lists.
- Calls: dial by hand, no robocalls or prerecorded messages, never cold-text businesses, and stop if asked.
- Client texting: register every Twilio number for A2P 10DLC; only text people who called or texted the business or
  filled in a form with consent wording; the assistant always says it's automated.
- Health businesses: patients' texts may contain health information. Use HIPAA-eligible services with signed BAAs
  (Twilio, Anthropic), self-host n8n, and keep the assistant on scheduling, not health questions.
