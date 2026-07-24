#!/usr/bin/env bash
# Device-presence E2E against a real git server and two real devices.
#
# The unit tests pin the policy; this pins the plumbing, which is the half
# that cannot be reasoned about — whether a push really replaces a ref,
# whether ls-remote really shows another machine, whether leaving really
# stops the count. A second physical device is the one thing this project
# cannot test, and at the ref level a second clone with its own device id
# IS a second device, so that is exactly what runs here.
#
#   A  a lone device publishes and counts itself                     (1)
#   B  a second device on the same account makes it                  (2)
#   C  a beat REPLACES its own ref — one ref per device, ever
#   D  the two devices disagreeing on accounts count separately
#   E  a dead device's ref ages out instead of inflating the count
#   F  leaving stops the count immediately, not after the TTL
#   G  a device that cannot push still counts everyone (read-only credential)
#   H  an unreachable remote holds the last good answer, never invents one
#   I  SMARTBAR_PRESENCE=off touches nothing at all
#   J  beats transfer ZERO objects and create no branch or tag
#
# Nothing here touches the real GitHub remote, the real HOME, or the real
# ~/.config: every device gets its own HOME, cache and config directory.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL="$(cd "$HERE/.." && pwd)"
WORK="$(mktemp -d)"
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

MINE="syu3cs@virginia.edu"
OTHER="ios8build@gmail.com"

GIT=(git -c user.name=e2e -c user.email=e2e@localhost -c commit.gpgsign=false \
         -c init.defaultBranch=main -c advice.detachedHead=false)

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- build a real bare origin from the CURRENT working tree ----------------
mkdir -p "$WORK/src"
tar -C "$REAL" --exclude .git --exclude .build --exclude __pycache__ \
    --exclude '*.pyc' -cf - . | tar -C "$WORK/src" -xf -
"${GIT[@]}" -C "$WORK/src" init -q
"${GIT[@]}" -C "$WORK/src" add -A
"${GIT[@]}" -C "$WORK/src" commit -q -m "e2e base"
"${GIT[@]}" init -q --bare "$WORK/origin.git"
"${GIT[@]}" -C "$WORK/src" remote add origin "$WORK/origin.git"
"${GIT[@]}" -C "$WORK/src" push -q origin main
"${GIT[@]}" -C "$WORK/origin.git" symbolic-ref HEAD refs/heads/main

for dev in mac linux; do
  "${GIT[@]}" clone -q "$WORK/origin.git" "$WORK/$dev"
  mkdir -p "$WORK/$dev-home" "$WORK/$dev-cache" "$WORK/$dev-config"
done

# $1 device, $2 active email ("-" for none), rest: flags for the launcher.
beat() {
  local dev="$1" active="$2"; shift 2
  local payload
  [[ "$active" == "-" ]] && active=""
  payload="{\"active\":\"$active\",\"accounts\":[\"$MINE\",\"$OTHER\"]}"
  ( cd "$WORK/$dev" \
    && HOME="$WORK/$dev-home" \
       SMARTBAR_CACHE_DIR="$WORK/$dev-cache" \
       SMARTBAR_CONFIG_DIR="$WORK/$dev-config" \
       SMARTBAR_PRESENCE_LABEL="$dev" \
       python3 ./bin/ai-smartbar "$@" <<<"$payload" )
}

count() {  # $1 device, $2 email -> devices currently on that account
  python3 - "$WORK/$1-cache/presence-state.json" "$2" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as handle:
        print(json.load(handle).get("counts", {}).get(sys.argv[2], 0))
except (OSError, ValueError):
    print("NOSTATE")
PY
}

# The prefix form, NOT 'refs/smartbar/*': for-each-ref globs are path-aware
# and a single * will not cross a slash, so the pattern that ls-remote uses
# (fnmatch, crosses slashes — the reason presence.GLOB works at all) would
# silently list nothing here.
refs() { "${GIT[@]}" -C "$WORK/origin.git" for-each-ref --format='%(refname)' \
           'refs/smartbar/'; }
