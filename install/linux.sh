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
# Published into the icon THEME rather than referenced in place, because the
# .desktop entry and notify-send both resolve a bare NAME through the theme
# and neither can see a path inside the checkout.
ICON_SRC="$REPO/assets/ai-smartbar.png"
ICON_SIZE=512
ICON_DIR="$HOME/.local/share/icons/hicolor/${ICON_SIZE}x${ICON_SIZE}/apps"
ICON_NAME="ai-smartbar"

install_icon() {
  # Redrawn at exactly the theme directory's size when this box can draw --
  # pycairo is already a hard dependency of the tray, and app_icon renders
  # vector, so this is a clean 512 and not a resample of the 1024 asset.
  # Copying it verbatim is the fallback: an oversized file in a sized
  # directory still resolves, it is merely scaled at lookup time.
  mkdir -p "$ICON_DIR" 2>/dev/null || return 0
  PYTHONPATH="$REPO" python3 -m smartbar.paint.app_icon \
      "$ICON_DIR/$ICON_NAME.png" "$ICON_SIZE" >/dev/null 2>&1 \
    || cp -f "$ICON_SRC" "$ICON_DIR/$ICON_NAME.png" 2>/dev/null \
    || return 0
  # Some panels only notice a new icon after the theme cache is rebuilt;
  # absent on minimal installs, and the icon still resolves without it.
  if command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" \
      >/dev/null 2>&1 || true
  fi
}
UNITS="$HOME/.config/systemd/user"
CHANNEL="${SMARTBAR_UPDATE_CHANNEL:-}"
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
      rm -f "$BIN" "$AUTOSTART" "$ICON_DIR/$ICON_NAME.png"
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

# How often to check for a release. Was hardcoded to 6h here, which silently
# ignored the documented SMARTBAR_UPDATE_INTERVAL on Linux entirely. The CLI
# resolves it (env, then config.env, then the default, floored) and renders the
# crontab spec, so cron's minute-resolution arithmetic is unit-tested rather
# than open-coded in shell. Defaults reproduce the old `17 */6 * * *` exactly.
INTERVAL_SEC="$("$REPO/bin/ai-smartbar" --update-interval 2>/dev/null || echo 21600)"
INTERVAL_CRON="$("$REPO/bin/ai-smartbar" --update-interval cron 2>/dev/null \
  || echo '17 */6 * * *')"

install_icon
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=AI smartbar
Comment=Claude usage limits in the system tray
Exec=${CONFIG_EXEC}$BIN
Icon=${ICON_NAME}
X-GNOME-Autostart-enabled=true
EOF

install_updater() {
  # Every failure below MUST be an explicit `return 1`. `set -e` is suspended
  # for this whole function body, because the caller invokes it as the left
  # operand of `||` and bash disables errexit inside any command used as a
  # condition — recursively. So a bare `systemctl enable` that fails would not
  # abort the function; it would carry on to the success echo, and the outer
  # WARNING would never fire because the function still returned 0. That is
  # the exact inverse of what the wrapper is for.
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
OnUnitActiveSec=${INTERVAL_SEC}s
Persistent=true
[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload || return 1
    systemctl --user enable --now ai-smartbar-update.timer || return 1
    echo "Update timer enabled (channel=$CHANNEL, every ${INTERVAL_SEC}s)."
  elif command -v crontab >/dev/null; then
    # `crontab -l` legitimately fails when there is no crontab yet, so its
    # own failure stays tolerated — but the write must not.
    { crontab -l 2>/dev/null | grep -v "ai-smartbar --update"
      echo "$INTERVAL_CRON SMARTBAR_UPDATE_CHANNEL=$CHANNEL ${CONFIG_EXEC}$REPO/bin/ai-smartbar --update"
    } | crontab - || return 1
    echo "Update cron entry installed (channel=$CHANNEL, '$INTERVAL_CRON')."
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
