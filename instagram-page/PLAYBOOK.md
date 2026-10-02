# Read This Twice — Instagram playbook

A faceless motivation and mindset page. Month 1 (100 posts) is in
`output/schedule.csv`. Run `python3 build.py` to rebuild the files after you
edit the text, or `python3 build.py --start 2026-10-12 --per-day 2` to change
the start date or how many posts go out each day.

## 1. What the data says

Source: the scrape you shared, 1,525 posts tagged #motivation. Most are from
March 2024, and 932 of them have like counts.

- **Most posts flop.** The median post got 3 likes. Only the top 5% got 143+
  likes and the top 1% got 533+. Growth comes from copying the outliers, not
  the average.
- **The two biggest posts were faceless quote pages:**
  - 26,686 likes: a bold one-line "truth bomb" image. The whole caption was
    *"Truthbomb. Drop a 💥 if this hit you."*
  - 9,818 likes: a screenshot of a tweet about gradual growth. Caption:
    *"Drop a 🖤 to affirm this."*
- **Other formats that work:** "Type YES to affirm" affirmation cards, short
  questions ("Progress or excuses?"), and carousels (457 of the 1,525 posts).
- **Short captions and few hashtags.** Posts with zero hashtags had a median of
  12 likes. Posts with 25+ hashtags had a median of 4. The big pages put a
  short call to action in the caption and nothing else.
- **Skip the fitness and selfie formats.** About half of the top 35 posts
  depend on a real person's body or face, so a faceless page can't reproduce
  them.
- **Limits of this data:** it doesn't include follower counts, so part of what
  separates the winners is simply account size. It also predates the two rule
  changes below.

## 2. Two 2026 rules that change "copy exactly"

1. **Original content only.** Since 30 April 2026, Instagram stops recommending
   "aggregator" accounts, meaning accounts that mostly repost other people's
   content. Your existing followers still see their posts, but nobody new is
   shown them. So copy the **format** (layout, length, caption style, call to
   action), but write and design every post yourself. All 100 posts here are
   original text.
2. **Five hashtags at most.** Since December 2025, Instagram allows at most 5
   hashtags per post. Each caption here uses 3.

## 3. The page

**Name:** Read This Twice, **@readthistwice**

