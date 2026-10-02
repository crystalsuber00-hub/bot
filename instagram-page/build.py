"""Build month-1 content for the @quietlyleveling Instagram page.

Writes to output/:
  schedule.csv         every post in posting order: date, time, format, on-image text, caption
  canva_<format>.csv   one file per Canva template, for Canva "Bulk create"

All on-image text is original. The formats are modeled on the top posts in the
#motivation scrape (see PLAYBOOK.md); the wording is not copied, because
Instagram stops recommending accounts that mostly repost others' content.

Usage: python3 build.py [--start 2026-10-05] [--per-day 3]
"""
import argparse
import csv
import datetime as dt
from pathlib import Path

HANDLE = "@quietlyleveling"
OUT = Path(__file__).parent / "output"

# --- Format A: tweet-style card (modeled on the 9.8k-like post) ------------------
TWEET = [
    "Nobody talks about how lonely it gets when you stop being who everyone was used to.",
    "Healing made me boring and I've never been more at peace.",
    "You don't need a new year. You need a new Tuesday and the nerve to act different on it.",
    "The version of you that you're scared to become is the one your life is waiting for.",
    "Some of y'all aren't tired. You're just carrying conversations you should've ended.",
    "Discipline is choosing what you want most over what you want now. Every day. Even the boring ones.",
    "I stopped explaining myself and somehow everyone understood me better.",
    "Your comfort zone isn't comfortable. It's just familiar.",
    "Normalize going quiet for a few months and coming back different.",
    "Growth is realizing the thing you begged for would've ruined you.",
    "You can be grateful for where you are and still refuse to stay there.",
    "Stop waiting to feel ready. Ready is a feeling that shows up after you start.",
    "Being misunderstood by people who never tried to understand you is not your problem.",
    "Quiet seasons are where the loud results get built.",
    "Not everyone deserves a front row seat to your life. Some get the cheap seats. Some don't get tickets.",
    "You're not behind. You're on a timeline nobody else has the map to.",
    "The day you stop needing their approval is the day you get your time back.",
    "Consistency looks boring from the outside. That's exactly why so few people have it.",
    "One day you'll thank yourself for not quitting the version of you nobody clapped for.",
    "Protect your mornings like they're the only part of the day you fully own. Because they are.",
    "Outgrowing people isn't cruel. Staying small so they stay comfortable is.",
    "You keep asking for a sign. The exhaustion was the sign.",
    "I don't post my goals anymore. I post the results.",
    "Leveling up is lonely at first. Then it's peaceful. Then it's obvious.",
    "Your future self is watching you right now through memories. Give them something good.",
    "The habit you keep skipping is the door you keep saying is locked.",
    "Not answering is an answer. Learn to hear it.",
    "Calm is a superpower. The person who can't be provoked controls the room.",
    "Move in silence long enough and people start asking what changed.",
    "Stop shrinking to fit places you've outgrown.",
    "Rest is not a reward for finishing. It's part of how you finish.",
    "You don't have to prove anything to people who already decided who you are.",
    "Small wins every day beat big plans every January.",
    "Nobody needs to know you're working on yourself. They'll see it.",
    "Quiet progress still counts. Especially on the days nobody sees.",
]

# --- Format B: bold one-liner "truth bomb" (modeled on the 26.7k-like post) -------
ONE_LINER = [
    "Comfort is the most expensive thing you own.",
    "Your excuses have never paid a single bill.",
    "Silence is a full sentence.",
    "Peace over people. Every time.",
    "Busy is not the same as productive.",
    "Less announcing. More becoming.",
    "Motivation leaves. Habits stay.",
    "Nobody is coming. Get up.",
    "Your circle is a forecast of your future.",
    "Stop romanticizing your potential. Start using it.",
    "Quiet is a strategy.",
    "If it costs your peace, it's too expensive.",
    "Overthinking is just fear with a calendar.",
    "Do it scared. Do it tired. Do it anyway.",
    "You're not lazy. You're unclear.",
    "The bar was never too high. You were looking down.",
    "Effort nobody sees still counts.",
    "Your phone is stealing your best hours.",
    "Your next level is boring on purpose.",
    "Start before the plan is perfect.",
    "Distance shows you who was real.",
    "Average is a decision you make daily.",
    "Level up quietly. Let the results talk.",
    "Your peace is not up for negotiation.",
    "Don't let one bad day write the whole story.",
]

