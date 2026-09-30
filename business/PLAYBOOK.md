# Local Website Business: Step-by-Step Playbook

You sell simple, good-looking websites to local businesses that don't have one. Claude finds the leads, builds a free preview site for each one, writes your call list every day, and builds the real site when someone pays. You do about 2 hours a day of calls and follow-ups, and you're the one who signs clients and collects payment.

| Claude does | You do |
|---|---|
| Finds businesses with no website (`leadgen find`) | Fill in `business/config.toml` once |
| Builds a free preview site for each | Make ~20 calls a day from the list |
| Writes your daily call list with scripts (`leadgen today`) | Text the preview link to people who say yes |
| Drafts sales emails that follow the law | Record each outcome (`leadgen mark`) |
| Builds the final site from the client's info | Send the contract, collect payment |
| Updates sites for monthly clients | Answer client questions |

Nothing here guarantees sales. Most calls will be "no." That's normal. What matters is making the calls every day and tracking what works.

---

## Part 1: Set up the business (days 1–3, before your first call)

This is general US information, not legal or tax advice. Rules differ by state and city. Check with your state's website or a local Small Business Development Center (SBDC, free advising) if unsure.

1. **Pick a name.** For example "[Your City] Web Co." Search your state's Secretary of State business database to make sure no one's using it.
2. **Choose a structure.**
   - **Sole proprietorship** is the default when you start selling on your own. No filing is needed if you use your legal name.
   - If you use a different business name, most places require a **DBA** ("doing business as") filing with your county or state. It's usually cheap.
   - An **LLC** separates business debts from personal ones. State fees range from about $50 to $500. It's optional to start. Many people form one once money is coming in.
3. **Get a free EIN** (Employer Identification Number) at irs.gov. It takes about 10 minutes online, and it's always free, so ignore sites that charge for it. You give it to banks and clients instead of your Social Security number.
4. **Open a business bank account.** Many banks and online banks have free ones. Keep business money separate from personal money.
5. **Check whether you need a local business license.** Search "[your city] business license." Some cities require one even for home businesses.
6. **Set up payments with Stripe** (stripe.com; it charges about 2.9% + 30¢ per payment). Create three **Payment Links**:
   - Deposit: 50% of your setup price (e.g., $250)
   - Final payment: the other 50% (e.g., $250)
   - Monthly plan: a **subscription** for your monthly price (e.g., $39/month)
7. **Get a business phone number.** A free Google Voice number keeps your personal number private and works for calls and texts.
8. **Get a business email.** A free Gmail like yourbusinessname@gmail.com is fine to start.
9. **Plan for taxes.** You're self-employed: set aside about **25–30% of profit** for taxes. If you expect to owe $1,000 or more for the year, the IRS expects quarterly estimated payments (Form 1040-ES). Whether website design is subject to sales tax depends on your state, so check your state's revenue department.
10. **Fill in `business/config.toml`** with your name, business name, phone, email, mailing address, city and prices.
11. **Publish the preview sites** so you can text links (see Part 7). Claude can walk you through this.

**Pricing to start** (already in the config):

| | Price | What's included |
|---|---|---|
| Setup | $500 one-time | One-page site, mobile-friendly, their domain connected, one round of changes |
| Monthly | $39/month | Hosting, keeping it online, up to 30 minutes of edits a month |

That's affordable for a small shop and fair for you. Raise the price after your first 3–5 clients.

---

## Part 2: Your 2-hour daily routine

| Time | Task |
|---|---|
| 10 min | Open `business/today.md` (ask Claude to run `python -m leadgen today`). Follow-ups are at the top. |
| 80 min | Calls: follow-ups first, then new leads. Aim for 20 conversations or attempts. |
| 20 min | Text preview links to everyone who said "sure, send it." Send emails for leads with an email address (`python -m leadgen email <id>`). |
| 10 min | Mark every outcome: `python -m leadgen mark <id> <status> "note"` |

- **Best call times:** Tuesday to Thursday, 10:00–11:30 AM and 2:00–4:00 PM. Don't call restaurants during lunch or dinner rush, or salons on Saturday.
- **Walking in** works well too. Show the preview on your phone.
- **Statuses:** `called`, `voicemail`, `interested`, `won`, `lost`, `do_not_contact`. The tool schedules follow-ups automatically: 1 day for interested, 2 for voicemail, 3 for called.

---

## Part 3: First contact (the call)

**Opening** (today.md has a version tailored to each business):

> "Hi, is this the owner or manager of [Business]? My name is [You], I'm local here. I couldn't find a website for you, so I went ahead and built you a free preview website. Could I text you the link? No cost to look."

- **If they say yes:** "Great, what's the best number to text?" Text it within 5 minutes:
  > "Hi, it's [You]. Here's the preview I made for [Business]: [link]. If you like it, I can put it live on your own web address within a week. Happy to answer any questions."
  Then mark it: `leadgen mark <id> interested "texted link"`.
- **If it's voicemail:** leave a short message with your number and "I made you a free website preview." Mark it `voicemail`.
- **If they say no:** "No problem, thanks for your time." Mark it `lost`.
- **If they say don't call again:** mark it `do_not_contact`. The tool will never list them again.

**Common objections:**

