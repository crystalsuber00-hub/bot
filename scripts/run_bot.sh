#!/usr/bin/env bash
# Starts the bot for one trading day (Mac/Linux). It stops itself at 16:10 ET.
# If it exits with an error, you get a phone alert (ntfy) with the last log lines.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
if [ -f .env ]; then set -a; . ./.env; set +a; fi   # NTFY_TOPIC=... etc.
. .venv/bin/activate
spxbot -c "${SPXBOT_CONFIG:-config.toml}" --until 16:10 >> logs/spxbot.log 2>&1
code=$?
if [ "$code" -ne 0 ] && [ -n "${NTFY_TOPIC:-}" ]; then
  curl -s -H "Title: spxbot stopped with an error" -H "Priority: high" -H "Tags: warning" \
       --data-binary "$(printf 'Exit code %s. Last log lines:\n%s' "$code" "$(tail -n 8 logs/spxbot.log)")" \
       "${NTFY_SERVER:-https://ntfy.sh}/$NTFY_TOPIC" >/dev/null || true
fi
exit "$code"
