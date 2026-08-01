#!/usr/bin/env bash
# Cut a release: bump the canonical version, propagate it, run the tests, then
# commit + tag + push. The `vX.Y.Z` tag is exactly what every device's updater
# looks for, so none of the gates here are optional — an untested tag ships to
# every machine at once.
#
#   ./install/release.sh patch            # 0.3.0 -> 0.3.1
#   ./install/release.sh minor            # 0.3.0 -> 0.4.0
#   ./install/release.sh 1.0.0            # explicit
#   ./install/release.sh patch --no-push  # stop before pushing (dry run)
#   ./install/release.sh minor --full     # also run the slow e2e suites
#   ./install/release.sh patch --gh       # also create a GitHub release
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INIT="$REPO/smartbar/__init__.py"
SWIFT_VERSION="$REPO/macos-swift/Sources/AISmartbar/Version.swift"
cd "$REPO"

BUMP="${1:-}"
shift || true
PUSH=1; FULL=0; GH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-push) PUSH=0; shift ;;
    --full)    FULL=1; shift ;;
    --gh)      GH=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

CURRENT="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$INIT")"
[[ -n "$CURRENT" ]] || { echo "cannot read __version__ from $INIT" >&2; exit 1; }
IFS=. read -r MAJOR MINOR PATCH <<EOF
$CURRENT
EOF
case "$BUMP" in
  major) NEW="$((MAJOR + 1)).0.0" ;;
  minor) NEW="${MAJOR}.$((MINOR + 1)).0" ;;
  patch) NEW="${MAJOR}.${MINOR}.$((PATCH + 1))" ;;
  [0-9]*.[0-9]*.[0-9]*) NEW="$BUMP" ;;
  *) echo "usage: $0 <major|minor|patch|X.Y.Z> [--no-push] [--full] [--gh]" >&2
     exit 2 ;;
esac

# --- gates: never tag something a device cannot safely converge on ---------
[[ -z "$(git status --porcelain)" ]] \
  || { echo "working tree is dirty — commit or stash first" >&2; exit 1; }
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "$BRANCH" == "main" ]] \
  || { echo "releases are cut from main (currently on '$BRANCH')" >&2; exit 1; }
git fetch --tags --quiet origin
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "main and origin/main differ — push or pull before releasing" >&2
  exit 1
fi

# A local pass is necessary but not sufficient: cairo and the native platform
# surfaces differ across the three GitHub runners. Require the newest *main
# branch push* run for this exact HEAD, deliberately excluding a tag-triggered
# run at the same SHA. A queued run gets a short bounded wait; missing gh/auth,
# no matching run, API errors, timeout, cancellation, or failure all stop the
# release before the version files or refs are changed.
command -v gh >/dev/null 2>&1 \
  || { echo "GitHub CLI (gh) is required to verify CI before tagging" >&2; exit 1; }
gh auth status --hostname github.com >/dev/null 2>&1 \
  || { echo "gh is not authenticated to github.com — refusing to tag" >&2; exit 1; }

HEAD_SHA="$(git rev-parse HEAD)"
CI_FIELDS="databaseId,headSha,headBranch,event,status,conclusion,workflowName,url"
CI_JQ='if length == 0 then "" else .[0] | [.databaseId, .headSha, .headBranch, .event, .status, (.conclusion // "-"), .workflowName, .url] | map(tostring) | join("|") end'
if ! CI_ROW="$(gh run list --workflow tests.yml --branch main --commit "$HEAD_SHA" \
    --event push --limit 10 --json "$CI_FIELDS" --jq "$CI_JQ")"; then
  echo "could not query the GitHub tests workflow — refusing to tag" >&2
  exit 1
fi
[[ -n "$CI_ROW" ]] \
  || { echo "no GitHub tests workflow run found for main HEAD $HEAD_SHA" >&2; exit 1; }

read_ci_row() {
  IFS='|' read -r CI_RUN_ID CI_RUN_SHA CI_RUN_BRANCH CI_RUN_EVENT \
    CI_RUN_STATUS CI_RUN_CONCLUSION CI_RUN_WORKFLOW CI_RUN_URL <<<"$1"
}
validate_ci_identity() {
  [[ -n "$CI_RUN_ID" && "$CI_RUN_SHA" == "$HEAD_SHA" \
      && "$CI_RUN_BRANCH" == "main" && "$CI_RUN_EVENT" == "push" \
      && "$CI_RUN_WORKFLOW" == "tests" ]]
}
read_ci_row "$CI_ROW"
validate_ci_identity \
  || { echo "GitHub returned a non-matching workflow run — refusing to tag" >&2; exit 1; }

