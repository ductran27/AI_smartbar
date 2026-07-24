#!/usr/bin/env bash
# Install (default) or --uninstall ai-smartbar on Linux. No sudo needed.
# Also installs the self-updater (systemd user timer, cron fallback) unless
# --no-auto-update is given. Re-running this script is the update apply step,
# so it must always end with exactly one tray running.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HOME/.local/bin/ai-smartbar"
AUTOSTART="$HOME/.config/autostart/ai-smartbar.desktop"
CACHE="$HOME/.cache/ai-smartbar"
UNITS="$HOME/.config/systemd/user"
CHANNEL="${SMARTBAR_UPDATE_CHANNEL:-}"
INTERVAL_H=6
AGENT_PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
# Matches the no-argument tray only: `--update` / `--warmup-once` invocations
# must never be caught, or an update would kill itself here.
TRAY_PATTERN="ai-smartbar\$"

remove_timer() {
  if command -v systemctl >/dev/null; then
    systemctl --user disable --now ai-smartbar-update.timer 2>/dev/null || true
  fi
  rm -f "$UNITS/ai-smartbar-update.timer" "$UNITS/ai-smartbar-update.service"
  if command -v crontab >/dev/null; then
    crontab -l 2>/dev/null | grep -v "ai-smartbar --update" | crontab - 2>/dev/null || true
  fi
}

AUTO_UPDATE=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall)
      pkill -f "$TRAY_PATTERN" 2>/dev/null || true
      remove_timer
      rm -f "$BIN" "$AUTOSTART"
      rm -rf "$CACHE"
      echo "ai-smartbar uninstalled."
      exit 0 ;;
    --no-auto-update) AUTO_UPDATE=0; shift ;;
    --channel)   CHANNEL="${2:?--channel needs release|main}"; shift 2 ;;
    --channel=*) CHANNEL="${1#*=}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
# No explicit channel: keep whatever this device already uses. Re-running an
# installer (the updater does exactly that) must not flip a development box
# onto the release channel behind the user's back.
if [[ -z "$CHANNEL" ]]; then
  # `|| true` is load-bearing: on a FRESH install there is no unit and no
  # crontab, and sed and `crontab -l` both exit non-zero for a missing one.
  # Under `set -euo pipefail` that status propagates out of the assignment and
  # kills the installer here — before the symlink, before the autostart entry,
  # before anything. macOS got this right with `|| true` on its PlistBuddy
  # read-back; this side did not, so no fresh Linux install could complete.
  EXISTING="$(sed -n 's/^Environment=SMARTBAR_UPDATE_CHANNEL=//p' \
      "$UNITS/ai-smartbar-update.service" 2>/dev/null | head -1 || true)"
  if [[ -z "$EXISTING" ]] && command -v crontab >/dev/null; then
    EXISTING="$(crontab -l 2>/dev/null \
      | sed -n 's/.*SMARTBAR_UPDATE_CHANNEL=\([a-z]*\).*/\1/p' | head -1 || true)"
  fi
  case "$EXISTING" in release|main) CHANNEL="$EXISTING" ;; esac
fi
CHANNEL="${CHANNEL:-release}"
case "$CHANNEL" in
  release|main) ;;
  *) echo "channel must be 'release' or 'main' (got '$CHANNEL')" >&2; exit 2 ;;
esac

mkdir -p "$(dirname "$BIN")" "$(dirname "$AUTOSTART")" "$CACHE"
ln -sf "$REPO/bin/ai-smartbar" "$BIN"

# This device's settings. Both files below are rewritten from scratch on every
# update (applying an update IS re-running this script), so anything edited
# into them by hand is lost — ~/.config/ai-smartbar/config.env is the durable
# place, folded in here and therefore re-applied every time. Both renderings
# collapse to nothing when the file is absent, leaving these units byte-identical
# to what they were before. Never fatal: no config must still mean a working unit.
CONFIG_EXEC="$("$REPO/bin/ai-smartbar" --print-config exec || true)"
CONFIG_SYSTEMD="$("$REPO/bin/ai-smartbar" --print-config systemd || true)"

cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=AI smartbar
Comment=Claude usage limits in the system tray
Exec=${CONFIG_EXEC}$BIN
Icon=dialog-information
X-GNOME-Autostart-enabled=true
EOF

install_updater() {
  # Prove a non-interactive fetch works before promising updates; skipped when
  # the updater is re-running this script (it just fetched, and a network blip
  # must not turn a good update into a rollback).
  if [[ "${SMARTBAR_UPDATE_APPLY:-}" != "1" ]]; then
    if env -i PATH="$AGENT_PATH" HOME="$HOME" GIT_TERMINAL_PROMPT=0 \
         git -C "$REPO" ls-remote --quiet origin HEAD >/dev/null 2>&1; then
      echo "Non-interactive fetch OK."
    else
      echo "ERROR: cannot fetch origin without a prompt — add an SSH key or a" >&2
      echo "       stored credential helper, then re-run this installer." >&2
      return 1
    fi
  fi
  if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "$UNITS"
    cat > "$UNITS/ai-smartbar-update.service" <<EOF
[Unit]
Description=AI smartbar self-update
[Service]
Type=oneshot
# The tray this service restarts must OUTLIVE the service; without this
# systemd tears down the whole cgroup and takes the new tray with it.
KillMode=process
Environment=PATH=$AGENT_PATH
Environment=SMARTBAR_UPDATE_CHANNEL=$CHANNEL${CONFIG_SYSTEMD}
ExecStart=$REPO/bin/ai-smartbar --update
EOF
    cat > "$UNITS/ai-smartbar-update.timer" <<EOF
[Unit]
Description=AI smartbar update check
[Timer]
OnBootSec=2min
OnUnitActiveSec=${INTERVAL_H}h
Persistent=true
[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now ai-smartbar-update.timer
    echo "Update timer enabled (channel=$CHANNEL, every ${INTERVAL_H}h)."
  elif command -v crontab >/dev/null; then
    { crontab -l 2>/dev/null | grep -v "ai-smartbar --update"
      echo "17 */$INTERVAL_H * * * SMARTBAR_UPDATE_CHANNEL=$CHANNEL ${CONFIG_EXEC}$REPO/bin/ai-smartbar --update"
    } | crontab -
    echo "Update cron entry installed (channel=$CHANNEL, every ${INTERVAL_H}h)."
  else
    echo "WARNING: neither systemd --user nor crontab available — no auto-update." >&2
    return 1
  fi
}

if [[ "$AUTO_UPDATE" == "1" ]]; then
  install_updater || \
    echo "WARNING: this device will not self-update (see above)." >&2
fi

# Exactly one tray: stop any previous instance before starting the new code.
pkill -f "$TRAY_PATTERN" 2>/dev/null || true
sleep 1
setsid nohup "$BIN" >/dev/null 2>&1 &
sleep 2
pgrep -f "$TRAY_PATTERN" >/dev/null && echo "ai-smartbar is running." \
  || { echo "FAILED to start — check $CACHE/tray.log"; exit 1; }
