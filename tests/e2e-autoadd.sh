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

run_case() {
  local name="$1" add_fail="$2" want_adds="$3" state pid
  state=$(mktemp -d)
  : > "$state/calls.log"
  SMARTBAR_CSWAP="$MOCK" MOCK_STATE_DIR="$state" MOCK_ADD_FAIL="$add_fail" \
    SMARTBAR_INTERVAL=2 "$BIN" >/dev/null 2>&1 &
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
  echo "PASS($name): adds=$adds"
  rm -rf "$state"
}

run_case "register-on-login" 0 1
run_case "cooldown-on-failure" 1 1
echo "E2E auto-registration: ALL PASS"
