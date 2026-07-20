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

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  rm -rf "$APP_DIR"
  echo "AI_smartbar.app uninstalled."
  exit 0
fi

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
cat > "$APP_DIR/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.ductran.ai-smartbar</string>
  <key>CFBundleName</key><string>AI smartbar</string>
  <key>CFBundleExecutable</key><string>AISmartbar</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
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
echo "AI_smartbar (native SwiftUI) installed — check the menu bar."
echo "Log: ~/Library/Logs/ai-smartbar.log"
