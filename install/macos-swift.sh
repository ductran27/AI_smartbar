#!/usr/bin/env bash
# Build + install the NATIVE SwiftUI menu-bar app (recommended macOS UI),
# or --uninstall. Requires macOS 13+, Xcode Command Line Tools, claude-swap.
# Takes over the com.ductran.ai-smartbar LaunchAgent label from the Python
# variant, so only one AI_smartbar starts at login.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="$REPO/macos-swift"
APP_DIR="$HOME/Applications/AI_smartbar.app"
PLIST="$HOME/Library/LaunchAgents/com.ductran.ai-smartbar.plist"

# Canonical version lives in smartbar/__init__.py; install/release.sh bumps it
# there and everything else (this bundle, Version.swift, the git tag) follows.
VERSION="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$REPO/smartbar/__init__.py")"
VERSION="${VERSION:-0.0.0}"

AUTO_UPDATE=1
CHANNEL_ARG=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall)
      launchctl unload "$PLIST" 2>/dev/null || true
      rm -f "$PLIST"
      rm -rf "$APP_DIR"
      "$REPO/install/macos-update.sh" --uninstall >/dev/null 2>&1 || true
      echo "AI_smartbar.app uninstalled."
      exit 0 ;;
    --no-auto-update) AUTO_UPDATE=0; shift ;;
    --channel)   CHANNEL_ARG=(--channel "${2:?--channel needs release|main}"); shift 2 ;;
    --channel=*) CHANNEL_ARG=(--channel "${1#*=}"); shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v swift >/dev/null \
  || { echo "Swift toolchain missing — run: xcode-select --install"; exit 1; }
command -v cswap >/dev/null || [[ -x "$HOME/.local/bin/cswap" ]] \
  || { echo "Install claude-swap first (pipx install claude-swap; then cswap add)"; exit 1; }

echo "Building AI_smartbar (release)…"
swift build -c release --package-path "$PKG"
BIN="$(swift build -c release --package-path "$PKG" --show-bin-path)/AISmartbar"
[[ -x "$BIN" ]] || { echo "Build produced no binary at $BIN"; exit 1; }

echo "Bundling ${APP_DIR}…"
mkdir -p "$APP_DIR/Contents/MacOS"
cp "$BIN" "$APP_DIR/Contents/MacOS/AISmartbar"
cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.ductran.ai-smartbar</string>
  <key>CFBundleName</key><string>AI smartbar</string>
  <key>CFBundleExecutable</key><string>AISmartbar</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
EOF

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ductran.ai-smartbar</string>
  <key>ProgramArguments</key>
  <array><string>$APP_DIR/Contents/MacOS/AISmartbar</string></array>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/ai-smartbar.log</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "AI_smartbar $VERSION (native SwiftUI) installed — check the menu bar."
echo "Log: ~/Library/Logs/ai-smartbar.log"

# Self-updating is ON by default: a device nobody re-installs by hand should
# still pick up the next release. --no-auto-update opts this device out.
if [[ "$AUTO_UPDATE" == "1" ]]; then
  "$REPO/install/macos-update.sh" ${CHANNEL_ARG[@]+"${CHANNEL_ARG[@]}"} || \
    echo "WARNING: update agent NOT installed — this device will not self-update." >&2
fi