CI_WAIT_LIMIT=180
CI_POLL_INTERVAL=5
CI_WAIT_STARTED=$SECONDS
while [[ "$CI_RUN_STATUS" != "completed" ]]; do
  if (( SECONDS - CI_WAIT_STARTED >= CI_WAIT_LIMIT )); then
    echo "GitHub tests workflow did not complete within ${CI_WAIT_LIMIT}s — refusing to tag" >&2
    exit 1
  fi
  echo "Waiting for GitHub tests workflow run $CI_RUN_ID (${CI_RUN_STATUS})…"
  sleep "$CI_POLL_INTERVAL"
  CI_VIEW_JQ='[.databaseId, .headSha, .headBranch, .event, .status, (.conclusion // "-"), .workflowName, .url] | map(tostring) | join("|")'
  if ! CI_ROW="$(gh run view "$CI_RUN_ID" --json "$CI_FIELDS" --jq "$CI_VIEW_JQ")"; then
    echo "could not refresh GitHub tests workflow run $CI_RUN_ID — refusing to tag" >&2
    exit 1
  fi
  read_ci_row "$CI_ROW"
  validate_ci_identity \
    || { echo "GitHub workflow identity changed while waiting — refusing to tag" >&2; exit 1; }
done
[[ "$CI_RUN_CONCLUSION" == "success" ]] || {
  echo "GitHub tests workflow is ${CI_RUN_CONCLUSION} for $HEAD_SHA — refusing to tag" >&2
  [[ -z "$CI_RUN_URL" ]] || echo "  $CI_RUN_URL" >&2
  exit 1
}
echo "GitHub tests workflow passed for $HEAD_SHA ($CI_RUN_URL)"

if git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null; then
  echo "tag v$NEW already exists" >&2
  exit 1
fi

echo "Releasing $CURRENT -> $NEW"

# --- propagate the one canonical version ----------------------------------
sed -i.bak "s/^__version__ = \".*\"/__version__ = \"$NEW\"/" "$INIT"
rm -f "$INIT.bak"
cat > "$SWIFT_VERSION" <<EOF
// Generated by install/release.sh — do not edit by hand.
// Mirrors smartbar/__init__.py so the popover, the CLI, the app bundle's
// Info.plist and the git tag all name the same build.
enum AppVersion {
    static let current = "$NEW"
}
EOF

revert() {
  git checkout -- "$INIT" "$SWIFT_VERSION" 2>/dev/null || true
}

# --- tests ----------------------------------------------------------------
LOG="$(mktemp)"
run_suite() {
  echo "  $1"
  if ! "${@:2}" >"$LOG" 2>&1; then
    echo "FAILED: $1 — not releasing" >&2
    tail -30 "$LOG" >&2
    revert
    exit 1
  fi
}
echo "Running tests…"
run_suite "unit tests" python3 -m unittest discover -s tests
run_suite "e2e-update" "$REPO/tests/e2e-update.sh"
# Always: presence is the only feature that writes to a REMOTE on a timer,
# so a release must never ship it broken. Uses a throwaway bare repo.
run_suite "e2e-presence" "$REPO/tests/e2e-presence.sh"
# Always: this one generates the LaunchAgents and systemd units every device
# re-installs on update. A malformed unit is not a degraded feature, it is a
# device that stops running the app — and it would ship to all of them at once.
# Fully sandboxed (stubbed launchctl/systemctl/crontab, temporary HOME).
run_suite "e2e-config" "$REPO/tests/e2e-config.sh"
if [[ "$FULL" == "1" ]]; then
  run_suite "e2e-warmup" "$REPO/tests/e2e-warmup.sh"
  run_suite "e2e-autoadd" "$REPO/tests/e2e-autoadd.sh"
fi
rm -f "$LOG"

# --- commit, tag, push ----------------------------------------------------
git add "$INIT" "$SWIFT_VERSION"
git commit -q -m "release: v$NEW"
git tag -a "v$NEW" -m "v$NEW"
echo "Committed and tagged v$NEW."

if [[ "$PUSH" == "0" ]]; then
  echo "--no-push: nothing pushed. Undo with:"
  echo "  git tag -d v$NEW && git reset --hard HEAD~1"
  exit 0
fi
git push -q origin main
git push -q origin "v$NEW"
echo "Pushed main and v$NEW — devices on the release channel pick it up within 6h"
echo "(or immediately via the popover's upgrade button / 'ai-smartbar --update')."

if [[ "$GH" == "1" ]]; then
  if command -v gh >/dev/null; then
    gh release create "v$NEW" --title "v$NEW" --generate-notes \
      || echo "WARNING: gh release failed; the tag is pushed, so updates still work." >&2
  else
    echo "WARNING: gh not installed — tag pushed, no GitHub release created." >&2
  fi
fi
