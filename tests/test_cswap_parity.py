"""The primer/list snippets exist twice, in two languages. Pin them together.

smartbar/core/cswap.py's PRIMER_CODE and COMBINED_CODE each carry a "Keep in
sync with the copy in macos-swift CswapClient" comment, and nothing enforced
it — `grep CswapClient tests/` came back empty. These are the exact strings
handed to a Python interpreter to drive claude_swap's internals, so a drift
between them means the Mac app and every other front-end freshen the usage
store differently, silently, and only one of them is right.

tests/test_presence.py:521 already proves this source-scraping pattern earns
its keep: its own comment records that one of the four constants it pins HAD
drifted. Same approach here, and for the same reason — it runs in the
ordinary unit suite with no Swift toolchain, so a contributor who never opens
Xcode still cannot break the Mac.

Both sides are read as SOURCE TEXT rather than runtime values. Swift and
Python spell the one escape these snippets contain (\\n) identically in
source, so comparing the files avoids having to reimplement either
language's unescaping rules to make the assertion true.
"""
from __future__ import annotations

import os
import re
import textwrap
import unittest

import smartbar

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
PY_SOURCE = os.path.join(REPO, "smartbar", "core", "cswap.py")
SWIFT_SOURCE = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                            "CswapClient.swift")


def python_literal(name):
    """The body of `NAME = \"\"\"\\ ... \"\"\"` in cswap.py, as written."""
    with open(PY_SOURCE, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r'^%s = """\\\n(.*?)^"""$' % name, text,
                      re.DOTALL | re.MULTILINE)
    assert match, "could not find %s in cswap.py" % name
    return match.group(1)


def swift_literal(name):
    """The body of `static let name = \"\"\" ... \"\"\"` in CswapClient.swift.

    Swift strips from every line the indentation of the CLOSING delimiter, so
    the literal is dedented the same way the compiler would before comparing.
    """
    with open(SWIFT_SOURCE, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r'static let %s = """\n(.*?)\n([ \t]*)"""' % name, text,
                      re.DOTALL)
    assert match, "could not find %s in CswapClient.swift" % name
    body, closing_indent = match.group(1), match.group(2)
    lines = []
    for line in body.split("\n"):
        if line.startswith(closing_indent):
            line = line[len(closing_indent):]
        lines.append(line)
    return "\n".join(lines) + "\n"


class TestSnippetsHaveNotDrifted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(SWIFT_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")

    def test_the_primer_snippet_is_identical_in_both_languages(self):
        self.assertEqual(swift_literal("primerCode"), python_literal("PRIMER_CODE"))

    def test_the_combined_snippet_is_identical_in_both_languages(self):
        self.assertEqual(swift_literal("combinedCode"),
                         python_literal("COMBINED_CODE"))

    def test_the_scraper_actually_found_something(self):
        """Guard against a regex that silently matches nothing.

        Without this, renaming either constant would turn both tests above
        into a comparison of two empty strings — green, and worthless.
        """
        for body in (python_literal("PRIMER_CODE"), swift_literal("primerCode")):
            self.assertIn("ClaudeAccountSwitcher", body)
            self.assertGreater(len(body.splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
