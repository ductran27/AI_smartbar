"""Release-tag invariants: the tag names exactly the release commit."""
from pathlib import Path
import re
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "install" / "release.sh").read_text(
    encoding="utf-8"
)


def without_comments(text):
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#"))


CODE = without_comments(SOURCE)


class TestReleaseGate(unittest.TestCase):
    def test_tag_is_bound_to_the_exact_release_sha(self):
        captured = SOURCE.index('RELEASE_SHA="$(git rev-parse HEAD)"')
        tag = SOURCE.index('git tag -a "v$NEW" -m "v$NEW" "$RELEASE_SHA"')
        push = SOURCE.index(
            'git push -q origin "$TAG_OBJECT_SHA:refs/tags/v$NEW"')
        self.assertLess(captured, tag)
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

    def test_pushes_go_to_the_repository_origin_is_fetched_from(self):
        self.assertIn('[[ "$PUSH_GH_REPO" == "$GH_REPO" ]]', SOURCE)

    def test_main_push_also_uses_the_captured_release_sha(self):
        self.assertIn(
            'git push -q origin "$RELEASE_SHA:refs/heads/main"', SOURCE)
        self.assertNotIn("git push -q origin main", SOURCE)

    def test_github_release_may_not_create_or_retarget_the_tag(self):
        self.assertIn('gh release create "v$NEW" --repo "$GH_REPO"', SOURCE)
        self.assertIn('--verify-tag --title "v$NEW"', SOURCE)

    def test_bash_variables_are_braced_before_unicode_punctuation(self):
        # macOS ships Bash 3.2, whose locale-dependent identifier parsing can
        # absorb the first byte of an adjacent ellipsis under `set -u`.
        self.assertIsNone(re.search(
            r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", SOURCE))

    def test_a_release_runs_no_tests_and_waits_on_no_ci(self):
        # Decided 2026-11-05 to save Actions minutes: the release commit only
        # changes the version files, and the code was tested before it merged.
        for needle in ("unittest", "tests/e2e-", "gh run "):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, CODE)


class TestWorkflowsRunNoTests(unittest.TestCase):
    """No GitHub Actions workflow runs the suite (same decision as above)."""

    WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

    def test_no_workflow_runs_the_suite(self):
        for path in sorted(self.WORKFLOWS.glob("*.yml")):
            text = without_comments(path.read_text(encoding="utf-8"))
            with self.subTest(workflow=path.name):
                self.assertNotIn("unittest", text)
                self.assertNotIn("tests/e2e-", text)

    def test_lint_runs_only_on_pull_requests_or_by_hand(self):
        text = without_comments(
            (self.WORKFLOWS / "lint.yml").read_text(encoding="utf-8"))
        # The `on:` block runs until the next top-level key.
        on = re.search(r"^on:\n((?:[ \t].*\n|\n)*)", text, flags=re.M).group(1)
        triggers = re.findall(r"^  ([a-z_]+):", on, flags=re.M)
        self.assertEqual(triggers, ["pull_request", "workflow_dispatch"])


if __name__ == "__main__":
    unittest.main()
