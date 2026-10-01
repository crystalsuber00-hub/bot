// Render month-one Reels from the strongest posts.
//
//   node reels.js        -> ../reels/<date>_<time>_REEL-<post_id>.mp4   (1080×1920, 30 fps, silent AAC track)
//                           ../reels/<date>_<time>_REEL-<post_id>-cover.jpg
//                           ../reels/<date>_<time>_REEL-<post_id>.txt    (caption)
//
// The text builds line by line (list items one by one) over a slow zoom, then
// holds so it can be read, and cuts back to the start so the Reel loops.
// Add trending audio in the Instagram app when posting; the file carries silence.
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { chromium } = require("playwright");
const { fontFaces, parseCSV, esc, HANDLE, DATA } = require("./render.js");

const OUT = path.join(__dirname, "..", "reels");
const W = 1080, H = 1920, FPS = 30;

// Picked before launch for shareability (the formats behind the dataset's top
// posts). Each Reel goes out after its image version. Wednesdays and Saturdays.
const REELS = [
  ["RTT002", "2026-10-07"], ["RTT005", "2026-10-10"], ["RTT004", "2026-10-14"], ["RTT013", "2026-10-17"],
  ["RTT034", "2026-10-21"], ["RTT051", "2026-10-24"], ["RTT057", "2026-10-28"], ["RTT060", "2026-10-31"],
  ["RTT061", "2026-11-04"], ["RTT070", "2026-11-07"],
];
const TIME = "17:00";

// Safe zone: Instagram covers ~120px on the right (buttons) and the bottom ~420px
// (caption, audio), and the profile grid crops to the middle 1080×1440 (y 240–1680).
// All text sits in x 96–960, y 300–1460.
const CSS = `
* { margin: 0; padding: 0; box-sizing: border-box; }
body { width: ${W}px; height: ${H}px; overflow: hidden; }
.stage { position: absolute; inset: 0; transform-origin: 50% 45%; }
.ln { display: block; }
.handle { position: absolute; left: 96px; top: 1400px; font: 600 34px Inter, sans-serif; }
.rule { width: 96px; height: 6px; background: #C9A227; margin-bottom: 48px; }
.block { position: absolute; left: 96px; width: 864px; }

.one { background: #111111; }
.one .block { top: 520px; }
.one .txt { font: 700 112px/1.1 "Playfair Display", serif; color: #F5F2EB; letter-spacing: -.01em; }
.one .handle, .cv .handle { color: #C9A227; }

.tweet { background: #F5F2EB; }
.tweet .block { left: 72px; width: 936px; top: 500px; background: #FFFFFF; border-radius: 48px; padding: 64px; }
.tweet .who { display: flex; align-items: center; gap: 24px; margin-bottom: 44px; }
.tweet .av { width: 112px; height: 112px; border-radius: 50%; background: #111111; color: #C9A227;
  font: 800 44px Inter, sans-serif; letter-spacing: -.02em; display: flex; align-items: center; justify-content: center; }
.tweet .nm { font: 700 42px Inter, sans-serif; color: #0F1419; }
.tweet .hd { font: 400 36px Inter, sans-serif; color: #536471; margin-top: 2px; }
.tweet .txt { font: 400 64px/1.34 Inter, sans-serif; color: #0F1419; }
.tweet .handle { color: #8C6D10; }

.aff { background: #F5F2EB; }
.aff .block { top: 520px; text-align: center; }
.aff .lab { font: 600 30px Inter, sans-serif; letter-spacing: .2em; color: #8C6D10; margin-bottom: 56px; }
.aff .txt { font: italic 600 96px/1.2 "Playfair Display", serif; color: #111111; }
.aff .pill { display: inline-block; margin-top: 88px; background: #111111; color: #F5F2EB; font: 600 38px Inter, sans-serif;
  padding: 28px 56px; border-radius: 999px; }
.aff .handle { left: 0; right: 0; text-align: center; color: #8C6D10; }

.cv { background: #111111; }
.cv .block { top: 300px; }
.cv .hook { font: 700 88px/1.1 "Playfair Display", serif; color: #F5F2EB; letter-spacing: -.01em; }
.cv ol { list-style: none; margin-top: 60px; display: grid; gap: 30px; }
.cv li { display: grid; grid-template-columns: 92px 1fr; align-items: baseline; }
.cv .n { font: 800 56px Inter, sans-serif; color: #C9A227; letter-spacing: -.03em; }
.cv .p { font: 600 50px/1.2 Inter, sans-serif; color: #F5F2EB; }
.cv .save { margin-top: 64px; font: 600 40px Inter, sans-serif; color: #C9A227; }
.cv .handle { display: none; }
`;

