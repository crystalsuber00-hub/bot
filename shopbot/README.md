# shopbot: an automated dropshipping store

One program that runs the whole store: it finds products, builds the website, takes payments, forwards orders to the supplier, emails customers their tracking numbers, refunds cancelled orders, and runs the marketing emails. You check in when it alerts you.

## What runs on its own

| Job | How | When |
|---|---|---|
| **Find products** | Searches your keywords on [CJdropshipping](https://cjdropshipping.com), drops brands/replicas/restricted items, quotes shipping, prices each item, and scores it on profit per sale × demand (how many other stores already sell it) ÷ shipping time. Lists the best few. | daily |
| **Write listings** | Rewrites the supplier's junk titles ("2026 New Hot Sale Free Shipping…") into clean titles, descriptions and SEO text with Claude (if `ANTHROPIC_API_KEY` is set), or a plain cleanup otherwise. | with each new product |
| **Website** | Home page, collection pages per keyword, product pages with Google rich-result data, cart, order tracking, shipping/returns/privacy/terms pages, sitemap, mobile layout, dark mode. | always on |
| **Checkout** | Stripe Checkout (cards, Apple Pay, Google Pay); collects shipping address and phone. Prices always come from the database, never the browser. | per order |
| **Fulfilment** | On payment: confirmation email → re-quotes shipping → places the CJ order → pays it from your CJ wallet. | every 5 min |
| **Tracking** | Emails the tracking number when CJ ships, marks delivery. If CJ cancels: automatic Stripe refund + apology email. | hourly |
| **Price/stock sync** | Re-checks supplier cost and stock; reprices, hides sold-out items, brings restocked ones back. | every 6 h |
| **Finding customers** | Google Shopping feed (`/feeds/google.xml`, also accepted by Meta and Pinterest catalogues), SEO pages + sitemap, abandoned-checkout reminders, weekly new-arrivals email, post-delivery follow-up with recommendations. | continuous / weekly |
| **Alerts to you** | New orders and problems by phone push ([ntfy](https://ntfy.sh)) and email. Dashboard at `/admin`. | as they happen |

### Safety rails (things it deliberately won't do alone)
- **No double orders**: if the connection drops while placing a supplier order, the order goes to review instead of retrying, since the supplier may already have it.
- **No money-losing orders**: if supplier cost + shipping + card fees exceed what the customer paid (supplier raised prices since the sale), or exceed `max_auto_order_cost`, the order waits for you.
- **No spam**: marketing emails go only to people who opted in (double opt-in on the site, or the consent box in Stripe Checkout), every one has an unsubscribe link (one-click), and your business address is in the footer. It never emails scraped or purchased lists; that's illegal in most places and gets your domain blacklisted.
- **No fake discounts**: the crossed-out "was" price is off by default, because showing a former price you never charged is deceptive pricing under FTC and EU rules.

## Try it in 1 minute (fake products, nothing real)
```
pip install -e .
shopbot demo
```
Open http://localhost:8000, buy something with the demo checkout, then watch `/admin` (login `admin` / `demo`): within a minute the order is sent to the fake supplier, then "ships" and "delivers". Emails land in `outbox/` as `.eml` files.

## Going live

### 1. Accounts (one-time, about 1–2 hours)
| Service | What for | Cost |
|---|---|---|
| [CJdropshipping](https://cjdropshipping.com) | Products + fulfilment. Get the API key at *My CJ → Authorization → API*. Add money to your CJ wallet; that's how orders get paid automatically. | free; you pay per order |
| [Stripe](https://stripe.com) | Customer payments. Activate the account (business details, bank). | 2.9% + 30¢ per sale (US) |
| A domain + host | The website. Any host that runs Docker or Python: Render, Railway, Fly.io, a $5 VPS. | ~$15/yr + ~$7/mo |
| Email sending (SMTP) | Order and marketing email. SendGrid, Postmark, Amazon SES, or Gmail with an app password for low volume. Set up SPF/DKIM for your domain in the provider or emails land in spam. | free tier is enough to start |
| [Anthropic API](https://console.anthropic.com) (optional) | Better product copy. A few cents per product. | pay as you go |
| [ntfy](https://ntfy.sh) app (optional) | Push alerts on your phone. | free |

### 2. Configure
```
cp config.shop.example.toml config.shop.toml   # store name, niche keywords, pricing
cp .env.example .env                           # fill in the shopbot keys
```
Pick a **niche**, not "everything": 3–6 related keywords (e.g. dog grooming, pet fountains, no-pull harnesses). Niche stores convert better and rank better.

### 3. Check
```
pip install -e '.[shop-ai]'
set -a; . ./.env; set +a
shopbot check        # tests supplier, Stripe, email and settings; places no orders
shopbot research     # fill the catalogue now instead of waiting for the daily run
```

### 4. Deploy
**Docker (any host):**
```
docker build -t shopbot .
docker run -d --name shop -p 8000:8000 --env-file .env -v $PWD/config.shop.toml:/app/config.shop.toml -v shopdata:/app/data shopbot
```
Put it behind HTTPS (Caddy: `your-domain.com { reverse_proxy localhost:8000 }`, or your host's built-in TLS). **The database is `data/shop.db`: keep that volume and back it up.**

**Bigger traffic:** run the website with `gunicorn -w 4 shopbot.wsgi:app` and the automation with `shopbot worker` (exactly one).

### 5. Connect Stripe
Stripe Dashboard → Developers → Webhooks → *Add endpoint* `https://your-domain.com/webhooks/stripe` with events
`checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.expired`, `charge.refunded`, `charge.dispute.created`.
Copy its signing secret into `STRIPE_WEBHOOK_SECRET`. Do one real purchase of a cheap item to yourself and refund it.

### 6. Go live
Keep `sandbox = true` until a test order looks right in your CJ dashboard, then set `sandbox = false`.

## Getting customers (automated marketing)

| Channel | What the bot does | One-time setup by you |
|---|---|---|
| **Google Shopping (free listings)** | Keeps `/feeds/google.xml` current with prices and stock. | [Merchant Center](https://merchants.google.com): add a feed, *Scheduled fetch*, daily, `https://your-domain/feeds/google.xml`. Verify the domain. |
| **Google search** | SEO product/collection pages with rich-result data, plus a weekly **buying guide** written by Claude (`/guides`). | [Search Console](https://search.google.com/search-console): verify the domain, submit `/sitemap.xml`. |
| **Pinterest** | Pins products to your board, 5 a day spread through the day, best scorers first; re-pins older products after 21 days (with fresh wording when Claude is connected). | See *Social accounts* below. Also add the feed as a Pinterest catalogue. |
| **Facebook Page + Instagram** | Posts one product a day to each with a channel-specific caption ("link in bio" on Instagram). | See *Social accounts* below. Add the feed to Meta Commerce Manager for shoppable posts and ads. |
| **TikTok / Reels / Shorts** | Can't be automated honestly (needs real video). The Monday report gives you **3 video briefs**: hook, shot list and caption for your best sellers. | Film them with your phone and your pet. |
| **Email** | Welcome email with your discount code on signup, best-sellers email 3 days later, abandoned-checkout reminder, weekly new arrivals, post-delivery follow-up, win-back after 60 days. Only to people who opted in. | Create the `WELCOME10` promotion code in Stripe (*Product catalog → Coupons*: 10% off, once, first-time order only). |
| **Paid ads** | Not automated. Adds Google Analytics, Google Ads and Meta Pixel tags (`[tracking]`) with view and purchase events so ad platforms can optimise. | Create the ad accounts and campaigns yourself; the strategy doc has the plan. |

**Knowing what works:** every visit is tagged with the channel it came from (UTM tags, or the referring site), and each order is credited to it. `/admin` shows *Sales by channel*; the **Monday report** to `notify.owner_email` compares the week with the one before, lists best sellers, auto-posts, and the video briefs.

### Social accounts
All optional; each channel switches on once its ids and tokens are set.

**Pinterest**
1. Convert your Pinterest account to a free business account and create a board (e.g. "Dog Grooming Tips & Tools").
2. At [developers.pinterest.com](https://developers.pinterest.com) create an app, request **Standard access** (Trial access can only make pins visible to you), and generate a token with `boards:read`, `pins:read` and `pins:write` scopes.
3. Put `PINTEREST_ACCESS_TOKEN`, `PINTEREST_REFRESH_TOKEN`, `PINTEREST_APP_ID` and `PINTEREST_APP_SECRET` in `.env` (with the refresh token the bot renews access by itself), and the board id in `[social] pinterest_board_id` (the number in the URL from `GET /v5/boards`, or ask me to fetch it).

**Facebook Page and Instagram**
1. Create a Facebook Page for the store, switch your Instagram to a **professional (business)** account, and link it to the Page.
2. At [developers.facebook.com](https://developers.facebook.com) create a *Business* app. As the app's admin, in Graph API Explorer grant `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic` and `instagram_content_publish`, then exchange for a **long-lived Page token** (`/me/accounts` with a long-lived user token).
3. Put it in `FACEBOOK_PAGE_TOKEN`, and set `[social] facebook_page_id` and `instagram_user_id` (`GET /{page-id}?fields=instagram_business_account`).
4. Posting to pages you administer usually works without app review while the app is in development mode. If Meta asks for review or Business Verification, the bot reports the error in `/admin` and keeps going on the other channels.

Instagram only accepts JPEG images. Most CJ product photos are JPEGs; if one isn't, that post fails and the bot moves on.

## Your part (it really is small, but not zero)
- Answer customer emails (replies go to `contact_email`).
- Act on alerts: an order "needs review" (top up the CJ wallet, fix an address, then press *Retry* in `/admin`), a chargeback, a failed refund.
- Look at `/admin` weekly: hide products that don't sell, adjust keywords.

## Commands
```
shopbot run          # website + all automation (what the Docker image runs)
shopbot serve        # website only
shopbot worker       # automation only
shopbot research | sync | fulfill | track | social        # run one job now
shopbot newsletter | guides | report                       # send/publish now, ignoring the weekly schedule
shopbot check        # verify setup
shopbot demo         # try it with fake data
```

## Before you take real money
- Register the business and check sales-tax obligations in your state/country (Stripe Tax can calculate it: enable it, then set `automatic_tax = true`).
- Read the policy pages the site generates (`/pages/...`) and adjust them to what you'll actually honour.
- Order samples of what you list. You're legally responsible for product safety and for claims in your listings.
- Be realistic: shipping from China takes 1–3 weeks, and most new stores need several weeks of traffic before the first sales. Track profit in `/admin`, not just revenue.

## Limits
- One supplier integration (CJdropshipping). The supplier interface in `suppliers.py` is small if you want to add another.
- CJ's API was integrated from its public documentation and tested against mocked responses, not a live CJ account. Run `shopbot check` and a sandbox order before going live, and tell me what breaks.
- The Dockerfile hasn't been built yet (no Docker daemon where this was written); `shopbot run` itself was run and tested.
- The built-in web server suits a small store; use gunicorn (above) when traffic grows.
- No customer accounts or product reviews yet. Discount codes work through Stripe promotion codes.
- Pinterest and Meta integrations follow their public API docs and are tested against mocked responses; run `shopbot social` once after connecting each account and check the post appeared.
