// Render the five Read This Twice post templates.
//
//   node render.js            -> pdf/<format>.pdf  (Canva-importable templates with {{placeholders}})
//                                previews/*.png    (sample posts filled from ../output/canva_*.csv)
//                                previews/profile-grid.png (first 12 posts as they'll sit on the profile)
//                                previews/profile-picture.png
//   node render.js --all DIR  -> also render every post to DIR/<post_id>[-N].png
//
// Every post is checked against its text box; the script exits non-zero if any
// post's text would overflow, so a new month's copy can't silently break a design.
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const HERE = __dirname;
const DATA = path.join(HERE, "..", "output");
const W = 1080, H = 1350;
const HANDLE = "@readthistwice";

// Brand fonts (both are in Canva's font library, so imported templates keep them).
// Downloaded once from Google Fonts into fonts/ and embedded, so renders never fall back silently.
const FONT_FILES = [
  ["Inter", "normal", 400, "inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuLyfMZg"],
  ["Inter", "normal", 500, "inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuI6fMZg"],
  ["Inter", "normal", 600, "inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuGKYMZg"],
  ["Inter", "normal", 700, "inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuFuYMZg"],
  ["Inter", "normal", 800, "inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuDyYMZg"],
  ["Playfair Display", "normal", 700, "playfairdisplay/v40/nuFvD-vYSZviVYUb_rj3ij__anPXJzDwcbmjWBN2PKeiukDQ"],
  ["Playfair Display", "italic", 600, "playfairdisplay/v40/nuFRD-vYSZviVYUb_rj3ij__anPXDTnCjmHKM4nYO7KN_naUbtY"],
];
function fontFaces() {
  const dir = path.join(HERE, "fonts");
  fs.mkdirSync(dir, { recursive: true });
  return FONT_FILES.map(([family, style, weight, id]) => {
    const file = path.join(dir, `${family.replace(/ /g, "")}-${weight}${style === "italic" ? "italic" : ""}.ttf`);
    if (!fs.existsSync(file)) {
      require("child_process").execFileSync("curl", ["-sSf", "-o", file, `https://fonts.gstatic.com/s/${id}.ttf`]);
    }
    const b64 = fs.readFileSync(file).toString("base64");
    return `@font-face { font-family: "${family}"; font-style: ${style}; font-weight: ${weight};
      src: url(data:font/ttf;base64,${b64}) format("truetype"); }`;
  }).join("\n");
}
let FACES = "";

