# Canva templates

There are five templates, one per post format. Each is ready to import into
Canva and fill with Bulk create.

| File | Pages | Fill from | Placeholders |
|---|---|---|---|
| `pdf/tweet.pdf` | 1 | `output/canva_tweet.csv` (35 rows) | `{{text}}` |
| `pdf/one_liner.pdf` | 1 | `output/canva_one_liner.csv` (25 rows) | `{{text}}` |
| `pdf/affirmation.pdf` | 1 | `output/canva_affirmation.csv` (15 rows) | `{{text}}` |
| `pdf/question.pdf` | 1 | `output/canva_question.csv` (10 rows) | `{{text}}` |
| `pdf/carousel.pdf` | 7 | `output/canva_carousel.csv` (15 rows) | `{{hook}}` on page 1, `{{point1}}`–`{{point5}}` on pages 2–6 |

`previews/` shows each template filled with a real post. It also has:

- `profile-grid.png`: the first 12 posts as they'll look on your profile.
- `profile-picture.png`: your profile picture.

## Importing into Canva

1. Open Canva and go to **Upload → Upload files**, then pick a PDF from `pdf/`. Open it.
   Canva turns PDF text into editable text boxes.
2. Check the size under **Resize**. It should be 1080 × 1350 px. If Canva shows
   another size, use **Resize** to set it to 1080 × 1350.
3. Check the fonts. Click a text box and make sure the font shows as **Inter**
   or **Playfair Display**. Both are free in Canva. If Canva swapped one, set it
   back using the specs below.
4. Click each `{{placeholder}}` and drag its text box to full width:
   - **Tweet card:** from the left edge of the text to 64 px inside the card's
     right edge.
   - **All other formats:** from 96 px to 984 px across the page.

   Canva makes the box only as wide as the placeholder word, so without this
   the real text wraps one word per line.
5. Go to **Apps → Bulk create → Upload data** and upload the matching CSV.
6. Right-click each placeholder, choose **Connect data**, and pick the column
   with the same name.
7. Click **Continue**, review the preview, then click **Generate**.
8. Download as **PNG** with all pages. Rename the files by the `post_id` column
   (RTT001…) so each one matches its caption in `schedule.csv`.

For the carousel, each CSV row turns all 7 pages into one carousel. Page 7
(the save and follow slide) is the same every time.

**You can skip Canva entirely.** Month one's 100 posts are already rendered
from these designs in `../posts/`, ready to upload. To render a new month, run
`node render.js --all ../posts`.

## Why these designs help a new account grow

- **Portrait 4:5.** It takes up the most screen space in the feed of any shape
  Instagram allows.
- **Text sized for small screens.** The smallest post text is 60 px and the
  headlines are 80–104 px. They stay readable on the Explore page and the
  profile grid, where posts show at about a third of the screen width.
- **Survives the profile crop.** The profile grid crops posts to 3:4, and all
  text stays inside that crop (see `previews/profile-grid.png`).
- **Your handle is on every image.** Screenshots and reposts carry your name
  with them. Shares are the main way people who don't follow you find a page.
- **The comment prompt is on the image itself.** The affirmation and question
  posts ask for comments in the design, because most people never open the
  caption.
- **Carousels pull people through.** The cover says "Swipe →", each slide
  shows a progress bar, and the last slide asks for a save and a follow. Every
  point slide also makes sense on its own, because Instagram sometimes shows
  an unswiped carousel again starting from a later slide.
- **One consistent look.** Three colors, two fonts and the same positions every
  time mean people recognize your post before they read the handle. The grid
  looks like a brand, which helps turn profile visits into follows.
- **Honest tweet cards.** They show only your own name and avatar. There's no
  verification badge and no fake like counts.

## Specs (for rebuilding by hand)

Canvas 1080 × 1350. Positions are distances from the top-left corner in px.
Colors: ink `#111111`, paper `#F5F2EB`, gold `#C9A227`, dark gold `#8C6D10`.

| Format | Background | Main text box | Main text style | Fixed parts |
|---|---|---|---|---|
| tweet | paper | inside a white card at x 72–1008, y 335–1015, radius 44 | Inter Regular 60, line height 1.34, `#0F1419` | Avatar: 108 circle in ink with "R2" in Inter ExtraBold 42 gold. Name: Inter Bold 40. Handle: Inter Regular 34, `#536471` |
| one_liner | ink | x 96–984, y 390, max 640 tall | Playfair Display Bold 100, line height 1.12, paper | Gold rule 96×6 at y 330. Handle: Inter SemiBold 30, gold, bottom 72 |
| affirmation | paper | x 110–970, y 380, centered | Playfair Display SemiBold Italic 84, line height 1.2, ink | "TODAY'S AFFIRMATION" label: Inter SemiBold 28, dark gold. Ink pill at y 960 reading "Claim it in the comments ↓" in Inter SemiBold 36 |
| question | gold | x 96–984, y 370 | Inter ExtraBold 92, line height 1.08, ink | "YOUR TURN" label: Inter Bold 30. 4 px ink rule at y 1000, then "Answer in the comments ↓" in Inter SemiBold 38 |
| carousel cover | ink | x 96–984, y 390 | Playfair Display Bold 104, line height 1.08, paper | Gold rule. "Swipe →" in Inter SemiBold 34, gold, bottom right |
| carousel points | paper | x 96–984, y 560 | Inter Bold 80, line height 1.12, ink | "01"–"05" in Inter ExtraBold 220, gold, at y 250. Progress bar of five 44×8 bars, bottom right |
| carousel close | ink | fixed text | Playfair Display Bold 92, paper | Gold bookmark icon at y 300. "Follow @readthistwice for daily reminders." in Inter Medium 40, gold |

## Changing the designs

Everything is defined in `render.js`. Edit the CSS or the text, then run
`node render.js`. The script makes new PDFs and previews and checks that every
post in `../output/` still fits. If one doesn't, it stops and names the post. The
first run downloads the two fonts into `fonts/`.
