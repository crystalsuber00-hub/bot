#!/usr/bin/env bash
# Auto-start the bot every weekday on Linux (cron). Usage: scripts/install_linux.sh [HH:MM in ET, default 09:15]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
read -r H M < <(python3 "$ROOT/scripts/local_time.py" "${1:-09:15}")
LINE="$((10#$M)) $((10#$H)) * * 1-5 /bin/bash $ROOT/scripts/run_bot.sh  # spxbot"
( crontab -l 2>/dev/null | grep -v '# spxbot$' || true; echo "$LINE" ) | crontab -
echo "Installed cron: $LINE"
echo "Local time $H:$M = ${1:-09:15} ET. Machine must be on and IB Gateway logged in. Logs: $ROOT/logs/"
echo "Remove with: crontab -l | grep -v '# spxbot\$' | crontab -"