// Brand: ink #111111, paper #F5F2EB, gold #C9A227 (#8C6D10 where gold text sits on paper).
const CSS = `
* { margin: 0; padding: 0; box-sizing: border-box; }
@page { size: ${W}px ${H}px; margin: 0; }
body { width: ${W}px; }
.page { width: ${W}px; height: ${H}px; position: relative; overflow: hidden; break-after: page; }
.page:last-child { break-after: auto; }
.abs { position: absolute; }
.handle { position: absolute; left: 96px; bottom: 72px; font: 600 30px Inter, sans-serif; letter-spacing: .01em; }
.rule { position: absolute; left: 96px; top: 330px; width: 96px; height: 6px; background: #C9A227; }

/* Tweet card: the 9.8k-like format, with the page's own identity. Fixed card so Canva bulk create stays aligned. */
.tweet { background: #F5F2EB; }
.tweet .card { position: absolute; left: 72px; right: 72px; top: 335px; height: 680px; background: #FFFFFF;
  border-radius: 44px; padding: 64px; }
.tweet .who { display: flex; align-items: center; gap: 24px; }
.tweet .av { width: 108px; height: 108px; border-radius: 50%; background: #111111; color: #C9A227;
  font: 800 42px Inter, sans-serif; letter-spacing: -.02em; display: flex; align-items: center; justify-content: center; }
.tweet .nm { font: 700 40px Inter, sans-serif; color: #0F1419; }
.tweet .hd { font: 400 34px Inter, sans-serif; color: #536471; margin-top: 2px; }
.tweet .fit { margin-top: 44px; height: 400px; font: 400 60px/1.34 Inter, sans-serif; color: #0F1419; }

/* One-liner: the 26.7k-like "truth bomb". */
.one { background: #111111; }
.one .fit { left: 96px; right: 96px; top: 390px; height: 640px; font: 700 100px/1.12 "Playfair Display", serif;
  color: #F5F2EB; letter-spacing: -.01em; }
.one .handle { color: #C9A227; }

/* Affirmation: centered, the comment prompt printed on the image for people who never open captions. */
.aff { background: #F5F2EB; }
.aff .lab { left: 96px; right: 96px; top: 300px; text-align: center; font: 600 28px Inter, sans-serif;
  letter-spacing: .2em; color: #8C6D10; }
.aff .fit { left: 110px; right: 110px; top: 380px; height: 500px; text-align: center;
  font: italic 600 84px/1.2 "Playfair Display", serif; color: #111111; }
.aff .pill { left: 50%; transform: translateX(-50%); top: 960px; background: #111111; color: #F5F2EB;
  font: 600 36px Inter, sans-serif; padding: 26px 52px; border-radius: 999px; white-space: nowrap; }
.aff .handle { left: 0; right: 0; text-align: center; color: #8C6D10; }

/* Question: loud gold so it reads as "your turn" in a feed of quotes. */
.q { background: #C9A227; }
.q .lab { left: 96px; top: 300px; font: 700 30px Inter, sans-serif; letter-spacing: .2em; color: #111111; }
.q .fit { left: 96px; right: 96px; top: 370px; height: 560px; font: 800 92px/1.08 Inter, sans-serif;
  color: #111111; letter-spacing: -.02em; }
.q .ans { left: 96px; right: 96px; top: 1000px; border-top: 4px solid #111111; padding-top: 28px;
  font: 600 38px Inter, sans-serif; color: #111111; }
.q .handle { color: #111111; opacity: .72; }

/* Carousel: cover, five numbered points, closing save/follow slide. */
.cv { background: #111111; }
.cv .fit { left: 96px; right: 96px; top: 390px; height: 620px; font: 700 104px/1.08 "Playfair Display", serif;
  color: #F5F2EB; letter-spacing: -.01em; }
.cv .handle, .cl .handle { color: #C9A227; }
.swipe { position: absolute; right: 96px; bottom: 72px; font: 600 34px Inter, sans-serif; }
.cv .swipe { color: #C9A227; }
.pt { background: #F5F2EB; }
.pt .num { left: 90px; top: 250px; font: 800 220px/1 Inter, sans-serif; color: #C9A227; letter-spacing: -.04em; }
.pt .fit { left: 96px; right: 96px; top: 560px; height: 460px; font: 700 80px/1.12 Inter, sans-serif;
  color: #111111; letter-spacing: -.01em; }
.pt .handle { color: #8C6D10; }
.prog { position: absolute; right: 96px; bottom: 82px; display: flex; gap: 10px; }
.prog i { width: 44px; height: 8px; border-radius: 4px; background: rgba(17,17,17,.16); }
.prog i.on { background: #111111; }
.cl { background: #111111; }
.cl svg { position: absolute; left: 96px; top: 300px; }
.cl .ct { left: 96px; right: 96px; top: 470px; font: 700 92px/1.12 "Playfair Display", serif; color: #F5F2EB; }
.cl .cf { left: 96px; right: 96px; top: 830px; font: 500 40px/1.35 Inter, sans-serif; color: #C9A227; }
`;

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const handle = () => `<div class="handle">${HANDLE}</div>`;
const BOOKMARK = `<svg width="96" height="120" viewBox="0 0 96 120" fill="none" aria-hidden="true">
  <path d="M8 8h80v104L48 84 8 112z" stroke="#C9A227" stroke-width="8" stroke-linejoin="round" fill="none"/></svg>`;

// Each builder takes one CSV row and returns its pages.
const BUILD = {
  tweet: (r) => [`<div class="page tweet"><div class="card">
      <div class="who"><div class="av">R2</div><div><div class="nm">Read This Twice</div><div class="hd">${HANDLE}</div></div></div>
      <div class="fit">${esc(r.text)}</div></div></div>`],
  one_liner: (r) => [`<div class="page one"><div class="rule"></div>
      <div class="abs fit">${esc(r.text)}</div>${handle()}</div>`],
  affirmation: (r) => [`<div class="page aff"><div class="abs lab">TODAY'S AFFIRMATION</div>
      <div class="abs fit">${esc(r.text)}</div><div class="abs pill">Claim it in the comments ↓</div>${handle()}</div>`],
  question: (r) => [`<div class="page q"><div class="abs lab">YOUR TURN</div>
      <div class="abs fit">${esc(r.text)}</div><div class="abs ans">Answer in the comments ↓</div>${handle()}</div>`],
  carousel: (r) => [
    `<div class="page cv"><div class="rule"></div><div class="abs fit">${esc(r.hook)}</div>${handle()}<div class="swipe">Swipe →</div></div>`,
    ...[1, 2, 3, 4, 5].map((n) => `<div class="page pt"><div class="abs num">0${n}</div>
      <div class="abs fit">${esc(r["point" + n])}</div>${handle()}
      <div class="prog">${[1, 2, 3, 4, 5].map((k) => `<i class="${k <= n ? "on" : ""}"></i>`).join("")}</div></div>`),
    `<div class="page cl">${BOOKMARK}<div class="abs ct">Save this so you can read it twice.</div>
      <div class="abs cf">Follow ${HANDLE} for daily reminders.</div>${handle()}</div>`,
  ],
};
const PLACEHOLDER = {
  tweet: { text: "{{text}}" }, one_liner: { text: "{{text}}" }, affirmation: { text: "{{text}}" },
  question: { text: "{{text}}" },
  carousel: { hook: "{{hook}}", point1: "{{point1}}", point2: "{{point2}}", point3: "{{point3}}", point4: "{{point4}}", point5: "{{point5}}" },
};
const FORMATS = Object.keys(BUILD);

