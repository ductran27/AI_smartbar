"""The popover's colours exist twice, in two languages. Pin them together.

smartbar/core/popover_theme.py owns a Scheme per appearance (DARK, LIGHT) —
every ground, ink and overlay every renderer paints from.
macos-swift/Sources/AISmartbar/PopoverPalette.swift re-declares the same two
palettes by hand, because SwiftUI draws the macOS popover instead of reading
popover_theme.py directly. Same approach as test_model_parity.py: read the
Swift as SOURCE TEXT, so a value drifting on one side fails the ordinary unit
suite with no Swift toolchain and no Xcode required.

This used to pin six colours on one scheme, leaving the borders, bars,
buttons and tabs unpinned on the grounds that they were "a translucent
white-alpha overlay with no Swift twin". That stopped being true when light
arrived: an overlay is now a real per-scheme value — white-on-dark versus
black-on-light — and getting one wrong paints white on white. So every field
of the Scheme is pinned, in both appearances, and TestEveryFieldIsCovered
makes adding a Scheme field without a Swift twin a failure rather than a
silent gap.
"""
from __future__ import annotations

import dataclasses
import os
import re
import unittest

import smartbar
from smartbar.core import popover_theme as theme

REPO = os.path.dirname(os.path.dirname(os.path.abspath(smartbar.__file__)))
SWIFT_DIR = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")
PALETTE_SOURCE = os.path.join(SWIFT_DIR, "PopoverPalette.swift")

# `windowBG: Color(red: 0.059, green: 0.071, blue: 0.086, opacity: 1.0),` —
# one entry per line, always all four arguments even when opacity is 1 (see
# PopoverPalette's own header: a scraping test reads it as text).
_COLOR_ENTRY = re.compile(
    r"(\w+): Color\(red: ([\d.]+), green: ([\d.]+), blue: ([\d.]+), "
    r"opacity: ([\d.]+)\)")

# Swift property name -> the Scheme field it must match.
_FIELD_FOR = {
    "windowBG": "window_bg",
    "cardBG": "card_bg",
    "cardBorder": "card_border",
    "text": "text",
    "textSecondary": "text_secondary",
    "textTertiary": "text_tertiary",
    "textSpent": "text_spent",
    "barTrack": "bar_track",
    "pace": "pace",
    "buttonBG": "button_bg",
    "buttonBGHover": "button_bg_hover",
    "buttonBorder": "button_border",
    "buttonDisabled": "button_disabled",
    "tabBGSelected": "tab_bg_selected",
    "tabBGHover": "tab_bg_hover",
    "tabBG": "tab_bg",
    "accent": "accent",
    "accentHover": "accent_hover",
    "accentText": "accent_text",
    "danger": "danger",
    "dangerHover": "danger_hover",
    "warning": "warning",
}

# The two Scheme fields that are deliberately NOT colours: `name` is a label
# and `status` is the used-ramp, which lives in model.RGB/RGB_LIGHT and is
# pinned by test_model_parity.py against StatusPalette.swift instead.
_NOT_A_COLOR = {"name", "status"}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def swift_palette(scheme: str) -> dict:
    """{Swift property: (r, g, b, a)} for one `static let <scheme>` block.

    Sliced by declaration rather than scraped from the whole file: both
    palettes use identical property names, so one flat pass would let the
    second silently overwrite the first and compare light against light.
    """
    text = _read(PALETTE_SOURCE)
    start = text.index(f"static let {scheme} = Palette(")
    rest = text[start + 1:]
    end = rest.find("static let ")
    block = rest if end < 0 else rest[:end]
    return {name: (float(r), float(g), float(b), float(a))
            for name, r, g, b, a in _COLOR_ENTRY.findall(block)}


class SwiftPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(PALETTE_SOURCE):
            raise unittest.SkipTest("macos-swift/ is not in this checkout")


class TestTheScraperFoundSomething(SwiftPresent):
    """Guard against a regex that silently matches nothing — without this,
    renaming a property on the Swift side would turn every assertion below
    into a comparison of two empty containers: green, and worthless."""

    def test_each_scheme_block_yields_a_full_palette(self):
        for scheme in ("dark", "light"):
            with self.subTest(scheme=scheme):
                self.assertEqual(len(swift_palette(scheme)), len(_FIELD_FOR))

    def test_the_two_blocks_are_actually_different(self):
        # The slicing above is the part most likely to break quietly: a bad
        # index would hand both calls the same block and every comparison
        # would still pass on one scheme's values.
        self.assertNotEqual(swift_palette("dark"), swift_palette("light"))


class TestEveryFieldIsCovered(SwiftPresent):
    def test_the_map_names_every_colour_field_the_scheme_has(self):
        fields = {f.name for f in dataclasses.fields(theme.Scheme)}
        self.assertEqual(set(_FIELD_FOR.values()), fields - _NOT_A_COLOR,
                         "a Scheme colour gained or lost a field without "
                         "_FIELD_FOR following — the Mac would paint an "
                         "unpinned colour that nothing checks")


class TestChromeParity(SwiftPresent):
    def test_every_colour_matches_in_both_languages(self):
        for scheme_name, scheme in (("dark", theme.DARK),
                                    ("light", theme.LIGHT)):
            swift = swift_palette(scheme_name)
            for swift_name, field in sorted(_FIELD_FOR.items()):
                with self.subTest(scheme=scheme_name, color=swift_name):
                    python_rgba = tuple(round(v, 3)
                                        for v in getattr(scheme, field))
                    self.assertEqual(
                        swift.get(swift_name), python_rgba,
                        "PopoverPalette.%s's %s and popover_theme.%s.%s "
                        "disagree — the Mac would paint a different colour "
                        "from every other front-end"
                        % (scheme_name, swift_name, scheme.name.upper(),
                           field))


class TestThePaceNotchIsTheCardsOwnGround(SwiftPresent):
    """The caret is drawn as a hole in the bar, so it has to BE the card
    colour. A near-miss would read as a faint grey stripe rather than as a
    notch, in a way no colour-by-colour comparison would flag."""

    def test_pace_equals_card_bg_in_both_schemes_and_languages(self):
        for scheme_name, scheme in (("dark", theme.DARK),
                                    ("light", theme.LIGHT)):
            with self.subTest(scheme=scheme_name):
                self.assertEqual(scheme.pace, scheme.card_bg)
                swift = swift_palette(scheme_name)
                self.assertEqual(swift["pace"], swift["cardBG"])


if __name__ == "__main__":
    unittest.main()
