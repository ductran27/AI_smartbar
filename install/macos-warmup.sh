#!/usr/bin/env bash
# Install (or --uninstall) the auto window-starter: a LaunchAgent that runs
# `ai-smartbar --warmup-once` every 10 minutes. Installing this agent IS the
# opt-in — nothing warms without it. See the warmup spec in docs/superpowers.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.ductran.ai-smartbar.warmup.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "AI_smartbar warmup agent uninstalled."
  exit 0
fi

command -v cswap >/dev/null || [[ -x "$HOME/.local/bin/cswap" ]] \
  || { echo "Install claude-swap first (pipx install claude-swap)"; exit 1; }
command -v claude >/dev/null || [[ -x "$HOME/.local/bin/claude" ]] \
  || { echo "Install the claude CLI first (the warmup pings through it)"; exit 1; }

# launchd hands agents a bare PATH (/usr/bin:/bin) — cswap resolves the
# claude CLI via PATH, so bake the usual install dirs in. The runner also
# hardens its subprocess PATH itself; this is belt and suspenders.
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ductran.ai-smartbar.warmup</string>
  <key>ProgramArguments</key>
  <array>
    <string>${REPO}/bin/ai-smartbar</string>
    <string>--warmup-once</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/.cache/ai-smartbar/warmup-agent.log</string>
</dict>
</plist>
EOF
mkdir -p "$HOME/.cache/ai-smartbar"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Warmup agent installed (every 10 min, gate-checked)."
echo "Log: ~/.cache/ai-smartbar/warmup.log — uninstall: $0 --uninstall"
