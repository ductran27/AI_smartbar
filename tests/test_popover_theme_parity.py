"""The popover's chrome tokens exist twice, in two languages. Pin them
together.

smartbar/core/popover_theme.py owns the two grounds (WINDOW_BG, CARD_BG) and
four inks (TEXT, TEXT_SECONDARY, TEXT_TERTIARY, TEXT_SPENT) every renderer
paints from. macos-swift/Sources/AISmartbar/PopoverPalette.swift re-declares
the same six colors by hand, because SwiftUI draws the macOS popover instead
of reading popover_theme.py directly. Nothing enforced that the two stayed in
step until this file — same approach as test_model_parity.py: read the Swift
as SOURCE TEXT, so a value drifting on one side fails the ordinary unit suite
with no Swift toolchain and no Xcode required.
"""
from __future__ import annotations

import os
import re
import unittest

import smartbar
from smartbar.core import popover_theme as theme

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
SWIFT_DIR = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")
PALETTE_SOURCE = os.path.join(SWIFT_DIR, "PopoverPalette.swift")

# `static let chalk = Color(red: 0.914, green: 0.929, blue: 0.949)` — one
# literal decimal per channel, one declaration per line (see PopoverPalette's
# own header comment: a scraping test reads it as text).
_COLOR_CASE = re.compile(
    r"static let (\w+) = Color\(red: ([\d.]+), green: ([\d.]+), "
    r"blue: ([\d.]+)\)")

# Swift property name -> the popover_theme token it must match. PopoverPalette
# only carries the four inks and two grounds this stage retuned; everything
# else (borders, bars, buttons, tabs, accent/danger/warning) stays a
# translucent white-alpha overlay and has no Swift twin to pin.
_TOKEN_FOR = {
    "windowBG": "WINDOW_BG",
    "cardBG": "CARD_BG",
    "chalk": "TEXT",
    "mist": "TEXT_SECONDARY",
    "dim": "TEXT_TERTIARY",
    "spent": "TEXT_SPENT",
}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def swift_palette() -> dict:
    """{Swift property name: (r, g, b)} as PopoverPalette.swift declares it."""
    text = _read(PALETTE_SOURCE)
    return {name: (float(r), float(g), float(b))
            for name, r, g, b in _COLOR_CASE.findall(text)}


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(PALETTE_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestTheScraperFoundSomething(SwiftPresent):
    """Guard against a regex that silently matches nothing — without this,
    renaming a property on the Swift side would turn every assertion below
    into a comparison of two empty containers: green, and worthless."""

    def test_the_scraper_returns_a_full_set(self):
        self.assertEqual(len(swift_palette()), 6)


class TestChromeParity(SwiftPresent):
    def test_every_chrome_color_matches_in_both_languages(self):
        swift = swift_palette()
        for swift_name, token in sorted(_TOKEN_FOR.items()):
            with self.subTest(color=swift_name):
                python_rgb = tuple(round(v, 3) for v in theme.__dict__[token][:3])
                self.assertEqual(
                    swift.get(swift_name), python_rgb,
                    "PopoverPalette.swift's %s and popover_theme.%s disagree "
                    "— the Mac would paint a different chrome color from "
                    "every other front-end" % (swift_name, token))


if __name__ == "__main__":
    unittest.main()
