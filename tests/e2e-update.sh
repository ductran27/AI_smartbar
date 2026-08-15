#!/usr/bin/env bash
# Self-update E2E against a throwaway git "origin" and a recording installer.
# This is the only check that covers devices we cannot physically test, so it
# exercises the whole loop end to end, including the destructive paths:
#   A  a pinned device sees a newer release            (--check-update -> 10)
#   B  it applies it, re-running its installer         (HEAD moves, 1 install)
#   C  a second run is a no-op                         (current, no install)
#   D  a dirty tree refuses to update                  (blocked, HEAD frozen)
#   E  --reset updates anyway and parks the work        (rescue ref exists)
#   F  a release whose install fails is rolled back    (HEAD restored)
#
# Nothing here touches the real repo, the real LaunchAgents (see
# SMARTBAR_UPDATE_TARGETS) or the real HOME.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL="$(cd "$HERE/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

export HOME="$WORK/home"                       # never the tester's HOME
export SMARTBAR_CACHE_DIR="$WORK/cache"
export SMARTBAR_UPDATE_TARGETS=linux           # never the real install shapes
export SMARTBAR_UPDATE_NOTIFY=off
export E2E_INSTALL_LOG="$WORK/installs.log"
mkdir -p "$HOME" "$SMARTBAR_CACHE_DIR"
: > "$E2E_INSTALL_LOG"

GIT=(git -c user.name=e2e -c user.email=e2e@localhost -c commit.gpgsign=false \
         -c init.defaultBranch=main -c advice.detachedHead=false)

fail() { echo "FAIL: $*" >&2; exit 1; }
device() { "${GIT[@]}" -C "$WORK/device" "$@"; }
# Release tag only: the junk tags below deliberately share a commit with a
# release, and `nightly` would otherwise sort first.
head_tag() {
  device tag --points-at HEAD | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1
}
installs() { wc -l < "$E2E_INSTALL_LOG" | tr -d ' '; }
run_update() { (cd "$WORK/device" && python3 ./bin/ai-smartbar "$@"); }

set_version() {  # $1 = repo dir, $2 = version
  printf '__version__ = "%s"\n' "$2" > "$1/smartbar/__init__.py"
}

# --- build the fake origin from the CURRENT working tree -------------------
# A clone of real history would test yesterday's updater; we want this one.
mkdir -p "$WORK/src"
tar -C "$REAL" --exclude .git --exclude .build --exclude __pycache__ \
    --exclude '*.pyc' -cf - . | tar -C "$WORK/src" -xf -

# The installer the runner will re-run: records its invocation, and fails on
# demand so the rollback path is covered too.
cat > "$WORK/src/install/linux.sh" <<'INSTALLER'
#!/usr/bin/env bash
echo "installed $(git -C "$(dirname "$0")/.." describe --tags --always 2>/dev/null)" \
  >> "${E2E_INSTALL_LOG:?}"
[[ "${E2E_FAIL_INSTALL:-}" == "1" ]] && exit 1
exit 0
INSTALLER
chmod +x "$WORK/src/install/linux.sh"

cut_release() {  # $1 = version -> commits and tags v$1 in the fake origin
  set_version "$WORK/src" "$1"
  "${GIT[@]}" -C "$WORK/src" add -A
  "${GIT[@]}" -C "$WORK/src" commit -q -m "release: v$1"
  "${GIT[@]}" -C "$WORK/src" tag "v$1"
}

"${GIT[@]}" -C "$WORK/src" init -q
cut_release 0.0.1
cut_release 0.0.2
# Junk refs the selector must ignore (lexicographic ordering would pick these).
"${GIT[@]}" -C "$WORK/src" tag nightly
"${GIT[@]}" -C "$WORK/src" tag v0.0.10-rc1

"${GIT[@]}" clone -q "$WORK/src" "$WORK/device"
device checkout -q --detach v0.0.1
[[ "$(head_tag)" == "v0.0.1" ]] || fail "device not pinned to v0.0.1"

# --- A: a newer release is visible ----------------------------------------
set +e; run_update --check-update >/dev/null; CODE=$?; set -e
[[ "$CODE" == "10" ]] || fail "A: --check-update returned $CODE, wanted 10"
[[ "$(head_tag)" == "v0.0.1" ]] || fail "A: --check-update moved HEAD"
[[ "$(installs)" == "0" ]] || fail "A: --check-update ran an installer"

# --- B: it applies, and re-runs the device's installer ---------------------
run_update --update || fail "B: update exited non-zero"
[[ "$(head_tag)" == "v0.0.2" ]] || fail "B: HEAD is $(head_tag), wanted v0.0.2"
[[ "$(installs)" == "1" ]] || fail "B: installer ran $(installs) times, wanted 1"
grep -q "updated to v0.0.2" "$SMARTBAR_CACHE_DIR/update.log" \
  || fail "B: success not logged"
grep -q '"currentVersion": "0.0.2"' "$SMARTBAR_CACHE_DIR/update-state.json" \
  || fail "B: state file does not report the new version"
