#!/usr/bin/env bash
# config.env has to actually REACH the agents. The parsing and rendering are
# unit-tested; what those cannot cover is the splice point — a `${CONFIG_PLIST}`
# in the wrong place produces a plist that will not load, or a .desktop whose
# Exec line runs nothing, on a device nobody is watching. So this drives the
# REAL installers and reads back the files they wrote.
#
# Contained by PATH order, not by trust: every tool that would touch the
# tester's machine (launchctl, systemctl, crontab, pkill, setsid, nohup) is
# shadowed by a no-op stub, and HOME plus SMARTBAR_CONFIG_DIR point into a
# temporary directory. Nothing here can disturb real agents or processes.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

STUB="$WORK/stub"; mkdir -p "$STUB"
for tool in launchctl systemctl crontab pkill setsid nohup cswap claude pgrep; do
  printf '#!/bin/sh\nexit 0\n' > "$STUB/$tool"
  chmod +x "$STUB/$tool"
done

export HOME="$WORK/home"
export SMARTBAR_CONFIG_DIR="$WORK/config"
export SMARTBAR_UPDATE_APPLY=1   # skip the credential probe; we are not updating
export PATH="$STUB:$PATH"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.local/bin" \
         "$HOME/.config/autostart" "$HOME/.config/systemd/user" \
         "$SMARTBAR_CONFIG_DIR"

APP_PLIST="$HOME/Library/LaunchAgents/com.ductran.ai-smartbar.plist"
WARM_PLIST="$HOME/Library/LaunchAgents/com.ductran.ai-smartbar.warmup.plist"
UPD_PLIST="$HOME/Library/LaunchAgents/com.ductran.ai-smartbar.update.plist"
AUTOSTART="$HOME/.config/autostart/ai-smartbar.desktop"
SERVICE="$HOME/.config/systemd/user/ai-smartbar-update.service"

fail() { echo "FAIL: $*" >&2; exit 1; }
IS_MAC=0; [ "$(uname -s)" = "Darwin" ] && IS_MAC=1

write_config() { cat > "$SMARTBAR_CONFIG_DIR/config.env"; }

# A value with a space is the sharp case: it must arrive as ONE assignment in
# all three formats, or `env` treats the tail of a path as a command.
write_config <<'EOF'
# this device runs hot
SMARTBAR_INTERVAL=90
SMARTBAR_WARMUP_DAILY_CAP=3
SMARTBAR_CSWAP=/opt/my tools/cswap
SMARTBAR_UPDATE_CHANNEL=main
EOF

echo "e2e-config: settings reach the agents"

# --- A: the updater's own agent, and the reserved key --------------------
if [ "$IS_MAC" = "1" ]; then
  "$REPO/install/macos-update.sh" --channel release >/dev/null 2>"$WORK/a.err"
  plutil -lint "$UPD_PLIST" >/dev/null || fail "A: update plist is not valid"
  grep -q "SMARTBAR_INTERVAL" "$UPD_PLIST" || fail "A: settings did not land"
  grep -q "<string>/opt/my tools/cswap</string>" "$UPD_PLIST" \
    || fail "A: a value with a space did not survive"
  # config.env asked for channel=main; --channel said release. The channel has
  # its own flag and its own read-back, so the config file must NOT win — two
  # sources for one key is how the halves of a system come to disagree.
  plutil -p "$UPD_PLIST" | grep -q '"SMARTBAR_UPDATE_CHANNEL" => "release"' \
    || fail "A: config.env overrode the channel it is not allowed to set"
  grep -q "installer itself" "$WORK/a.err" \
    || fail "A: the reserved key was dropped without saying so"
  echo "  A: update agent carries the settings, channel stays authoritative"
fi

# --- B: the warmup agent ------------------------------------------------
if [ "$IS_MAC" = "1" ]; then
  "$REPO/install/macos-warmup.sh" >/dev/null 2>&1
  plutil -lint "$WARM_PLIST" >/dev/null || fail "B: warmup plist is not valid"
  grep -q "SMARTBAR_WARMUP_DAILY_CAP" "$WARM_PLIST" || fail "B: settings missing"
  grep -q "<key>PATH</key>" "$WARM_PLIST" \
    || fail "B: the baked PATH was lost (launchd gives agents a bare one)"
  echo "  B: warmup agent carries the settings and keeps its PATH"
fi

