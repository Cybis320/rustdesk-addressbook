#!/usr/bin/env bash
# Install a macOS LaunchAgent so the address book server auto-starts at login
# and stays running at http://127.0.0.1:8765. Re-run after moving the project.
#   ./install-login-agent.sh           # install + start
#   ./install-login-agent.sh uninstall # stop + remove
set -euo pipefail

LABEL="com.$(id -un).rustdesk-addressbook"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
PORT="${PORT:-8765}"

if [[ "${1:-}" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

[[ "$(uname)" == "Darwin" ]] || { echo "This installer is macOS-only. On Linux use a systemd --user service running: $PY $DIR/serve.py $PORT --no-open"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DIR/serve.py</string>
    <string>$PORT</string>
    <string>--no-open</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$DIR/serve.log</string>
  <key>StandardErrorPath</key><string>$DIR/serve.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "Installed $LABEL — http://127.0.0.1:$PORT  (logs: $DIR/serve.log)"
