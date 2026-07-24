#!/usr/bin/env bash
# Install (or --uninstall) the self-updater: a LaunchAgent that runs
# `ai-smartbar --update` at login and every 6 hours. Installed by default
# from install/macos-swift.sh and install/macos.sh — run it directly only to
# change channel or to opt a device out.
#
#   ./install/macos-update.sh                  # release channel (default)
#   ./install/macos-update.sh --channel main   # follow origin/main (dev box)
#   ./install/macos-update.sh --uninstall
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.ductran.ai-smartbar.update.plist"
INTERVAL="${SMARTBAR_UPDATE_INTERVAL:-21600}"
CHANNEL="${SMARTBAR_UPDATE_CHANNEL:-}"
AGENT_PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall)
      launchctl unload "$PLIST" 2>/dev/null || true
      rm -f "$PLIST"
      echo "Update agent uninstalled — this device no longer self-updates."
      exit 0 ;;
    --channel)   CHANNEL="${2:?--channel needs release|main}"; shift 2 ;;
    --channel=*) CHANNEL="${1#*=}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
# No explicit channel: keep whatever this device is already set to. Both the
# updater (which re-runs the installers) and a human re-running one by hand
# must not silently flip a development box onto the release channel.
if [[ -z "$CHANNEL" && -f "$PLIST" ]]; then
  EXISTING="$(/usr/libexec/PlistBuddy -c \
    'Print :EnvironmentVariables:SMARTBAR_UPDATE_CHANNEL' "$PLIST" 2>/dev/null || true)"
  case "$EXISTING" in release|main) CHANNEL="$EXISTING" ;; esac
fi
CHANNEL="${CHANNEL:-release}"
case "$CHANNEL" in
  release|main) ;;
  *) echo "channel must be 'release' or 'main' (got '$CHANNEL')" >&2; exit 2 ;;
esac

# launchd hands agents a bare PATH and no TTY, and this repo is typically
# PRIVATE — so prove a non-interactive fetch works NOW instead of letting the
# agent fail silently forever (the bug that kept v2's warmup from ever
# firing). Skipped when the updater itself is re-running this installer: it
# just fetched successfully, and a transient network blip must not fail an
# otherwise good update into a rollback.
if [[ "${SMARTBAR_UPDATE_APPLY:-}" != "1" ]]; then
  if env -i PATH="$AGENT_PATH" HOME="$HOME" GIT_TERMINAL_PROMPT=0 \
       git -C "$REPO" ls-remote --quiet origin HEAD >/dev/null 2>&1; then
    echo "Non-interactive fetch OK."
  else
    ORIGIN="$(git -C "$REPO" remote get-url origin 2>/dev/null || echo '<no origin>')"
    cat >&2 <<MSG
ERROR: cannot fetch $ORIGIN without an interactive prompt.
The updater runs from launchd, where nobody can type a password, so it would
never apply anything. Fix one of these and re-run this installer:
  * HTTPS remote: gh auth login && gh auth setup-git   (token -> keychain)
  * SSH remote:   install a key and set origin to git@github.com:...
MSG
    exit 1
  fi
fi

mkdir -p "$HOME/.cache/ai-smartbar" "$(dirname "$PLIST")"
NEW="$(mktemp)"
cat > "$NEW" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ductran.ai-smartbar.update</string>
  <key>ProgramArguments</key>
  <array>
    <string>${REPO}/bin/ai-smartbar</string>
    <string>--update</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>${AGENT_PATH}</string>
    <key>SMARTBAR_UPDATE_CHANNEL</key><string>${CHANNEL}</string>
  </dict>
  <key>StartInterval</key><integer>${INTERVAL}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/.cache/ai-smartbar/update-agent.log</string>
</dict>
</plist>
EOF

if [[ -f "$PLIST" ]] && cmp -s "$NEW" "$PLIST"; then
  rm -f "$NEW"
  echo "Update agent already current (channel=$CHANNEL, every $((INTERVAL/3600))h)."
  exit 0
fi
mv "$NEW" "$PLIST"

if [[ "${SMARTBAR_UPDATE_APPLY:-}" == "1" ]]; then
  # We ARE the update job right now: unloading would kill this very process
  # mid-update. The new plist takes effect at the next login/load.
  echo "Update agent plist refreshed; reload deferred (running inside the job)."
  exit 0
fi
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Update agent installed (channel=$CHANNEL, every $((INTERVAL/3600))h + at login)."
echo "Log: ~/.cache/ai-smartbar/update.log — opt out: $0 --uninstall"