// Each builder returns the stage markup. `.txt` is split into lines that reveal
// one at a time; every `.step` element reveals as one unit, in document order.
const handle = `<div class="handle">${HANDLE}</div>`;
const BUILD = {
  one_liner: (r) => `<div class="stage one"><div class="block"><div class="rule"></div>
      <div class="txt">${esc(r.text)}</div></div>${handle}</div>`,
  tweet: (r) => `<div class="stage tweet"><div class="block">
      <div class="who"><div class="av">R2</div><div><div class="nm">Read This Twice</div><div class="hd">${HANDLE}</div></div></div>
      <div class="txt">${esc(r.text)}</div></div></div>`,
  affirmation: (r) => `<div class="stage aff"><div class="block"><div class="lab">TODAY'S AFFIRMATION</div>
      <div class="txt">${esc(r.text)}</div><div class="step"><span class="pill">Claim it in the comments ↓</span></div></div>${handle}</div>`,
  carousel: (r) => `<div class="stage cv"><div class="block"><div class="rule"></div><div class="txt hook">${esc(r.hook)}</div>
      <ol>${[1, 2, 3, 4, 5].map((n) => `<li class="step"><span class="n">0${n}</span><span class="p">${esc(r["point" + n])}</span></li>`).join("")}</ol>
      <div class="step save">Save this. Read it twice.</div></div></div>`,
};

// Runs in the page: split text into lines, attach paused animations, return the timeline length.
function setupTimeline() {
  for (const el of document.querySelectorAll(".txt")) {
    const words = el.textContent.trim().split(/\s+/);
    el.innerHTML = words.map((w) => `<span class="w">${w.replace(/&/g, "&amp;").replace(/</g, "&lt;")}</span>`).join(" ");
    const lines = [];
    for (const w of el.querySelectorAll(".w")) {
      const last = lines[lines.length - 1];
      if (last && last.top === w.offsetTop) last.words.push(w.textContent); else lines.push({ top: w.offsetTop, words: [w.textContent] });
    }
    el.innerHTML = lines.map((l) => `<span class="ln reveal">${l.words.join(" ").replace(/&/g, "&amp;").replace(/</g, "&lt;")}</span>`).join("");
  }
  document.querySelectorAll(".step").forEach((el) => el.classList.add("reveal"));
  let t = 350;
  const anims = [];
  for (const el of document.querySelectorAll(".reveal")) {
    anims.push(el.animate([{ opacity: 0, transform: "translateY(28px)" }, { opacity: 1, transform: "none" }],
      { duration: 500, delay: t, fill: "both", easing: "cubic-bezier(.2,.7,.2,1)" }));
    t += el.classList.contains("ln") ? 650 : 900;
  }
  // Hold long enough to read everything once more; Reels between 7 and 15 seconds.
  const total = Math.min(15000, Math.max(7000, t + 3200));
  anims.push(document.querySelector(".stage").animate([{ transform: "scale(1)" }, { transform: "scale(1.04)" }],
    { duration: total, fill: "both", easing: "linear" }));
  anims.forEach((a) => a.pause());
  window.__seek = (ms) => anims.forEach((a) => { a.currentTime = ms; });
  // Bottom of the text block, for the safe-zone check.
  const block = document.querySelector(".block").getBoundingClientRect();
  return { total, bottom: block.bottom, right: block.right };
}

function encode(file) {
  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error",
    "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest",
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-profile:v", "high",
    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", file], { stdio: ["pipe", "inherit", "inherit"] });
  const done = new Promise((res, rej) => ff.on("close", (c) => c === 0 ? res() : rej(new Error(`ffmpeg exited ${c}`))));
  return { write: (buf) => new Promise((res) => ff.stdin.write(buf) ? res() : ff.stdin.once("drain", res)), end: () => { ff.stdin.end(); return done; } };
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const rows = {};
  for (const fmt of Object.keys(BUILD)) {
    for (const r of parseCSV(fs.readFileSync(path.join(DATA, `canva_${fmt}.csv`), "utf8"))) rows[r.post_id] = { ...r, fmt };
  }
  const captions = Object.fromEntries(parseCSV(fs.readFileSync(path.join(DATA, "schedule.csv"), "utf8")).map((r) => [r.post_id, r.caption]));
  const faces = fontFaces();
  const browser = await chromium.launch();
  const problems = [];
  try {
    for (const [id, date] of REELS) {
      const row = rows[id];
      if (!row) throw new Error(`${id} is not a reel-able post (question posts have no Reel design)`);
      const page = await browser.newPage({ viewport: { width: W, height: H } });
      await page.setContent(`<!doctype html><meta charset="utf-8"><style>${faces}${CSS}</style>${BUILD[row.fmt](row)}`);
      await page.evaluate(async () => { await Promise.all([...document.fonts].map((f) => f.load().catch(() => {}))); });
      const { total, bottom, right } = await page.evaluate(setupTimeline);
      if (bottom > 1460 || right > 1010) problems.push(`${id}: text reaches y ${Math.round(bottom)}, x ${Math.round(right)}, outside the safe zone`);

      const base = path.join(OUT, `${date}_${TIME.replace(":", "")}_REEL-${id}`);
      const enc = encode(`${base}.mp4`);
      const frames = Math.round(total / 1000 * FPS);
      for (let f = 0; f < frames; f++) {
        await page.evaluate((ms) => window.__seek(ms), f * 1000 / FPS);
        await enc.write(await page.screenshot({ type: "jpeg", quality: 92 }));
      }
      await enc.end();
      await page.evaluate((ms) => window.__seek(ms), total);
      await page.screenshot({ path: `${base}-cover.jpg`, quality: 95 });
      fs.writeFileSync(`${base}.txt`, captions[id] + "\n");
      await page.close();
      console.log(`${path.basename(base)}.mp4  ${(total / 1000).toFixed(1)}s`);
    }
  } finally {
    await browser.close();
  }
  if (problems.length) { console.error("Safe zone:\n  " + problems.join("\n  ")); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