| They say | You say |
|---|---|
| "We have Facebook." | "Facebook's great for regulars. A website is what shows up when new customers search Google, and it's something you own." |
| "How much?" | "$500 to set it up, then $39 a month for hosting and small updates. Half up front, half when you're happy with it." |
| "Too expensive." | "I understand. It pays for itself with one or two new customers. Want me to hold the preview for a week while you think?" |
| "Send me info." | "Sure, I'll text the preview now. Can I call you Thursday to hear what you think?" (Set `--followup 2`.) |
| "Do you do Google/SEO?" | "The site comes set up so Google can find it, and I'll connect it to your Google Business listing. I don't promise rankings. Nobody honestly can." |

**Rules to stay legal:**
- **Calls:** calling a business's listed number yourself is fine. Don't use robocalls or auto-dialers.
- **Texts:** only text people who said you could. Never send mass or automated texts; US law (the TCPA) has big fines for that.
- **Emails:** the drafts follow the CAN-SPAM Act. They use an honest subject line, include your mailing address, and include an opt-out. If someone opts out, mark them `do_not_contact` and stop within 10 business days at most.

---

## Part 4: They're interested (the follow-up)

Book a 15-minute call or visit. Pull the preview up together and ask:

1. What are your main services, with prices if you want them shown?
2. Do you have a logo and 3–6 good photos (storefront, work, team)?
3. Are your hours and address correct?
4. What do customers ask most on the phone? (This goes in an FAQ.)
5. Do you already own a web address, like yourbusiness.com?

Then close:

> "Here's how it works: I send you a simple agreement and a link for the $250 deposit. Once that's in, I'll have your site ready to review in 5 business days. When you're happy, you pay the other $250 and it goes live. After that it's $39 a month, cancel anytime."

---

## Part 5: The contract

1. Open `business/CONTRACT.md`, fill in the blanks (client name, price, date), and save it as a PDF or a Google Doc.
2. Get it signed with an e-signature tool. Several well-known ones (Dropbox Sign, DocuSign, Adobe Acrobat Sign) have had free or low-cost plans; check what's current. You can also email the PDF and have them sign and send back a photo.
3. Send the **deposit Payment Link** right after.
4. **Don't start work until the deposit is paid.**

The contract is a starting template, not legal advice. If you can, have a local attorney review it once. Many will do this for a small flat fee, and SBDCs can point you to one.

---

## Part 6: Build the site

1. **Collect their content** with a free Google Form. Ask for: logo, photos, services and prices, hours, about text (3–4 sentences), social links, and anything they want on the site.
2. **Domain:** have the client buy their web address **in their own name** (Cloudflare, Namecheap or Porkbun; usually about $10–20 a year). They own it forever, which builds trust. Have them add you as a user, or walk them through the setup on a quick call.
3. **Open a Claude session** and say: *"Client won: lead id `<id>`. Here's their info: [paste form answers]. Build the final site."* Claude runs `python -m leadgen final <id>` and customizes it with their real content, photos and services.
4. **Send them the draft link** for review. One round of changes is included.

---

## Part 7: Launch and get paid

1. They approve, then you send the **final Payment Link** (the other 50%).
2. Once paid: publish the site and connect their domain. The site gets free HTTPS, which the hosting sets up automatically.
3. **Add the website to their Google Business Profile.** This is one of the most valuable things you do for them; if they don't have a profile, help them make one.
4. Send the **monthly subscription link**, which bills automatically.
5. Ask: "Do you know another business owner who needs a site? I'll give you a month free for each one who signs up." Referrals are the easiest sales you'll ever get.
6. Mark the client `won` in the tracker.

**Hosting:** use GitHub Pages or Cloudflare Pages, both free for simple sites like these. Claude can set up and publish for you. Your cost per client is close to $0, so the monthly fee is nearly all profit.

---

## Part 8: Monthly clients

- Stripe bills them automatically.
- When they ask for an edit (new hours, a new photo), paste the request into a Claude session and it's done in minutes.
- Check `python -m leadgen stats` weekly to see leads, wins, and monthly recurring income.

**What the income could look like:** every client adds $39 a month that keeps coming.

| Clients | Setup money earned | Monthly recurring |
|---|---|---|
| 5 | $2,500 | $195/month |
| 20 | $10,000 | $780/month |
| 50 | $25,000 | $1,950/month |

These are examples, not predictions. The real number depends on how many calls you make and how well the pitch lands.

---

## Part 9: Track and adjust

After two weeks, check `leadgen stats` and tell Claude your numbers: how many calls, how many interested, how many won. If you're getting under about 1 interested per 30 calls, change something: the type of business (e.g., contractors instead of restaurants), the opening line, the price, or try walk-ins. Change one thing at a time so you know what worked.

---

## Commands

```
python -m leadgen find                         # find new leads in your city, build previews
python -m leadgen find --place "Atlanta, GA"   # another city
python -m leadgen today                        # write today's call list: business/today.md
python -m leadgen mark <id> interested "texted link, call Thurs"
python -m leadgen email <id>                   # print a sales email draft
python -m leadgen final <id>                   # build the live version for a paying client
python -m leadgen stats                        # pipeline and income summary
python -m leadgen categories                   # business types you can target
```
