#!/usr/bin/env bash
# One-time setup on a Mac: Python check, virtual env, install, config files. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer is needed (found: $(python3 --version 2>&1))."
  echo "Install it with Homebrew:  brew install python@3.12   then run this script again."
  echo "(No Homebrew? Get it from https://brew.sh or install Python from https://www.python.org/downloads/)"
  exit 1
fi
echo "[ok] using $($PY --version)"

[ -d .venv ] || "$PY" -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e .
echo "[ok] bot installed in .venv"

[ -f stocks.toml ] || { cp config.stocks.example.toml stocks.toml; echo "[ok] created stocks.toml (edit the watchlist there if you like)"; }
[ -f .env ] || { cp .env.example .env; echo "[ok] created .env"; }

missing=""
. ./.env
[ -n "${NTFY_TOPIC:-}" ] && [ "$NTFY_TOPIC" != "your-hard-to-guess-topic" ] || missing="$missing NTFY_TOPIC"
[ -n "${SCHWAB_APP_KEY:-}" ] || missing="$missing SCHWAB_APP_KEY"
[ -n "${SCHWAB_APP_SECRET:-}" ] || missing="$missing SCHWAB_APP_SECRET"
if [ -n "$missing" ]; then
  echo
  echo "Next: open .env in a text editor (open -e .env) and fill in:$missing"
  echo "Then run this script again."
  exit 0
fi
echo "[ok] .env has your ntfy topic and Schwab key"

if [ ! -f schwab_token.json ]; then
  echo
  echo "Next: log in to Schwab (repeat once a week):"
  echo "  . .venv/bin/activate && set -a && . ./.env && set +a && spxbot -c stocks.toml --schwab-login"
  echo "then:  spxbot -c stocks.toml --check"
  exit 0
fi
echo
echo "All set. Test:   . .venv/bin/activate && set -a && . ./.env && set +a && spxbot -c stocks.toml --check"
echo "Auto-start every weekday at 9:15 ET:   bash scripts/install_mac.sh"
