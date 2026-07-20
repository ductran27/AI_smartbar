#!/usr/bin/env bash
# Install (default) or --uninstall ai-smartbar on macOS.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUPPORT="$HOME/Library/Application Support/ai-smartbar"
VENV="$SUPPORT/venv"
PLIST="$HOME/Library/LaunchAgents/com.ductran.ai-smartbar.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  rm -rf "$SUPPORT"
  echo "ai-smartbar uninstalled."
  exit 0
fi

command -v cswap >/dev/null || { echo "Install claude-swap first (pipx install claude-swap)"; exit 1; }
mkdir -p "$SUPPORT"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip rumps
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ductran.ai-smartbar</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python3</string>
    <string>$REPO/bin/ai-smartbar</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/ai-smartbar.log</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "ai-smartbar installed — check your menu bar."
