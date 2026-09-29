#!/usr/bin/env bash
# Starts the bot for one trading day (Mac/Linux). It stops itself at 16:10 ET.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
if [ -f .env ]; then set -a; . ./.env; set +a; fi   # NTFY_TOPIC=... etc.
. .venv/bin/activate
exec spxbot -c config.toml --until 16:10 >> logs/spxbot.log 2>&1