# --- Format C: affirmation card (modeled on "Type YES to affirm") ---------------
AFFIRMATION = [
    "I am allowed to take up space.",
    "I don't chase. I attract what's meant for me.",
    "My calm is my power.",
    "I am becoming someone my younger self would be proud of.",
    "I release what I can't control.",
    "I am leveling up, even when nobody sees it.",
    "I keep the promises I make to myself.",
    "I am done apologizing for growing.",
    "My discipline is louder than my doubts.",
    "I deserve rest without guilt.",
    "I move in silence and I move with purpose.",
    "I don't need an audience to grow.",
    "I choose progress over perfection.",
    "I trust the timing of my life.",
    "I let my results do the talking.",
]

# --- Format D: carousel, hook + 5 points + closing slide -------------------------
CAROUSEL = [
    ("5 signs you're leveling up quietly",
     ["You need less validation.", "Drama bores you now.", "You'd rather sleep than argue.",
      "Your circle got smaller but better.", "You stopped announcing your plans."]),
    ("Things I stopped doing that changed my life",
     ["Replying instantly.", "Explaining my no.", "Scrolling before I'm out of bed.",
      "Waiting for motivation.", "Saying \"I'll start Monday.\""]),
    ("How to get disciplined (when you're not)",
     ["Make it stupidly small.", "Same time, same place.", "Never miss twice.",
      "Track it where you can see it.", "Reward the streak, not the result."]),
    ("Read this if you feel behind",
     ["Everyone posts highlights, not rough drafts.", "Your path isn't a race.",
      "Late bloomers still bloom.", "Comparing chapters is unfair to both of you.",
      "You're allowed to start at any age."]),
    ("Habits of people who are quietly winning",
     ["Up before the noise.", "Read more than they scroll.", "Move their body daily.",
      "Keep their goals private.", "Say no often."]),
    ("Red flags in yourself to fix this year",
     ["Needing to win every argument.", "Ghosting your own goals.",
      "Blaming your past for your present.", "Chasing attention over respect.",
      "Starting everything, finishing nothing."]),
    ("A 5-minute morning reset",
     ["Water before phone.", "Open a window.", "Name 3 things you're grateful for.",
      "Pick ONE must-do task.", "Move for 2 minutes."]),
    ("How to level up without telling anyone",
     ["Stop posting your plans.", "Track progress in your notes app.", "Let people think you're boring.",
      "Spend one Friday a month building.", "Show results, not intentions."]),
    ("What leveling up actually looks like",
     ["Not reacting.", "Going to bed early on a Friday.", "Saying no without a speech.",
      "Choosing the gym over the group chat.", "Being okay with nobody noticing yet."]),
    ("Rules for protecting your peace",
     ["Not every message needs a reply.", "Not every opinion needs your input.",
      "Leave early if it feels off.", "Mute freely.", "Rest before you break."]),
    ("Signs you're outgrowing your circle",
     ["Your wins make them quiet.", "Conversations feel recycled.",
      "You leave drained, not inspired.", "You hide your goals around them.",
      "You feel lonelier with them than alone."]),
    ("How to stop overthinking",
     ["Write it down.", "Ask: will this matter in a year?", "Give worry a 10-minute window.",
      "Act on the smallest piece.", "Go outside."]),
    ("Before you give up, remember...",
     ["Why you started.", "How far you've already come.", "The person you'd let down is you.",
      "Slow progress is still progress.", "Tomorrow-you will be glad you stayed."]),
    ("Daily non-negotiables for a better life",
     ["10 minutes of sunlight.", "One page of a book.", "7,000+ steps.",
      "No phone for the first 30 minutes.", "One hard thing."]),
    ("Quiet confidence looks like...",
     ["Not needing the last word.", "Listening more than talking.",
      "Celebrating others easily.", "Being fine with \"no.\"", "Walking away without a scene."]),
]
CAROUSEL_CLOSER = f"Save this for your next level.\nFollow {HANDLE}"

