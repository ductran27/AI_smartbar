"""Release-tag invariants that prevent an untested updater payload."""
from pathlib import Path
import re
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "install" / "release.sh").read_text(
    encoding="utf-8"
)


class TestReleaseGate(unittest.TestCase):
    def test_tag_is_bound_to_the_exact_sha_after_its_ci_gate(self):
        gate = SOURCE.index('if ! require_ci_success "$RELEASE_SHA"')
        tag = SOURCE.index('git tag -a "v$NEW" -m "v$NEW" "$RELEASE_SHA"')
        push = SOURCE.index(
            'git push -q origin "$TAG_OBJECT_SHA:refs/tags/v$NEW"')
        self.assertLess(gate, tag)
        self.assertLess(tag, push)
        self.assertIn(
            '[[ "$(git rev-parse "v$NEW^{}")" == "$RELEASE_SHA" ]]',
            SOURCE,
        )

    def test_no_push_creates_no_local_tag_that_can_escape_later(self):
        start = SOURCE.index('if [[ "$PUSH" == "0" ]]')
        end = SOURCE.index('git push -q origin "$RELEASE_SHA:refs/heads/main"')
        block = SOURCE[start:end]
        self.assertNotIn("git tag", block)
        self.assertIn("is untagged and nothing was pushed", block)

    def test_ci_queries_are_bounded_and_pinned_to_origin_repository(self):
        self.assertIn("GH_CALL_TIMEOUT=45", SOURCE)
        self.assertIn('run_gh run list --repo "$GH_REPO"', SOURCE)
        self.assertIn('run_gh run view "$CI_RUN_ID" --repo "$GH_REPO"', SOURCE)
        self.assertIn('[[ "$PUSH_GH_REPO" == "$GH_REPO" ]]', SOURCE)
        self.assertNotIn("$(gh run list", SOURCE)
        self.assertNotIn("$(gh run view", SOURCE)

    def test_main_push_also_uses_the_captured_release_sha(self):
        self.assertIn(
            'git push -q origin "$RELEASE_SHA:refs/heads/main"', SOURCE)
        self.assertNotIn("git push -q origin main", SOURCE)

    def test_github_release_may_not_create_or_retarget_the_tag(self):
        self.assertIn(
            'run_gh release create "v$NEW" --repo "$GH_REPO"', SOURCE)
        self.assertIn('--verify-tag --title "v$NEW"', SOURCE)

    def test_bash_variables_are_braced_before_unicode_punctuation(self):
        # macOS ships Bash 3.2, whose locale-dependent identifier parsing can
        # absorb the first byte of an adjacent ellipsis under `set -u`.
        self.assertIsNone(re.search(
            r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", SOURCE))


if __name__ == "__main__":
    unittest.main()