function parseCSV(text) {
  const rows = []; let row = [], field = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') q = false;
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  const [head, ...body] = rows.filter((r) => r.length > 1 || r[0]);
  return body.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""])));
}
const load = (fmt) => parseCSV(fs.readFileSync(path.join(DATA, `canva_${fmt}.csv`), "utf8"));

const doc = (pages) => `<!doctype html><html><head><meta charset="utf-8">
  <style>${FACES}${CSS}</style></head><body>${pages.join("")}</body></html>`;

async function open(browser, pages) {
  const page = await browser.newPage({ viewport: { width: W, height: H } });
  await page.setContent(doc(pages), { waitUntil: "networkidle" });
  const bad = await page.evaluate(async () => {
    await Promise.all([...document.fonts].map((f) => f.load().catch(() => {})));
    return [...document.fonts].filter((f) => f.status !== "loaded").map((f) => `${f.family} ${f.weight}`);
  });
  if (bad.length) throw new Error("Fonts failed to load: " + bad.join(", "));
  return page;
}

// Returns the indexes of pages whose text box overflows.
const overflows = (page) => page.evaluate(() =>
  [...document.querySelectorAll(".page")].flatMap((p, i) =>
    [...p.querySelectorAll(".fit")].some((el) => el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1) ? [i] : []));

async function shoot(page, dir, names) {
  const els = await page.$$(".page");
  for (let i = 0; i < els.length; i++) await els[i].screenshot({ path: path.join(dir, names[i]) });
}

async function main() {
  const allDir = process.argv.includes("--all") ? process.argv[process.argv.indexOf("--all") + 1] : null;
  const pdfDir = path.join(HERE, "pdf"), prevDir = path.join(HERE, "previews");
  [pdfDir, prevDir, allDir].filter(Boolean).forEach((d) => fs.mkdirSync(d, { recursive: true }));
  FACES = fontFaces();
  const browser = await chromium.launch();
  const problems = [];
  const rendered = {};  // post_id -> first page png (for the grid)
  try {
    for (const fmt of FORMATS) {
      // 1. Canva template PDF with placeholders.
      const tpl = await open(browser, BUILD[fmt](PLACEHOLDER[fmt]));
      await tpl.pdf({ path: path.join(pdfDir, `${fmt}.pdf`), width: `${W}px`, height: `${H}px`, printBackground: true });
      await tpl.close();

      // 2. Every post: fit check; previews for the first row; full renders when asked.
      const rows = load(fmt);
      for (const [idx, row] of rows.entries()) {
        const pages = BUILD[fmt](row);
        const page = await open(browser, pages);
        const bad = await overflows(page);
        if (bad.length) problems.push(`${row.post_id} (${fmt}) overflows on page ${bad.map((b) => b + 1).join(", ")}`);
        const multi = pages.length > 1;
        if (idx === 0) await shoot(page, prevDir, pages.map((_, i) => multi ? `${fmt}-${i + 1}.png` : `${fmt}.png`));
        const dir = allDir || path.join(require("os").tmpdir(), "rtt-render");
        fs.mkdirSync(dir, { recursive: true });
        await shoot(page, dir, pages.map((_, i) => multi ? `${row.post_id}-${i + 1}.png` : `${row.post_id}.png`));
        rendered[row.post_id] = path.join(dir, multi ? `${row.post_id}-1.png` : `${row.post_id}.png`);
        await page.close();
      }
      console.log(`${fmt}: template + ${rows.length} posts checked`);
    }

    // 3. Profile picture: the tweet-card avatar at 1080×1080 (Instagram crops it to a circle).
    const pfp = await browser.newPage({ viewport: { width: 1080, height: 1080 } });
    await pfp.setContent(`<!doctype html><style>${FACES}*{margin:0}body{width:1080px;height:1080px;background:#111111;
      display:flex;align-items:center;justify-content:center;font:800 400px Inter,sans-serif;letter-spacing:-.02em;color:#C9A227}</style>R2`);
    await pfp.evaluate(() => document.fonts.ready);
    await pfp.screenshot({ path: path.join(prevDir, "profile-picture.png") });

    // 4. Profile grid: the first 12 scheduled posts, newest top-left, cropped 3:4 like the profile view.
    const sched = parseCSV(fs.readFileSync(path.join(DATA, "schedule.csv"), "utf8")).slice(0, 12).reverse();
    const tiles = sched.map((r) => `<img src="data:image/png;base64,${fs.readFileSync(rendered[r.post_id]).toString("base64")}">`).join("");
    const grid = await browser.newPage({ viewport: { width: 1080, height: 1440 } });
    await grid.setContent(`<!doctype html><style>*{margin:0}body{background:#fff;width:1080px}
      .g{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
      img{width:100%;aspect-ratio:3/4;object-fit:cover;display:block}</style><div class="g">${tiles}</div>`);
    await grid.locator(".g").screenshot({ path: path.join(prevDir, "profile-grid.png") });
  } finally {
    await browser.close();
  }
  if (problems.length) { console.error("Text overflow:\n  " + problems.join("\n  ")); process.exit(1); }
  console.log("All posts fit their templates.");
}

main().catch((e) => { console.error(e); process.exit(1); });