# --- C: Linux's two files ------------------------------------------------
"$REPO/install/linux.sh" >/dev/null 2>&1 || true   # stubs make the tray "start"
[ -f "$AUTOSTART" ] || fail "C: no autostart entry was written"
grep -q '^Exec=env ' "$AUTOSTART" || fail "C: Exec has no env prefix"
grep -q '"SMARTBAR_CSWAP=/opt/my tools/cswap"' "$AUTOSTART" \
  || fail "C: the spaced value is not one quoted assignment in Exec"
[ -f "$SERVICE" ] || fail "C: no systemd unit was written"
grep -q '^Environment="SMARTBAR_INTERVAL=90"$' "$SERVICE" \
  || fail "C: systemd Environment line missing or misquoted"
grep -q '^ExecStart=' "$SERVICE" || fail "C: ExecStart was displaced"
grep -q '^Environment=SMARTBAR_UPDATE_CHANNEL=release$' "$SERVICE" \
  || fail "C: config.env overrode the channel on Linux too"
# The Exec line has to split the way a desktop file parser would.
python3 - "$AUTOSTART" <<'PY' || fail "C: Exec does not split into a sane argv"
import shlex, sys
line = next(l for l in open(sys.argv[1]) if l.startswith("Exec="))
argv = shlex.split(line[len("Exec="):].strip())
assert argv[0] == "env", argv
assert argv[-1].endswith("/ai-smartbar"), argv
assert "SMARTBAR_CSWAP=/opt/my tools/cswap" in argv, argv
PY
echo "  C: autostart Exec and the systemd unit both carry the settings"

# --- D: no config at all leaves the units exactly as they were ----------
# The commonest case by far. An empty render must not leave a stray `env`, a
# blank line inside a plist dict, or anything else that changes on every pass.
rm -f "$SMARTBAR_CONFIG_DIR/config.env"
"$REPO/install/linux.sh" >/dev/null 2>&1 || true
grep -q "^Exec=$HOME/.local/bin/ai-smartbar$" "$AUTOSTART" \
  || fail "D: Exec is not clean without a config: $(grep '^Exec=' "$AUTOSTART")"
grep -q "SMARTBAR_INTERVAL" "$SERVICE" && fail "D: stale setting left behind"
if [ "$IS_MAC" = "1" ]; then
  "$REPO/install/macos-update.sh" --channel release >/dev/null 2>&1
  plutil -lint "$UPD_PLIST" >/dev/null \
    || fail "D: plist invalid when the config is absent"
  grep -q "SMARTBAR_INTERVAL" "$UPD_PLIST" && fail "D: stale setting in plist"
  # Byte-for-byte identical across two runs, or every update churns the unit.
  cp "$UPD_PLIST" "$WORK/first.plist"
  "$REPO/install/macos-update.sh" --channel release >/dev/null 2>&1
  cmp -s "$WORK/first.plist" "$UPD_PLIST" \
    || fail "D: re-running the installer changed the plist"
fi
echo "  D: no config leaves the units clean and byte-identical"

# --- E: a config that tries to set something it must not ----------------
write_config <<'EOF'
PATH=/tmp/evil
DYLD_INSERT_LIBRARIES=/tmp/evil.dylib
SMARTBAR_INTERVAL=45
EOF
"$REPO/install/linux.sh" >/dev/null 2>"$WORK/e.err" || true
grep -q "not a SMARTBAR_" "$WORK/e.err" || fail "E: no warning about PATH"
grep -q '"PATH=/tmp/evil"' "$AUTOSTART" && fail "E: PATH was injected into Exec"
grep -q "DYLD" "$AUTOSTART" && fail "E: DYLD_INSERT_LIBRARIES was injected"
grep -q '"SMARTBAR_INTERVAL=45"' "$AUTOSTART" \
  || fail "E: the good setting was lost along with the bad ones"
if [ "$IS_MAC" = "1" ]; then
  "$REPO/install/macos-update.sh" --channel release >/dev/null 2>&1
  plutil -p "$UPD_PLIST" | grep -q '"PATH" => "/tmp/evil"' \
    && fail "E: PATH was overridden in the agent"
  grep -q "DYLD" "$UPD_PLIST" && fail "E: DYLD key reached the agent"
fi
echo "  E: non-SMARTBAR keys cannot reach an agent's environment"

echo "e2e-config: all scenarios passed"
