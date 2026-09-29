#!/usr/bin/env bash
# Auto-start the bot every weekday on macOS (launchd). Usage: scripts/install_mac.sh [HH:MM in ET, default 09:15]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
read -r H M < <(python3 "$ROOT/scripts/local_time.py" "${1:-09:15}")
PLIST="$HOME/Library/LaunchAgents/com.spxbot.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
{
cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.spxbot</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$ROOT/scripts/run_bot.sh</string></array>
  <key>StartCalendarInterval</key><array>
XML
for D in 1 2 3 4 5; do
  echo "    <dict><key>Weekday</key><integer>$D</integer><key>Hour</key><integer>$((10#$H))</integer><key>Minute</key><integer>$((10#$M))</integer></dict>"
done
cat <<XML
  </array>
  <key>StandardErrorPath</key><string>$ROOT/logs/launchd.err</string>
</dict></plist>
XML
} > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed: bot starts Mon-Fri at $H:$M local time (= ${1:-09:15} ET). Logs: $ROOT/logs/"
WAKE=$(python3 -c "from datetime import datetime,timedelta as t; print((datetime(2000,1,1,$((10#$H)),$((10#$M)))-t(minutes=5)).strftime('%H:%M:%S'))")
echo "Your Mac must be awake then. To wake it 5 minutes early, run: sudo pmset repeat wakeorpoweron MTWRF $WAKE"
echo "Remove with: launchctl unload $PLIST && rm $PLIST"
