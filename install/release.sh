#!/usr/bin/env bash
# Cut a release: bump the canonical version, propagate it, run the tests, then
# commit + tag + push. The `vX.Y.Z` tag is exactly what every device's updater
# looks for, so none of the gates here are optional — an untested tag ships to
# every machine at once.
#
#   ./install/release.sh patch            # 0.3.0 -> 0.3.1
#   ./install/release.sh minor            # 0.3.0 -> 0.4.0
#   ./install/release.sh 1.0.0            # explicit
#   ./install/release.sh patch --no-push  # commit an untagged local candidate
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
SWIFT_CURRENT="$(sed -n 's/^[[:space:]]*static let current = "\(.*\)"/\1/p' "$SWIFT_VERSION")"
[[ "$SWIFT_CURRENT" == "$CURRENT" ]] || {
  echo "Python ($CURRENT) and Swift ($SWIFT_CURRENT) versions disagree" >&2
  exit 1
}
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

RESUMING=0
if [[ "$CURRENT" == "$NEW" ]]; then
  RESUMING=1
fi

# --- gates: never tag something a device cannot safely converge on ---------
[[ -z "$(git status --porcelain)" ]] \
  || { echo "working tree is dirty — commit or stash first" >&2; exit 1; }
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "$BRANCH" == "main" ]] \
  || { echo "releases are cut from main (currently on '$BRANCH')" >&2; exit 1; }
git fetch --tags --quiet origin
HEAD_SHA="$(git rev-parse HEAD)"
ORIGIN_SHA="$(git rev-parse origin/main)"
LOCAL_CANDIDATE=0
if [[ "$HEAD_SHA" != "$ORIGIN_SHA" ]]; then
  # `--no-push`, or an interruption after the release commit and before its
  # push, leaves exactly one local version-only candidate. Permit that precise
  # shape to resume; every other divergence still requires human resolution.
  CANDIDATE_FILES="$(git diff-tree --no-commit-id --name-only -r HEAD | LC_ALL=C sort)"
  EXPECTED_CANDIDATE_FILES=$'macos-swift/Sources/AISmartbar/Version.swift\nsmartbar/__init__.py'
  if [[ "$RESUMING" == "1" \
        && "$(git rev-parse HEAD^)" == "$ORIGIN_SHA" \
        && "$(git log -1 --format=%s)" == "release: v$NEW" \
        && "$CANDIDATE_FILES" == "$EXPECTED_CANDIDATE_FILES" ]]; then
    LOCAL_CANDIDATE=1
  else
    echo "main and origin/main differ outside a resumable release candidate" >&2
    exit 1
  fi
fi

if git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null; then
  echo "tag v$NEW already exists; inspect its target before retrying any push" >&2
  exit 1
fi

