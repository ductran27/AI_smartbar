"""The status ramp exists twice, in two languages. Pin them together.

smartbar/core/model.py owns the ramp every UI renders: the six status names,
the RGB each one paints, and the three used-% boundaries between them. The
Swift app re-declares all three by hand — Status/Thresholds in Models.swift,
the colors in StatusPalette.swift, whose own header already says "must match
model.RGB" — and until this file nothing enforced that.

It was the last duplicated policy with no parity test: presence, plan, codex,
update and the cswap primer snippets are all pinned by source-scraping tests
(tests/test_presence.py's TestMacAndLinuxAgree records catching a real drift
this way; tests/test_cswap_parity.py was written because `grep CswapClient
tests/` came back empty). The values agree today — this keeps them agreeing.

Same approach as those: read the Swift as SOURCE TEXT, so it runs in the
ordinary unit suite with no Swift toolchain and a contributor who never opens
Xcode still cannot break the Mac.
"""
from __future__ import annotations

import os
import re
import unittest

import smartbar
from smartbar.core import model

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
SWIFT_DIR = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")
PALETTE_SOURCE = os.path.join(SWIFT_DIR, "StatusPalette.swift")
MODELS_SOURCE = os.path.join(SWIFT_DIR, "Models.swift")

# `NSColor(red: 0.18, green: 0.65, blue: 0.32, alpha: 1)` and the shorthand
# `NSColor(white: 0.45, alpha: 1)` the gray uses.
_RGB_CASE = re.compile(
    r"case \.(\w+): return NSColor\(red: ([\d.]+), green: ([\d.]+), "
    r"blue: ([\d.]+), alpha: 1\)")
_WHITE_CASE = re.compile(
    r"case \.(\w+): return NSColor\(white: ([\d.]+), alpha: 1\)")
# `static var yellow: Double { value("SMARTBAR_YELLOW", 50) }`
_THRESHOLD = re.compile(
    r'static var (\w+): Double \{ value\("(\w+)", ([\d.]+)\) \}')
# `enum Status: String { case green, yellow, low, critical, full, gray }`
_STATUS_ENUM = re.compile(r"enum Status: String \{\s*case ([^\n}]+)")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def swift_palette(scheme: str = "dark") -> dict:
    """{status name: (r, g, b)} for ONE of StatusPalette.swift's ramps.

    Sliced by property declaration rather than scraped from the whole file:
    the dark and light ramps use the same six case names, so a flat pass
    would let the second silently overwrite the first and compare a ramp
    against itself.
    """
    text = _read(PALETTE_SOURCE)
    start = text.index("var %sNSColor: NSColor {" % scheme)
    rest = text[start:]
    end = rest.find("\n    }")
    block = rest if end < 0 else rest[:end]
    found = {name: (float(r), float(g), float(b))
             for name, r, g, b in _RGB_CASE.findall(block)}
    found.update({name: (float(v), float(v), float(v))
                  for name, v in _WHITE_CASE.findall(block)})
    return found


def swift_thresholds() -> dict:
    """{env var name: default} as Models.swift's Thresholds declares it."""
    return {env: float(default)
            for _, env, default in _THRESHOLD.findall(_read(MODELS_SOURCE))}


def swift_status_names() -> set:
    match = _STATUS_ENUM.search(_read(MODELS_SOURCE))
    assert match, "could not find `enum Status` in Models.swift"
    return {name.strip() for name in match.group(1).split(",") if name.strip()}


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (PALETTE_SOURCE, MODELS_SOURCE):
            if not os.path.exists(path):
                raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestTheScraperFoundSomething(SwiftPresent):
    """Guard against regexes that silently match nothing — without this,
    renaming anything on the Swift side would turn every assertion below into
    a comparison of two empty containers: green, and worthless."""

    def test_all_three_scrapers_return_a_full_set(self):
        self.assertEqual(len(swift_palette("dark")), 6)
        self.assertEqual(len(swift_palette("light")), 6)
        # The slicing is the part most likely to break quietly: a bad index
        # would hand both calls the same block, and every comparison below
        # would still pass on one ramp's values.
        self.assertNotEqual(swift_palette("dark"), swift_palette("light"))
        self.assertEqual(len(swift_thresholds()), 3)
        self.assertEqual(len(swift_status_names()), 6)


class TestStatusNames(SwiftPresent):
    def test_both_languages_declare_the_same_six_statuses(self):
        self.assertEqual(swift_status_names(), set(model.RGB))

    def test_python_renders_every_status_it_can_return(self):
        """DOT and RGB must cover the same names: model.py's own header
        warns a missing key is a runtime crash in a UI we may not be able
        to run."""
        self.assertEqual(set(model.DOT), set(model.RGB))


class TestPalette(SwiftPresent):
    def test_every_status_paints_the_same_rgb_in_both_languages(self):
        """Both ramps: the dark one every renderer has always used, and the
        light one that arrived with the appearance-following panel. A light
        value drifting is exactly as invisible on a dark Mac as a dark one
        drifting is on a light Linux box, so neither can go unpinned."""
        for scheme, ramp in (("dark", model.RGB), ("light", model.RGB_LIGHT)):
            swift = swift_palette(scheme)
            for name, rgb in sorted(ramp.items()):
                with self.subTest(scheme=scheme, status=name):
                    self.assertEqual(
                        swift.get(name), rgb,
                        "StatusPalette.swift's %s ramp and model's disagree "
                        "about %s — the Mac would paint a different color "
                        "from every other front-end" % (scheme, name))

    def test_both_ramps_cover_exactly_the_same_statuses(self):
        """A status present in one ramp and missing from the other is a
        KeyError the moment someone switches appearance — and model.py's own
        header warns that a missing key crashes a UI we may not be able to
        run."""
        self.assertEqual(set(model.RGB_LIGHT), set(model.RGB))


class TestThresholds(SwiftPresent):
    def test_the_three_boundaries_default_the_same_in_both_languages(self):
        swift = swift_thresholds()
        for env, default in (("SMARTBAR_YELLOW", model.DEFAULT_YELLOW_USED),
                             ("SMARTBAR_LOW", model.DEFAULT_LOW_USED),
                             ("SMARTBAR_RED", model.DEFAULT_RED_USED)):
            with self.subTest(threshold=env):
                self.assertEqual(swift.get(env), default,
                                 "%s defaults differently on macOS — the same "
                                 "usage would show a different color there"
                                 % env)

    def test_the_ramp_is_ordered_so_every_band_is_reachable(self):
        """Boundaries out of order would make a band unreachable in both
        languages at once, which no cross-language comparison can catch."""
        self.assertLess(model.DEFAULT_YELLOW_USED, model.DEFAULT_LOW_USED)
        self.assertLess(model.DEFAULT_LOW_USED, model.DEFAULT_RED_USED)
        self.assertLess(model.DEFAULT_RED_USED, 100.0)


if __name__ == "__main__":
    unittest.main()
