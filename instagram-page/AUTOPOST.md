# Auto-poster

Posts go out on schedule with nobody touching anything. Every 15 minutes, a
GitHub Actions job reads `output/schedule.csv` and publishes whatever is due
through Instagram's official publishing API. It then records the post in
`autopost/published.csv`.

How it fits together:

- **This folder becomes its own public GitHub repo,** for example
  `readthistwice`. Instagram downloads each image from a public web address, so
  the media has to be public. Everything in it goes public on Instagram anyway.
  Keep it separate from your trading bot repo.
- **GitHub Pages serves `posts/` and `reels/`.** This is the `pages.yml`
  workflow.
- **`autopost.yml` publishes** at most one post per run. A post more than 3 hours
  late (for example, if the runner was down) is skipped and logged as missed,
  so posts never go out in a burst.
- **`refresh-token.yml` renews your Instagram token every Monday.** Tokens
  expire after 60 days, so you never have to handle that by hand.

It covers everything from the first post on Monday, October 5 (`start_date`
in `config.toml`). **No Facebook Page is needed.** It logs in with your
Instagram account directly. Reels are off by default (see `config.toml`):
posting through the API can't add trending audio, so schedule those in the
Instagram app.

## One-time setup (about an hour)

### 1. The repo and media hosting
1. Create a **public** repo named `readthistwice` and push the contents of this
   folder to its `main` branch. (I can do this step for you.)
2. In the repo, go to **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Go to **Actions → Publish media → Run workflow**. When it finishes, the run
   page shows the site address, like `https://<your-username>.github.io/readthistwice`.
4. Open `autopost/config.toml` and edit three settings:
   - Set `public_base_url` to that address.
   - Set `timezone` to yours.
   - Check `start_date`.

   Commit the change.

### 2. The Instagram access token
1. Make sure @readthistwice is a **Creator** or **Business** account.
2. Go to [developers.facebook.com](https://developers.facebook.com), then
   **My Apps → Create app**. When asked for a use case, pick the one for
   managing messaging and content on Instagram. Pick **Business** if it asks
   for an app type.
3. In the app, open the Instagram product's **API setup with Instagram login**
   page, then go to **Generate access tokens → Add account**. Log in as
   @readthistwice and copy the token it shows.
   - If the dashboard asks you to add the account as an **Instagram tester**
     first, do that under **App roles → Roles**. Then accept the invite in
     Instagram under **Settings → Website permissions → Apps and websites →
     Tester invites**.
   - You shouldn't need Meta's App Review, because the only account using the
     app is your own. If the dashboard insists on it before it will publish,
     tell me.
4. In the GitHub repo, go to **Settings → Secrets and variables → Actions → New
   repository secret**. Name it `IG_ACCESS_TOKEN` and paste in the token.

### 3. Automatic token renewal
1. On GitHub, go to **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
   - Repository access: only `readthistwice`.
   - Permissions: **Secrets → Read and write**.
   - Expiration: the longest it allows. Put a reminder in your calendar a week
     before that date.
2. Add it as a second repository secret named `SECRETS_PAT`.

### 4. Test it
1. Go to **Actions → Autopost → Run workflow** and choose **check**. It
   confirms the token works, shows your daily posting limit, and checks that
   the next posts' images load from GitHub Pages. Every line should start with
   `ok`.
2. Run it again with **dry-run** to see what would be posted right now, without
   posting anything.
3. Go to **Actions → Refresh Instagram token → Run workflow**. Run this at
   least 24 hours after you created the token; Instagram refuses to renew a
   token younger than that.

After that, the 15-minute schedule takes over.

## Each month
1. Get next month's posts from Claude (see `PROMPT.md`) and paste them into
   `build.py`.
2. Run `python3 build.py --start <first day>`, then
   `node templates/render.js --all posts`. For Reels, add their IDs and dates
   to `templates/reels.js` and run `node templates/reels.js`.
3. Commit and push. GitHub Pages republishes the media on its own, and the
   auto-poster picks up the new dates.

## Keeping an eye on it
- **`autopost/published.csv`** lists every post with its Instagram link, or
  `missed` and the reason.
- **Failed runs show a red ✕** under Actions, and GitHub emails you about them.
  Check that's switched on under GitHub **Settings → Notifications → Actions**.
  A failed run retries on its own 15 minutes later.
- **To pause posting,** go to **Actions → Autopost → … → Disable workflow**.
- **To see what's coming,** run `python3 autopost/poster.py upcoming`.

## Things to know
- **Timing:** GitHub starts scheduled jobs a few minutes late, sometimes 15–30
  minutes when it's busy. Posts go out close to their time, not on the dot.
- **Inactivity pause:** GitHub pauses scheduled jobs in a public repo after 60
  days without a commit. Each post's log entry is a commit, so this only
  happens if posting stops for two months.
- **Previews are public:** anyone who guesses the GitHub Pages address can see
  upcoming images early.
- **Daily limit:** Instagram allows 100 posts a day through its publishing
  API. The schedule uses 3 to 4.
- **Testing locally:** the tests run against a fake Instagram, so they need no
  account: `python3 -m unittest autopost/test_poster.py`.