github_repo_from_url() {
  local url="$1"
  local answer
  case "$url" in
    https://github.com/*) answer="${url#https://github.com/}" ;;
    git@github.com:*) answer="${url#git@github.com:}" ;;
    ssh://git@github.com/*) answer="${url#ssh://git@github.com/}" ;;
    *) return 1 ;;
  esac
  answer="${answer%.git}"
  [[ "$answer" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || return 1
  printf '%s\n' "$answer"
}

ORIGIN_FETCH_URL="$(git remote get-url origin)"
ORIGIN_PUSH_URL="$(git remote get-url --push origin)"
GH_REPO="$(github_repo_from_url "$ORIGIN_FETCH_URL")" \
  || { echo "origin fetch URL is not a supported github.com repository" >&2; exit 1; }
PUSH_GH_REPO="$(github_repo_from_url "$ORIGIN_PUSH_URL")" \
  || { echo "origin push URL is not a supported github.com repository" >&2; exit 1; }
[[ "$PUSH_GH_REPO" == "$GH_REPO" ]] || {
  echo "origin fetch and push URLs name different GitHub repositories" >&2
  exit 1
}

# A local pass is necessary but not sufficient: cairo and the native platform
# surfaces differ across the three GitHub runners. Require the newest *main
# branch push* run for this exact HEAD, deliberately excluding a tag-triggered
# run at the same SHA. A queued run gets a bounded wait; missing gh/auth,
# no matching run, API errors, timeout, cancellation, or failure all stop the
# release before the version files or refs are changed.
command -v gh >/dev/null 2>&1 \
  || { echo "GitHub CLI (gh) is required to verify CI before tagging" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 \
  || { echo "python3 is required to bound GitHub CLI calls" >&2; exit 1; }
GH_CALL_TIMEOUT=45
run_gh() {
  python3 -c 'import subprocess, sys
try:
    done = subprocess.run(sys.argv[2:], timeout=float(sys.argv[1]), check=False)
except subprocess.TimeoutExpired:
    print("GitHub CLI call timed out", file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(done.returncode)' "$GH_CALL_TIMEOUT" gh "$@"
}
run_gh auth status --hostname github.com >/dev/null 2>&1 \
  || { echo "gh is not authenticated to github.com — refusing to tag" >&2; exit 1; }

CI_FIELDS="databaseId,headSha,headBranch,event,status,conclusion,workflowName,url"
CI_JQ='if length == 0 then "" else .[0] | [.databaseId, .headSha, .headBranch, .event, .status, (.conclusion // "-"), .workflowName, .url] | map(tostring) | join("|") end'
read_ci_row() {
  IFS='|' read -r CI_RUN_ID CI_RUN_SHA CI_RUN_BRANCH CI_RUN_EVENT \
    CI_RUN_STATUS CI_RUN_CONCLUSION CI_RUN_WORKFLOW CI_RUN_URL <<<"$1"
}
validate_ci_identity() {
  local expected_sha="$1"
  # push OR workflow_dispatch: the Windows and Swift legs only run on a
  # push when the release fingerprint / macos-swift/ changed, so a resumed
  # release (a fix commit on top of the candidate) needs the manual full
  # run that require_full_legs asks for — which is a dispatch event. With
  # `push` alone that advice could never be satisfied.
  [[ -n "$CI_RUN_ID" && "$CI_RUN_SHA" == "$expected_sha" \
      && "$CI_RUN_BRANCH" == "main" \
      && ( "$CI_RUN_EVENT" == "push" || "$CI_RUN_EVENT" == "workflow_dispatch" ) \
      && "$CI_RUN_WORKFLOW" == "tests" ]]
}
CI_WAIT_LIMIT=900
CI_DISCOVERY_LIMIT=120
CI_POLL_INTERVAL=5
CI_VIEW_JQ='[.databaseId, .headSha, .headBranch, .event, .status, (.conclusion // "-"), .workflowName, .url] | map(tostring) | join("|")'

require_ci_success() {
  local expected_sha="$1"
  local discovery_limit="${2:-0}"
  local discovery_started=$SECONDS
  local wait_started
  local row

  while true; do
    # No --event filter: the newest run for the SHA wins, so a full manual
    # dispatch made after a leg-skipping push run is the one gated (see
    # validate_ci_identity). --branch main keeps pull_request runs out.
    if ! row="$(run_gh run list --repo "$GH_REPO" \
        --workflow tests.yml --branch main \
        --commit "$expected_sha" --limit 10 \
        --json "$CI_FIELDS" --jq "$CI_JQ")"; then
      echo "could not query the GitHub tests workflow — refusing to tag" >&2
      return 1
    fi
    if [[ -n "$row" ]]; then
      break
    fi
    if (( SECONDS - discovery_started >= discovery_limit )); then
      echo "no GitHub tests workflow run found for main HEAD $expected_sha" >&2
      return 1
    fi
    echo "Waiting for GitHub to create the tests workflow for ${expected_sha}…"
    sleep "$CI_POLL_INTERVAL"
  done

  read_ci_row "$row"
  validate_ci_identity "$expected_sha" || {
    echo "GitHub returned a non-matching workflow run — refusing to tag" >&2
    return 1
  }

  wait_started=$SECONDS
  while [[ "$CI_RUN_STATUS" != "completed" ]]; do
    if (( SECONDS - wait_started >= CI_WAIT_LIMIT )); then
      echo "GitHub tests workflow did not complete within ${CI_WAIT_LIMIT}s — refusing to tag" >&2
      return 1
    fi
    echo "Waiting for GitHub tests workflow run $CI_RUN_ID (${CI_RUN_STATUS})…"
    sleep "$CI_POLL_INTERVAL"
    if ! row="$(run_gh run view "$CI_RUN_ID" --repo "$GH_REPO" \
        --json "$CI_FIELDS" \
        --jq "$CI_VIEW_JQ")"; then
      echo "could not refresh GitHub tests workflow run $CI_RUN_ID — refusing to tag" >&2
      return 1
    fi
    read_ci_row "$row"
    validate_ci_identity "$expected_sha" || {
      echo "GitHub workflow identity changed while waiting — refusing to tag" >&2
      return 1
    }
  done
  if [[ "$CI_RUN_CONCLUSION" != "success" ]]; then
    echo "GitHub tests workflow is ${CI_RUN_CONCLUSION} for $expected_sha — refusing to tag" >&2
    [[ -z "$CI_RUN_URL" ]] || echo "  $CI_RUN_URL" >&2
    return 1
  fi
  echo "GitHub tests workflow passed for $expected_sha ($CI_RUN_URL)"
}

# A resumed release gates the FIX commit's run, and the `changes` job only
# runs the Windows/Swift legs for the paths the fix touched — so the leg
# that just failed may simply have been skipped. Refuse to tag unless both
# expensive legs actually ran and passed on the gated SHA.
require_full_legs() {
  local jobs
  if ! jobs="$(run_gh run view "$CI_RUN_ID" --repo "$GH_REPO" --json jobs \
        --jq '[.jobs[] | select(.name == "windows" or .name == "swift")
               | .conclusion] | join(",")')"; then
    echo "could not read the gated run's jobs — refusing to tag" >&2
    return 1
  fi
  if [[ "$jobs" != "success,success" && "$jobs" != "success,success," ]]; then
    echo "the gated run did not exercise both release legs (windows,swift =" \
         "'${jobs:-none}') — dispatch a full run for $HEAD_SHA (Actions →" \
         "tests → Run workflow) and resume after it passes" >&2
    return 1
  fi
}

if [[ "$LOCAL_CANDIDATE" == "1" ]]; then
  echo "Resuming local unpushed release candidate v$NEW"
elif [[ "$RESUMING" == "1" ]]; then
  require_ci_success "$HEAD_SHA" "$CI_DISCOVERY_LIMIT"
  require_full_legs
  echo "Resuming untagged release v$NEW"
else
  require_ci_success "$HEAD_SHA" 0
  echo "Releasing $CURRENT -> $NEW"
fi

# --- propagate the one canonical version ----------------------------------
if [[ "$RESUMING" == "0" ]]; then
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
fi

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

# --- commit, gate the exact release SHA, then tag + push -------------------
if [[ "$RESUMING" == "0" ]]; then
  git add "$INIT" "$SWIFT_VERSION"
  git commit -q -m "release: v$NEW"
  echo "Committed v$NEW release candidate."
fi
RELEASE_SHA="$(git rev-parse HEAD)"

if [[ "$PUSH" == "0" ]]; then
  echo "--no-push: candidate $RELEASE_SHA is untagged and nothing was pushed."
  echo "Run '$0 $NEW --full' to resume, or revert the candidate to cancel."
  exit 0
fi
git push -q origin "$RELEASE_SHA:refs/heads/main"
if ! require_ci_success "$RELEASE_SHA" "$CI_DISCOVERY_LIMIT"; then
  echo "v$NEW was not tagged. Fix CI, push the fix, then resume with:" >&2
  echo "  $0 $NEW --full" >&2
  exit 1
fi
git tag -a "v$NEW" -m "v$NEW" "$RELEASE_SHA"
[[ "$(git rev-parse "v$NEW^{}")" == "$RELEASE_SHA" ]] \
  || { echo "local tag target does not match the CI-gated SHA" >&2; exit 1; }
TAG_OBJECT_SHA="$(git rev-parse "refs/tags/v$NEW")"
if ! git push -q origin "$TAG_OBJECT_SHA:refs/tags/v$NEW"; then
  echo "The local tag points to gated SHA $RELEASE_SHA but was not pushed." >&2
  echo "Retry only this exact ref after resolving the network error:" >&2
  echo "  git push origin $TAG_OBJECT_SHA:refs/tags/v$NEW" >&2
  exit 1
fi
echo "Pushed main and v$NEW — devices on the release channel pick it up within 6h"
echo "(or immediately via the popover's upgrade button / 'ai-smartbar --update')."

if [[ "$GH" == "1" ]]; then
  if command -v gh >/dev/null; then
    run_gh release create "v$NEW" --repo "$GH_REPO" \
      --verify-tag --title "v$NEW" --generate-notes \
      || echo "WARNING: gh release failed; the tag is pushed, so updates still work." >&2
  else
    echo "WARNING: gh not installed — tag pushed, no GitHub release created." >&2
  fi
fi
