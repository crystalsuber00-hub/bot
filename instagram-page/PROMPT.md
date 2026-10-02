# Prompt for the next month of posts

Paste this into Claude once a month. Fill in the bracketed parts from Instagram
Insights. Then copy the new lines into the lists in `build.py` and run
`python3 build.py --start <first day of the month>`, then `node templates/render.js`.
The render step fails and names the post if any new line is too long for its
template.

---

I run a faceless Instagram page called **Quietly Leveling (@quietlyleveling)**:
mindset, discipline, quiet growth, self-respect. The audience is people aged
20–35 who are trying to level up quietly. The voice is calm, direct and a little
blunt. It's never preachy, has no hustle-bro clichés and uses no religious
language. Every post should feel like private progress: discipline, outgrowing
old habits, calm confidence, results instead of announcements. Skip
manifestation, dating and relationship content.

Write next month's posts in these formats:
- **[35] tweet cards:** one or two sentences, 8–25 words, that read like a
  relatable tweet. Specific beats generic ("Healing made me boring and I've
  never been more at peace").
- **[25] one-liners:** under 10 words, with a punchy twist or contrast
  ("Overthinking is just fear with a calendar").
- **[15] affirmations:** first person, under 12 words, starting with "I"
  or "My".
- **[15] carousels:** a hook title plus exactly 5 short points, each under 8
  words. The hook must be a list promise ("5 signs…", "Things I stopped…",
  "How to…").
- **[10] questions:** a short question that is easy to answer in a comment
  (pick one, a number, or one word).

Rules:
- Everything must be original. Don't use famous quotes or quotes credited to
  anyone, and don't lightly reword well-known sayings.
- Don't repeat or paraphrase any of these existing posts: [paste the current
  lists from build.py]
- Last month's top posts by shares and saves were: [paste the top 5 and their
  formats]. Write more in that style and on those themes.
- Last month's weakest posts were: [paste the bottom 5]. Avoid those angles.

Return the result as Python lists I can paste into build.py: TWEET, ONE_LINER,
AFFIRMATION, QUESTION (lists of strings) and CAROUSEL (a list of
`("hook", ["p1", "p2", "p3", "p4", "p5"])` tuples).