nrefs() { refs | grep -c . || true; }
key() {  # account hash, computed by the code under test
  ( cd "$WORK/mac" && python3 -c "
from smartbar.core import presence
print(presence.account_key('$1'))" )
}
objects() { "${GIT[@]}" -C "$WORK/origin.git" count-objects -v \
              | awk '/^count:|^in-pack:/ {total += $2} END {print total}'; }

BEFORE_OBJECTS="$(objects)"

# --- A: a lone device publishes and counts itself --------------------------
beat mac "$MINE" --presence-beat >/dev/null || fail "A: first beat failed"
[[ "$(nrefs)" == "1" ]] || fail "A: expected 1 ref, got $(nrefs): $(refs)"
[[ "$(count mac "$MINE")" == "1" ]] \
  || fail "A: alone should be 1, got $(count mac "$MINE")"
[[ "$(count mac "$OTHER")" == "0" ]] \
  || fail "A: an account nobody is on must not be counted"
refs | grep -q "/mac/" || fail "A: the device label is missing from $(refs)"

# --- B: a second device on the same account makes it 2 ---------------------
beat linux "$MINE" --presence-beat >/dev/null || fail "B: linux beat failed"
[[ "$(nrefs)" == "2" ]] || fail "B: expected 2 refs, got $(nrefs)"
[[ "$(count linux "$MINE")" == "2" ]] \
  || fail "B: linux should see 2, got $(count linux "$MINE")"
beat mac "$MINE" --presence-beat >/dev/null || fail "B: mac re-beat failed"
[[ "$(count mac "$MINE")" == "2" ]] \
  || fail "B: mac should see 2, got $(count mac "$MINE")"

# --- C: a beat replaces its own ref, never accumulates ---------------------
FIRST="$(refs | grep '/mac/' | head -1)"
sleep 1                       # the epoch lives in the ref name
beat mac "$MINE" --presence-beat >/dev/null || fail "C: beat failed"
[[ "$(refs | grep -c '/mac/')" == "1" ]] \
  || fail "C: mac left more than one ref behind: $(refs)"
[[ "$(refs | grep '/mac/' | head -1)" != "$FIRST" ]] \
  || fail "C: the ref did not move, so the heartbeat is not beating"
[[ "$(nrefs)" == "2" ]] || fail "C: total refs should still be 2"

# --- D: devices on different accounts are counted separately --------------
beat linux "$OTHER" --presence-beat >/dev/null || fail "D: linux beat failed"
beat mac "$MINE" --presence-beat >/dev/null || fail "D: mac beat failed"
[[ "$(count mac "$MINE")" == "1" && "$(count mac "$OTHER")" == "1" ]] \
  || fail "D: expected 1 and 1, got $(count mac "$MINE") and $(count mac "$OTHER")"

# --- E: a dead device's ref ages out --------------------------------------
# Forge a ref stamped long ago — exactly what a machine that died leaves.
DEAD_EPOCH=$(( $(date +%s) - 86400 ))
SHA="$("${GIT[@]}" -C "$WORK/origin.git" rev-parse HEAD)"
"${GIT[@]}" -C "$WORK/mac" push -q origin \
  "${SHA}:refs/smartbar/p1/deadbeef0001/ghostbox/${DEAD_EPOCH}/$(key "$MINE")"
beat mac "$MINE" --presence-beat >/dev/null || fail "E: beat failed"
[[ "$(count mac "$MINE")" == "1" ]] \
  || fail "E: a day-old ref must not count, got $(count mac "$MINE")"

# --- F: leaving stops the count immediately -------------------------------
beat linux "$MINE" --presence-beat >/dev/null || fail "F: linux beat failed"
beat mac "$MINE" --presence-beat >/dev/null || fail "F: mac beat failed"
[[ "$(count mac "$MINE")" == "2" ]] || fail "F: expected 2 before the quit"
beat linux "$MINE" --presence-leave >/dev/null || fail "F: leave failed"
[[ "$(refs | grep -c '/linux/')" == "0" ]] \
  || fail "F: leaving left its ref behind: $(refs)"
beat mac "$MINE" --presence-beat >/dev/null || fail "F: mac re-beat failed"
[[ "$(count mac "$MINE")" == "1" ]] \
  || fail "F: back to 1 after a quit, got $(count mac "$MINE")"

# --- G: a read-only credential still counts everyone ----------------------
beat linux "$MINE" --presence-beat >/dev/null || fail "G: linux beat failed"
cat > "$WORK/origin.git/hooks/pre-receive" <<'HOOK'
#!/bin/sh
echo "remote: read-only credential" >&2
exit 1
HOOK
chmod +x "$WORK/origin.git/hooks/pre-receive"
BEFORE="$(nrefs)"
# The epoch in a ref name has one-second resolution, so a beat in the same
# second as the previous one pushes an IDENTICAL refspec — git answers
# "everything up-to-date" and never reaches the hook. Real beats are five
# minutes apart; here the wait is what makes the rejection deterministic.
sleep 1
set +e; beat mac "$MINE" --presence-beat >/dev/null; RC=$?; set -e
[[ "$RC" == "1" ]] || fail "G: a device that cannot publish must report it (rc=$RC)"
[[ "$(nrefs)" == "$BEFORE" ]] || fail "G: the rejected push changed the remote"
[[ "$(count mac "$MINE")" == "2" ]] \
  || fail "G: a read-only device must still count itself and linux, got $(count mac "$MINE")"
rm -f "$WORK/origin.git/hooks/pre-receive"

# --- H: an unreachable remote holds the last answer, never invents one -----
beat mac "$MINE" --presence-beat >/dev/null || fail "H: setup beat failed"
[[ "$(count mac "$MINE")" == "2" ]] || fail "H: expected 2 before going dark"
"${GIT[@]}" -C "$WORK/mac" remote set-url origin "$WORK/nowhere.git"
set +e; beat mac "$MINE" --presence-beat >/dev/null; RC=$?; set -e
[[ "$RC" == "1" ]] || fail "H: an unreachable remote must be reported (rc=$RC)"
[[ "$(count mac "$MINE")" == "2" ]] \
  || fail "H: the last good count must stand briefly, got $(count mac "$MINE")"
"${GIT[@]}" -C "$WORK/mac" remote set-url origin "$WORK/origin.git"

# --- I: the kill switch really kills --------------------------------------
BEFORE="$(nrefs)"
( export SMARTBAR_PRESENCE=off; beat mac "$MINE" --presence-beat >/dev/null ) \
  || fail "I: an opted-out device must exit cleanly"
[[ "$(nrefs)" == "$BEFORE" ]] \
  || fail "I: SMARTBAR_PRESENCE=off still wrote to the remote"

# --- J: beats are free, and invisible to anything that reads branches -----
[[ "$(objects)" == "$BEFORE_OBJECTS" ]] \
  || fail "J: presence pushed objects ($BEFORE_OBJECTS -> $(objects))"
[[ -z "$("${GIT[@]}" -C "$WORK/origin.git" for-each-ref \
           --format='%(refname)' refs/heads/ | grep -v '^refs/heads/main$')" ]] \
  || fail "J: presence created a branch"
[[ -z "$("${GIT[@]}" -C "$WORK/origin.git" for-each-ref refs/tags/)" ]] \
  || fail "J: presence created a tag"

echo "e2e-presence: all scenarios passed"
