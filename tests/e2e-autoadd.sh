#!/bin/bash
# Closed-loop E2E for auto-registration, driving the built macOS binary
# against the stateful mock cswap (no real accounts touched).
#   Run A: unregistered login -> exactly one `add`, then a re-list that
#          shows the active account.
#   Run B: MOCK_ADD_FAIL=1 -> add fails once; the cooldown holds further
#          attempts despite continued polls.
set -euo pipefail
cd "$(dirname "$0")/.."
BIN="macos-swift/.build/release/AISmartbar"
MOCK="$PWD/tests/mocks/mock-cswap-autoadd"
[ -x "$BIN" ] || { echo "build first: (cd macos-swift && swift build -c release)"; exit 1; }

# This suite launches the REAL app binary, so every way it can reach the
# outside world has to be fenced off — not just cswap. Presence is the one
# feature that does NOT go through SMARTBAR_CSWAP: it talks to git directly,
# and its repoRoot() falls back to the real checkout, so an unfenced run
# publishes a junk beacon to the real origin and overwrites the user's real
# device counts. (Observed: a test run replaced this Mac's beacon with
# "no active account" and blanked the live badges.) PRESENCE=off stops the
# remote write; CACHE_DIR keeps all other state off the user's disk.
#
# The guard below watches the ISOLATED cache, not the user's real one. The
# obvious check — fingerprint ~/.cache/ai-smartbar/presence-state.json before
# and after — looks stronger but fails at random: on any machine with the app
# installed, the real app rewrites that file on its own 300s beat, and a
# beat landing inside the ~20s test window is indistinguishable from a leak.
# (That false positive is not hypothetical; it fired here.) With presence
# disabled the app must write no presence state at all, so its absence in the
# directory we handed the child is the race-free form of the same assertion.

run_case() {
  local name="$1" add_fail="$2" want_adds="$3" state pid
  state=$(mktemp -d)
  : > "$state/calls.log"
  SMARTBAR_CSWAP="$MOCK" MOCK_STATE_DIR="$state" MOCK_ADD_FAIL="$add_fail" \
    SMARTBAR_INTERVAL=2 \
    SMARTBAR_PRESENCE=off SMARTBAR_CACHE_DIR="$state/cache" \
    SMARTBAR_OPENAI=off \
    "$BIN" >/dev/null 2>&1 &
  pid=$!
  sleep 9   # ≥4 poll cycles at 2s
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true

  local adds lists_after_add
  adds=$(grep -c " add$" "$state/calls.log" || true)
  if [ "$adds" -ne "$want_adds" ]; then
    echo "FAIL($name): expected $want_adds add call(s), saw $adds"; cat "$state/calls.log"; exit 1
  fi
  if [ "$add_fail" = "0" ]; then
    lists_after_add=$(awk '/ add$/{seen=1;next} seen && / list --json$/{n++} END{print n+0}' "$state/calls.log")
    if [ "$lists_after_add" -lt 1 ]; then
      echo "FAIL($name): no re-list after add"; cat "$state/calls.log"; exit 1
    fi
  fi
  if [ -e "$state/cache/presence-state.json" ]; then
    echo "FAIL($name): the app under test ran a presence beat — it can reach"
    echo "  the real origin and the user's real device counts. Restore the"
    echo "  SMARTBAR_PRESENCE=off / SMARTBAR_CACHE_DIR fence above."
    exit 1
  fi
  echo "PASS($name): adds=$adds, no presence leak"
  rm -rf "$state"
}

run_case "register-on-login" 0 1
run_case "cooldown-on-failure" 1 1
echo "E2E auto-registration: ALL PASS"
