#!/usr/bin/env bash
# Closed-loop warmup E2E against stateful mocks (no real accounts touched).
# Run A: both accounts idle -> exactly 2 warmups, verified.
# Run B: both running      -> 2 skips, zero claude calls.
# Run C: claude failing    -> clean errors, exit 1, no state corruption.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp "$HERE/mocks/mock-cswap-warmup" "$WORK/"
# Named exactly `claude`: the mock cswap resolves it from PATH the way the
# real one does (the runner prepends this dir via SMARTBAR_CLAUDE's dirname).
cp "$HERE/mocks/mock-claude" "$WORK/claude"
export SMARTBAR_CSWAP="$WORK/mock-cswap-warmup" SMARTBAR_CLAUDE="$WORK/claude"
export SMARTBAR_WARMUP_NOTIFY=off SMARTBAR_CACHE_DIR="$WORK/cache"
BIN="$HERE/../bin/ai-smartbar"

"$BIN" --warmup-once
grep -q "warmed #1" "$WORK/cache/warmup.log" && grep -q "warmed #2" "$WORK/cache/warmup.log" \
  || { echo "FAIL: run A did not warm both accounts"; exit 1; }

CALLS=$(wc -l < "$WORK/warmup-claude-calls.log")
"$BIN" --warmup-once
[[ "$(wc -l < "$WORK/warmup-claude-calls.log")" == "$CALLS" ]] \
  || { echo "FAIL: run B pinged despite running windows"; exit 1; }

rm -f "$WORK"/warmed-*
python3 - "$WORK/cache/warmup-state.json" <<'PY'
import json, sys
p = sys.argv[1]; s = json.load(open(p)); s["last"] = {}; json.dump(s, open(p, "w"))
PY
MOCK_CLAUDE_FAIL=1 "$BIN" --warmup-once && { echo "FAIL: run C should exit 1"; exit 1; }
grep -q "failed: claude exited" "$WORK/cache/warmup.log" \
  || { echo "FAIL: run C failure not logged"; exit 1; }
echo "E2E warmup: all three scenarios PASS"
