#!/usr/bin/env bash
# Install (default) or --uninstall ai-smartbar on Linux. No sudo needed.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HOME/.local/bin/ai-smartbar"
AUTOSTART="$HOME/.config/autostart/ai-smartbar.desktop"
CACHE="$HOME/.cache/ai-smartbar"

if [[ "${1:-}" == "--uninstall" ]]; then
  pkill -f "bin/ai-smartbar" 2>/dev/null || true
  rm -f "$BIN" "$AUTOSTART"
  rm -rf "$CACHE"
  echo "ai-smartbar uninstalled."
  exit 0
fi

mkdir -p "$(dirname "$BIN")" "$(dirname "$AUTOSTART")"
ln -sf "$REPO/bin/ai-smartbar" "$BIN"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=AI smartbar
Comment=Claude usage limits in the system tray
Exec=$BIN
Icon=dialog-information
X-GNOME-Autostart-enabled=true
EOF
echo "Installed $BIN and autostart entry. Starting..."
nohup "$BIN" >/dev/null 2>&1 &
sleep 2
pgrep -f "bin/ai-smartbar" >/dev/null && echo "ai-smartbar is running." \
  || { echo "FAILED to start — check $CACHE/tray.log"; exit 1; }
