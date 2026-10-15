#!/usr/bin/env bash
# Builds jobbot.zip: a clean copy of the job-application bot for someone else.
# It contains none of your resumes, listings, contacts or application history.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=$(mktemp -d)/jobbot
mkdir -p "$OUT/tests"
cp -r jobapply "$OUT/jobapply"
find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +
cp tests/test_jobapply.py "$OUT/tests/"
cp profile.example.toml "$OUT/"
printf 'company,email\n' > "$OUT/contacts.csv"
printf '__pycache__/\n*.egg-info/\nprofile.toml\njobs.json\nletters/\n.env\n' > "$OUT/.gitignore"
cat > "$OUT/pyproject.toml" <<'T'
[project]
name = "jobbot"
version = "0.1.0"
description = "Semi-automatic job application helper"
requires-python = ">=3.11"
dependencies = ["requests>=2.31"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
jobapply = "jobapply.__main__:main"

[tool.setuptools]
packages = ["jobapply"]

[tool.pytest.ini_options]
testpaths = ["tests"]
T
cat > "$OUT/SETUP.md" <<'T'
# Job bot setup (about 3 minutes)

You need Python 3.11 or newer.

```
cd jobbot
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
jobapply init
```

`jobapply init` asks for your name, email, phone, the job titles you want, your city,
the other cities you will commute to, whether remote is OK, your lowest hourly pay, and
your resume file. It writes `profile.toml` for you.

## Every day

```
jobapply search                      # remote jobs that match your titles
jobapply search -f my_jobs.json      # or import a list of jobs (JSON or CSV)
jobapply review --approve-above 1    # approve the matches
jobapply apply --no-email            # opens each job in your browser, you submit it
jobapply applied                     # everything you already applied to
```

- Nothing is ever sent without you pressing Enter on each job.
- `--no-email` means no email password is needed. To also email employers, set
  `SMTP_PASSWORD` to an app password (never your normal password) and leave off `--no-email`.
- Change wording, titles, cities or pay any time with `jobapply edit`.
- Jobs near you come from lists you import (JSON/CSV with title, company, location, url)
  or from an Adzuna account (free keys in ADZUNA_APP_ID and ADZUNA_APP_KEY, then add
  "adzuna" to `sources` in profile.toml). The built-in sources are remote jobs only.
T
mkdir -p dist && rm -f dist/jobbot.zip
(cd "$(dirname "$OUT")" && zip -qr "$OLDPWD/dist/jobbot.zip" jobbot)
echo "Created dist/jobbot.zip"