# --- Format E: question card (comment bait) --------------------------------------
QUESTION = [
    "Be honest: are you building or waiting?",
    "What's one habit you're starting this week?",
    "Would you rather be respected or liked?",
    "What did you outgrow this year?",
    "Peace or being right? You only get one.",
    "What's the best advice you ignored?",
    "Rate your discipline this week, 1 to 10.",
    "What are you quietly working on right now? One word.",
    "Who are you becoming? Describe it in 3 words.",
    "What would you do if you knew nobody would judge you?",
]

# Calls to action rotate so captions don't look copy-pasted. Comments, saves and
# shares (sends) are what push a post to non-followers.
CTAS = {
    "tweet": ["Drop a 🖤 if you needed this today.", "Send this to someone who needs it.",
              "Save this for the days you forget.", "Drop a 🖤 to affirm this."],
    "one_liner": ["Drop a 💥 if this hit you.", "Truth. Send this to your group chat.",
                  "Quietly agree? Drop a 💥", "Save this one."],
    "affirmation": ["Type YES to claim it 👇", "Type YES if this is for you 👇",
                    "Comment \"mine\" to claim it 👇"],
    "carousel": ["Which one is you? Comment the number 👇", "Save this and come back to it.",
                 "Send this to someone who's quietly leveling up."],
    "question": ["Comment your answer 👇", "Answer below. No wrong answers 👇"],
}
HASHTAGS = [  # Instagram allows max 5; 3 relevant tags is enough.
    "#quietlyleveling #levelup #selfimprovement",
    "#discipline #mindset #personalgrowth",
    "#levelingup #selfgrowth #motivation",
    "#quietconfidence #growthmindset #dailyreminder",
    "#quietlyleveling #discipline #mindsetshift",
]
TIMES = ["07:30", "12:00", "19:30"]


def posts():
    """Spread each format evenly across the month so formats stay mixed."""
    pools = {"tweet": TWEET, "one_liner": ONE_LINER, "carousel": CAROUSEL,
             "affirmation": AFFIRMATION, "question": QUESTION}
    queue = sorted(((k + 0.5) / len(items), fmt, k, item)
                   for fmt, items in pools.items() for k, item in enumerate(items))
    out = []
    for _, fmt, k, item in queue:
        cta = CTAS[fmt][k % len(CTAS[fmt])]
        if fmt == "carousel":
            hook, points = item
            slides = [hook] + [f"{i}. {p}" for i, p in enumerate(points, 1)] + [CAROUSEL_CLOSER]
        else:
            slides = [item]
        out.append((fmt, slides, cta))
    return out


def caption(fmt, slides, cta, n):
    lines = [cta, "", f"Follow {HANDLE} to level up quietly, one reminder a day.", "",
             HASHTAGS[n % len(HASHTAGS)]]
    if fmt == "carousel":
        lines = [slides[0], ""] + lines
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-10-05", help="first posting day (YYYY-MM-DD)")
    ap.add_argument("--per-day", type=int, default=3, choices=[1, 2, 3])
    args = ap.parse_args()
    start = dt.date.fromisoformat(args.start)
    times = {1: ["19:30"], 2: ["07:30", "19:30"], 3: TIMES}[args.per_day]

    OUT.mkdir(exist_ok=True)
    rows, canva = [], {k: [] for k in CTAS}
    for n, (fmt, slides, cta) in enumerate(posts()):
        post_id = f"QL{n + 1:03d}"
        day, slot = divmod(n, len(times))
        rows.append({
            "post_id": post_id, "date": (start + dt.timedelta(days=day)).isoformat(),
            "time": times[slot], "format": fmt, "slides": len(slides),
            "on_image_text": "\n---\n".join(slides), "caption": caption(fmt, slides, cta, n),
        })
        # Canva rows hold only the text that changes per post; the templates in
        # templates/ draw the name, handle, numbers and closing slide themselves.
        rec = {"post_id": post_id}
        if fmt == "carousel":
            rec["hook"] = slides[0]
            rec.update({f"point{i}": s.split(". ", 1)[1] for i, s in enumerate(slides[1:6], 1)})
        else:
            rec["text"] = slides[0]
        canva[fmt].append(rec)

    with open(OUT / "schedule.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for fmt, recs in canva.items():
        with open(OUT / f"canva_{fmt}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(recs[0]))
            w.writeheader()
            w.writerows(recs)
    print(f"{len(rows)} posts, {rows[0]['date']} to {rows[-1]['date']}, written to {OUT}")


if __name__ == "__main__":
    main()