- The name makes people curious, because it promises a line worth rereading.
- It works as a call to action on every post ("save this so you can read it
  twice"), and saves help reach.

Backup names if that handle is taken: @thefadein, @quietlyleveling,
@unfinished.you, @notes.to.future.me. You'll need to check which handles are
free yourself; Instagram doesn't let me look them up without logging in.

**Bio:**
```
Words you'll want to read twice.
Mindset • discipline • quiet growth
New reminder every day ↓
```

**Profile picture:** `templates/previews/profile-picture.png`, a black circle
with "R2" in gold. It's the same mark as the avatar on the tweet cards.

**Look:** black, off-white (#F5F2EB) and one accent color (muted gold #C9A227).
Use one serif font for one-liners (e.g. Playfair Display) and a clean sans-serif
font for everything else (e.g. Inter). Keep it consistent so people recognise
the page from the post alone.

## 4. The five formats (the `format` column)

The finished designs are in `templates/` (see `templates/TEMPLATES.md`).

| Format | Share | Design (1080×1350, 4:5) | Caption pattern |
|---|---|---|---|
| `tweet` | 35% | Cream background, white tweet-style card showing **your own** name, handle and "R2" avatar. Never use another person's name or tweet. | "Drop a 🖤 if you needed this today." |
| `one_liner` | 25% | Black background, gold rule, one sentence in big white serif type | "Drop a 💥 if this hit you." |
| `carousel` | 15% | Black cover with "Swipe →", five cream slides numbered 01–05 with a progress bar, black "Save this" closing slide | Hook line, then "Which one is you? Comment the number 👇" |
| `affirmation` | 15% | Cream, centered italic serif, "Claim it in the comments ↓" button | "Type YES to claim it 👇" |
| `question` | 10% | Gold background, big bold question, "Answer in the comments ↓" | "Comment your answer 👇" |

Every caption follows the same shape: call to action, then a follow line, then
3 hashtags. It's short on purpose. Comments, saves and shares ("sends") are
what get a post shown to non-followers.

## 5. Setup, step by step

You have to do these steps yourself; they need your phone number and login.

### A. Instagram account (about 10 minutes)
No Facebook Page is needed.
1. Download the Instagram app and sign up with a **new email address** (not
   your personal one), then claim the handle.
2. Go to **Settings → Account type and tools → Switch to professional account
   → Creator**. Auto-posting and scheduling need a Creator or Business account.
3. Add the bio and profile picture.

### B. The images

**Month one is already done.** All 100 posts are rendered in `posts/`, ready to
upload, so you can skip Canva. Each file is named
`<date>_<time>_<post_id>.jpg` (carousel slides end in `-1` to `-7`), so sorting
the folder by name gives the posting order. Each post's caption is in the
`.txt` file with the same name.

Use Canva instead only if you want to edit the designs by hand:

1. Get **Canva Pro**; Bulk Create needs it, and there's a free trial.
2. Import the 5 ready-made templates from `templates/pdf/` (Canva → **Upload** →
   pick the PDF). Each one opens as an editable design with `{{placeholder}}`
   text where the post's words go.
3. In each template, go to **Apps → Bulk create → Upload data** and upload the
   matching `output/canva_<format>.csv`.
4. Right-click each `{{placeholder}}` text box, choose **Connect data**, and pick
   the column with the same name.
   - Carousels: connect `{{hook}}` on page 1 and `{{point1}}`–`{{point5}}` on
     pages 2–6. Page 7 doesn't change, so each row becomes one 7-slide carousel.
5. Click **Continue → Generate**, then download as PNG and rename or sort the
   files by `post_id` (e.g. RTT001). That ID links each image to its caption in
   `schedule.csv`.

The step-by-step version, with fixes for common import problems, is in
`templates/TEMPLATES.md`.

### C. Scheduling by hand (only if you skip the auto-poster)
- **Later:** the free plan allows only **12 posts a month**, so 100 a month
  needs a paid plan. Later has **no spreadsheet import**, so for each post:
  1. **Connect** Instagram (it logs in through Facebook).
  2. Bulk-upload the PNGs to the Media Library.
  3. Drag each one onto its date and time from `schedule.csv` and paste its
     caption.
  4. Make sure **Auto Publish** is on.

  Doing a month in one sitting takes about 1–2 hours.
- **Meta Business Suite** (business.facebook.com) is free and schedules
  Instagram posts natively, with the same drag-and-paste workflow. Start here
  if you don't want to pay for Later yet.

### D. Reels

Month one has 10 Reels in `reels/`, two a week (Wednesdays and Saturdays at
17:00). Each one is a full-screen version of a strong post, with the text
appearing line by line over a slow zoom. Each Reel's date and time are in its
file name, its cover is the matching `-cover.jpg`, and its caption is in the
`.txt` file.

Before launch there's no data on what performs, so these 10 are picked for
shareability: the same formats as the top posts in the dataset. From week two
on, the weekly loop below replaces them with your real top posts.

The videos are silent on purpose. Schedule them in the **Instagram app** (+ →
Reel → Advanced settings → Schedule) so you can add a trending sound from
Instagram's music library. Keep the sound low so the words stay the focus.

### E. Automatic posting

`AUTOPOST.md` sets up an auto-poster on GitHub Actions. It publishes each post
on schedule through Instagram's official API, so no scheduling tool or
Facebook Page is needed. It starts with the first post on Monday, October 5,
so set it up before then.

## 6. The weekly loop

Volume alone won't grow the page. A short weekly review based on the data will.

1. Every Sunday, open **Instagram → Insights → Content** and sort the week's
   posts by **shares** and **saves** (not likes).
2. Find the top 3 posts and the bottom 3. Note which format and theme each one
   is.
3. Shift next month's mix toward the winners. For example, if carousels win,
   go from 15 to 30 carousels.
4. Make the top 2 posts of each week into **Reels**. Add their post IDs and
   dates to `REELS` in `templates/reels.js` and run `node templates/reels.js`.
   Reels reach far more non-followers than images do. (This dataset had too few
   videos to measure that.)
5. Spend 10 minutes a day replying to comments in the first hour after a post
   goes up.

## 7. Getting more posts

Use `PROMPT.md` with Claude to write each new month. Paste in last month's
top performers so every batch learns from the one before. Writing 2,000 posts
upfront would lock you into whatever you guessed in month 1. One month at a
time, based on real numbers, is how the outlier pages actually grow.