# The sha the installers actually ran for. Nothing else in the state file
# records it — appliedVersion only moves on a release — and channel=main
# reads it to tell "checked out" apart from "checked out AND built".
grep -q "\"appliedRef\": \"$(device rev-parse HEAD)\"" \
  "$SMARTBAR_CACHE_DIR/update-state.json" \
  || fail "B: appliedRef is not the sha that was checked out"

# --- C: nothing to do the second time -------------------------------------
set +e; run_update --check-update >/dev/null; CODE=$?; set -e
[[ "$CODE" == "0" ]] || fail "C: --check-update returned $CODE on a current device"
run_update --update || fail "C: no-op update exited non-zero"
[[ "$(installs)" == "1" ]] || fail "C: no-op update re-ran the installer"

# --- D: local changes freeze the device -----------------------------------
cut_release 0.0.3
echo "# local edit" >> "$WORK/device/README.md"
set +e; run_update --update 2>/dev/null; CODE=$?; set -e
[[ "$CODE" == "2" ]] || fail "D: dirty tree returned $CODE, wanted 2 (blocked)"
[[ "$(head_tag)" == "v0.0.2" ]] || fail "D: dirty tree still got updated"
grep -q "local edit" "$WORK/device/README.md" || fail "D: local edit was lost"

# --- E: --reset updates and parks the work --------------------------------
run_update --update --reset || fail "E: reset update exited non-zero"
[[ "$(head_tag)" == "v0.0.3" ]] || fail "E: reset did not reach v0.0.3"
[[ -n "$(device for-each-ref --format='%(refname)' refs/smartbar-rescue/)" ]] \
  || fail "E: --reset discarded local work without a rescue ref"
device stash list >/dev/null   # rescue ref must be a valid stash commit
RESCUE="$(device for-each-ref --format='%(refname)' refs/smartbar-rescue/ | head -1)"
device stash show "$RESCUE" >/dev/null || fail "E: rescue ref is not applicable"

# --- F: a bad release rolls back ------------------------------------------
cut_release 0.0.4
BEFORE="$(device rev-parse HEAD)"
set +e; E2E_FAIL_INSTALL=1 run_update --update 2>/dev/null; CODE=$?; set -e
[[ "$CODE" == "1" ]] || fail "F: failed install returned $CODE, wanted 1"
[[ "$(device rev-parse HEAD)" == "$BEFORE" ]] \
  || fail "F: HEAD not restored (at $(head_tag), wanted v0.0.3)"
[[ "$(head_tag)" == "v0.0.3" ]] || fail "F: rolled back to the wrong ref"
grep -q "rolling back" "$SMARTBAR_CACHE_DIR/update.log" || fail "F: rollback not logged"
grep -q '"v0.0.4"' "$SMARTBAR_CACHE_DIR/update-state.json" \
  || fail "F: failure not recorded against the ref"

# --- F2: the brake stops retrying a poisoned release ----------------------
for _ in 1 2; do
  set +e; E2E_FAIL_INSTALL=1 run_update --update >/dev/null 2>&1; set -e
done
set +e; E2E_FAIL_INSTALL=1 run_update --update 2>/dev/null; CODE=$?; set -e
[[ "$CODE" == "2" ]] || fail "F2: brake did not engage (returned $CODE, wanted 2)"
[[ "$(head_tag)" == "v0.0.3" ]] || fail "F2: device left off its last good release"

# --- G: re-installing must not silently reset the update channel ----------
# The updater re-runs the installers on every apply, so a channel that did
# not survive that would quietly move a dev box onto the release channel.
# SMARTBAR_UPDATE_APPLY=1 is the updater's own path and touches no launchctl.
if [[ "$(uname)" == "Darwin" ]]; then
  CH_HOME="$WORK/chan"; mkdir -p "$CH_HOME/Library/LaunchAgents"
  CH_PLIST="$CH_HOME/Library/LaunchAgents/com.ductran.ai-smartbar.update.plist"
  chan() { /usr/libexec/PlistBuddy -c \
    'Print :EnvironmentVariables:SMARTBAR_UPDATE_CHANNEL' "$CH_PLIST" 2>/dev/null; }
  env -u SMARTBAR_UPDATE_CHANNEL HOME="$CH_HOME" SMARTBAR_UPDATE_APPLY=1 \
    "$WORK/device/install/macos-update.sh" --channel main >/dev/null
  [[ "$(chan)" == "main" ]] || fail "G: --channel main not written (got '$(chan)')"
  env -u SMARTBAR_UPDATE_CHANNEL HOME="$CH_HOME" SMARTBAR_UPDATE_APPLY=1 \
    "$WORK/device/install/macos-update.sh" >/dev/null
  [[ "$(chan)" == "main" ]] || fail "G: re-install reset the channel to '$(chan)'"
  env -u SMARTBAR_UPDATE_CHANNEL HOME="$CH_HOME" SMARTBAR_UPDATE_APPLY=1 \
    "$WORK/device/install/macos-update.sh" --channel release >/dev/null
  [[ "$(chan)" == "release" ]] || fail "G: explicit channel change ignored"
  echo "  G: channel survives re-install (macOS)"
fi

echo "E2E update: A-G all PASS (check, apply, no-op, blocked, reset, rollback, brake, channel)"
