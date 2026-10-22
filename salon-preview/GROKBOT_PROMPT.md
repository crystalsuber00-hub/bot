# Grokbot prompts for salon preview sites

Two prompts. Paste **Prompt 1** to build a new preview site. Paste **Prompt 2** to redo the photos and pricing on a site that already exists.

The model that runs these needs web search, web fetch, and a way to run code (to download and crop photos). Without those tools it can't retrieve anything and will guess. For a closer visual match, attach `salon-preview/golden-barber/index.html` as the reference design.

---

## Prompt 1: Build a preview site

```
Build a preview website I can show to a local business to pitch them a new site.

BUSINESS: [paste Google Maps / share.google link, or business name + city]

Work through these steps in order. Only use facts you verified online. Never invent prices, hours, reviews, quotes or services.

STEP 1: IDENTIFY THE BUSINESS
- If I gave a share.google link, follow its redirects. The final Google URL has q=<business name> and kgmid=<id>. Search that name with any city it shows.
- Find and cross-check (2+ sources each where possible): exact name, full address including suite and floor, phone, hours, booking link, website, TikTok, Instagram, Facebook.
- Sources to check: Google Business, Yelp, Booksy, StyleSeat, Vagaro, Fresha, Square Appointments, GlossGenius, Atly, and the bios on their own social accounts.
- Open every booking link. If it redirects, 404s, or says the business is deleted or unavailable, it's dead. Don't use it, and tell me.

STEP 2: PRICING (REAL PRICES ONLY)
- Pull the service menu from booking pages, their website, Yelp menus, Google Business "Services", and price lists in their Instagram or TikTok captions, posts or highlights.
- If a booking page was deleted, search snippets often still quote the old menu. Use those and say so.
- Use regular prices, not promo prices. Keep durations if listed.
- No price found = "Call". If captions say "by appointment only", label the service that way.
- Make a table: service, price, duration, source.

STEP 3: REAL PHOTOS
- TikTok: fetch https://www.tiktok.com/embed/@HANDLE (no login needed). Its JSON has each video's "coverUrl" and "desc" (caption). Download covers right away, because the URLs expire.
- Instagram: try https://www.instagram.com/p/POST_ID/embed/ for post images. If Instagram blocks you, use their Facebook page, Google Business photos, Yelp photos, or their booking-site gallery.
- Use only the business's own photos. No stock photos.
- Pick 4 photos that each show a different service (a fade, a kids' cut, braids, a stylist at work, and so on). Skip blurry shots and ones with heavy on-screen text. For children, prefer back or side views.
- Crop each to 600x660, focused on the hair or work. Cut out on-screen text and picture-in-picture insets. Save as JPEG at about quality 72 and embed as base64 data: URIs.
- Write each photo caption from that post's own text (e.g. "boho braids").

STEP 4: BUILD THE SITE (ONE SELF-CONTAINED HTML FILE)
Style: a neighborhood shop sign. Not a dark-and-gold template, not glowing gradients, not rounded cards everywhere.
- Colors: cream paper background with a subtle noise texture (#f2e9d6), ink #161310, barber red #c4321e, blue #1f3d73, mustard #e3a72a, wood #7b4a22, felt black #1a1917. If the business has its own sign or brand colors, use them as the accents.
- Fonts (Google Fonts): Anton (big uppercase headlines), DM Serif Display italic (accent phrases), Archivo (body), Archivo Narrow (labels and buttons), Permanent Marker (handwritten notes, sparingly).
- Buttons: flat fill, 2.5px ink border, 6px corners, hard 4px offset shadow. No glow.

Sections, in this order:
1. Thin top banner: "Concept preview made for [Business] · not their official website".
2. Animated barber-pole stripe (red/cream/blue diagonals). For a non-barber business, use a stripe in their colors.
3. Nav: round monogram badge plus the name, links (Prices, Walk in / Appointments, The work, Find us), and a Call button.
4. Hero:
   - Kicker line: street address · city · floor.
   - The business's main word in huge Anton type, with one letter in mustard (ink outline, hard shadow).
   - The rest of the name in red serif italic.
   - One plain sentence about their services.
   - Buttons: Call and Directions, plus Book if a working booking link exists.
   - A rotating round stamp with a real price and a true line like "WALK-INS WELCOME ★" running around its edge.
5. Black scrolling ticker of their real services, separated by ✂.
6. Price list styled as a felt letterboard: wood frame, grooved black felt, white uppercase letters, dotted leaders, two columns (e.g. Barber / Salon). Add a taped yellow sticky note with one real policy (e.g. "Braids by appointment only").
7. "Two ways into the chair": two ticket stubs. A mustard "Walk in" ticket (only if they take walk-ins) and a blue "Call ahead" ticket (or "Book online" if a working booking link exists). Notched edges, dashed perforation, vertical stub text "Admit one" / "Reserved".
8. "Fresh off the chair": the 4 real photos as slightly rotated polaroids with handwritten captions. Each links to their TikTok or Instagram.
9. "Find us" on a dark background:
   - Numbered steps to get there, using only real details (address, floor, suite).
   - Phone, and hours (or "Call for today's hours" if unknown).
   - Directions button.
   - A drawn SVG map card taped on: street name, building with the address, red pin. It links to Google Maps. No iframes or embedded maps.
10. Footer: huge "See you in the chair", the business name, buttons, small address line, and another pole stripe.
11. Mobile: fixed bottom bar with Call and Directions. No sideways scrolling at 390px wide. At least 16px side margins.

Writing: short, plain and friendly, like the shop talking ("Come upstairs and grab a seat"). No fake reviews or testimonials, no invented hours, no buzzwords.

STEP 5: DELIVER
1. The HTML file.
2. A short report:
   - Business details with sources.
   - Price table with sources, plus which prices are still missing.
   - Links to the posts the photos came from.
   - Anything dead or unverified (e.g. an old booking link).
   - Questions to ask the owner (missing prices, hours, online booking).
```

---

## Prompt 2: Photos and pricing pass on an existing site

```
Do a photos-and-pricing pass on this preview site.

BUSINESS: [name, city]
TIKTOK: [@handle or "find it"]
INSTAGRAM: [@handle or "find it"]
SITE: [attach the current HTML file]

1. PRICING: Find every real price you can. Check their booking pages (Booksy, StyleSeat, Vagaro, Fresha, Square, GlossGenius), website, Yelp, Google Business services, and price lists in their TikTok or Instagram captions and highlights. If a booking page was deleted, search snippets often still quote the old menu. Use regular prices, not promos. Anything not found stays "Call". Never guess.
2. BOOKING LINKS: Open each one. If it's dead (redirect, 404, "business deleted"), swap every Book button for Call and tell me.
3. PHOTOS: Get their own photos. For TikTok, fetch https://www.tiktok.com/embed/@HANDLE and read "coverUrl" and "desc", then download right away. For Instagram, try post /embed/ pages, then fall back to Facebook, Google Business or Yelp photos. Pick 4 that each show a different service. Skip blurry or text-heavy shots. Use back or side views for kids. Crop to 600x660 with no on-screen text or insets, JPEG quality ~72, embedded as base64. Caption each from its post's text.
4. Update only the price board, the sticky note, the polaroids, and the booking buttons. Keep the rest of the design as is.
5. Report back: a price table with sources, which prices are still missing, the posts the photos came from, and any dead links.
```
