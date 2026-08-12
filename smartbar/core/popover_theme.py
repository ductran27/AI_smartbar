"""Palette, metrics and drawing primitives for the cross-platform popover.

The macOS popover is SwiftUI; the Linux one is painted with cairo. To make
them the same UI rather than two lookalikes, the geometry and colors live
here as plain numbers, `popover_layout.build()` turns them into positioned
primitives, and each platform only rasterises what it is handed. Nothing in
this module imports a graphics library, so it is unit-testable anywhere.

Values mirror PopoverView.swift / AccountCardView.swift / MetricBarRow.swift
1:1. One deliberate difference: macOS cards use `.thinMaterial`, whose blur
has no portable cairo equivalent, so CARD_BG is the solid shade it resolves
to over this background.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from smartbar.core.model import RGB

# --- geometry (points; mirrors the SwiftUI frames) -------------------------
WIDTH = 330.0
PAD = 11.0                 # PopoverView .padding(11)
SECTION_GAP = 8.0          # VStack(spacing: 8)
HEADER_H = 22.0            # equal-size refresh/quit buttons
CARD_GAP = 7.0             # VStack(spacing: 7)
CARD_RADIUS = 12.0
CARD_PAD_V = 9.0
CARD_PAD_H = 11.0
CARD_HEADER_H = 20.0
CARD_INNER_GAP = 7.0
ROW_H = 14.0
ROW_GAP = 7.0
STATE_ROW_H = 16.0         # "Re-login required…" line on a data-less card
STATE_LINE_H = 13.0        # extra height per wrapped line (.lineLimit(2))
STATE_MAX_LINES = 2
# Remove-confirm header: "Remove <full label>?" (see popover_layout._card).
# Wider and bolder than a caption, so it gets its own wrap cap and its own
# per-line height rather than reusing STATE_MAX_LINES/STATE_LINE_H.
CONFIRM_MAX_LINES = 2
CONFIRM_LINE_H = 15.0
DOT_R = 3.5                # 7pt circle
DOT_STROKE = 1.5
LABEL_W = 40.0             # MetricBarRow label column
# MetricBarRow value column, split into two independently right-anchored
# sub-columns — percentage, then countdown — instead of one right-anchored
# string (see popover_layout._card_body: a single string jitters sideways
# every time the countdown's length changes). Sized from real cairo
# metrics for the widest realistic value ("100%" / " · 23h 59m"), each
# with a few points of margin for other platforms' default mono fonts.
VALUE_PCT_W = 28.0
VALUE_COUNTDOWN_W = 66.0
BAR_H = 6.0
BAR_GAP = 9.0              # HStack(spacing: 9)
BUTTON_H = 18.0
TAB_H = 20.0               # provider tab row (Claude | OpenAI), when shown
TAB_GAP = 6.0              # gap between the two tab pills
TAB_TOP_GAP = 3.0          # tabs belong to the header, so they sit tighter
                           # under it than the SECTION_GAP between sections
CHIP_H = 15.0
REMOVE_HIT = 18.0          # square hit target for the per-card remove ✕
REMOVE_ICON = 10.0         # drawn size of the ✕ glyph inside that target
# Gap after a header-row label, e.g. title -> "Updated ..." -> "stale".
# popover_layout derives both offsets from this plus text_width() instead
# of hardcoding an x position (see build()).
HEADER_LABEL_GAP = 6.0

# Tab pills read as faded / not-faded rather than colored: the selected
# provider is full strength, the other recedes (user-picked over an accent
# fill). Mirrored by PopoverView.tabButton.
TAB_BG_SELECTED = (1.0, 1.0, 1.0, 0.16)
TAB_BG_HOVER = (1.0, 1.0, 1.0, 0.12)
TAB_BG = (1.0, 1.0, 1.0, 0.06)
BUTTON_PAD_H = 8.0
FOOTER_H = 20.0
ICON_BUTTON_W = 22.0

# --- type sizes ------------------------------------------------------------
SIZE_TITLE = 13.0          # .headline
SIZE_EMAIL = 12.0          # .callout.weight(.semibold)
SIZE_CAPTION = 10.0        # .caption2
SIZE_ROW_LABEL = 11.0
SIZE_ROW_VALUE = 10.5      # monospaced
SIZE_CHIP = 9.0
SIZE_ICON = 12.5

# --- colors (r, g, b, a in 0..1) -------------------------------------------
WINDOW_BG = (0.11, 0.11, 0.12, 1.0)
CARD_BG = (0.17, 0.17, 0.18, 1.0)
CARD_BORDER = (1.0, 1.0, 1.0, 0.07)
CARD_BORDER_ACTIVE = (1.0, 1.0, 1.0, 0.92)
TEXT = (1.0, 1.0, 1.0, 0.92)
TEXT_SECONDARY = (1.0, 1.0, 1.0, 0.60)
TEXT_TERTIARY = (1.0, 1.0, 1.0, 0.42)
TEXT_SPENT = (1.0, 1.0, 1.0, 0.68)      # MetricBarRow's 100% row
BAR_TRACK = (1.0, 1.0, 1.0, 0.09)
BUTTON_BG = (1.0, 1.0, 1.0, 0.12)
BUTTON_BG_HOVER = (1.0, 1.0, 1.0, 0.20)
BUTTON_BORDER = (1.0, 1.0, 1.0, 0.18)
BUTTON_DISABLED = (1.0, 1.0, 1.0, 0.05)
ACCENT = (0.0, 0.48, 1.0, 1.0)          # .borderedProminent
ACCENT_HOVER = (0.15, 0.56, 1.0, 1.0)
# Destructive confirm ("Remove") — the status ramp's critical red, so both
# renderers and the Swift tint (Status.critical) agree on one red.
DANGER = (0.80, 0.184, 0.184, 1.0)
DANGER_HOVER = (0.88, 0.25, 0.25, 1.0)
WARNING = (1.0, 0.62, 0.20, 1.0)


def status_rgba(name: str, alpha: float = 1.0):
    """Metric color by status name, as an RGBA tuple (see model.RGB)."""
    red, green, blue = RGB[name]
    return (red, green, blue, alpha)


# Advance-width factors for the fonts the renderer selects. The layout has to
# size buttons without a font engine, so it estimates; the renderer then
# measures for real and centres the label inside the box it was given, which
# keeps a wrong estimate cosmetic instead of misaligning anything.
_WIDTH_FACTOR = {"regular": 0.55, "bold": 0.60, "mono": 0.60}


def text_width(text: str, size: float, bold: bool = False,
               mono: bool = False) -> float:
    kind = "mono" if mono else ("bold" if bold else "regular")
    return len(text) * size * _WIDTH_FACTOR[kind]


# --- primitives ------------------------------------------------------------
@dataclass
class Box:
    """Filled and/or stroked rounded rectangle."""
    x: float
    y: float
    w: float
    h: float
    radius: float = 0.0
    fill: tuple = None
    stroke: tuple = None
    line_width: float = 1.0


@dataclass
class Dot:
    cx: float
    cy: float
    r: float
    color: tuple
    hollow: bool = False
    line_width: float = DOT_STROKE


@dataclass
class Label:
    """Text with an anchor; the renderer measures and truncates.

    `anchor` is "left" | "center" | "right" and says what `x` means. Keeping
    measurement in the renderer lets the layout stay free of font metrics
    while text still lands exactly where the design says.
    """
    x: float
    y: float                 # vertical CENTER of the line
    text: str
    size: float = SIZE_CAPTION
    color: tuple = TEXT
    bold: bool = False
    mono: bool = False
    anchor: str = "left"
    max_width: float = 0.0   # 0 = unlimited; else middle-truncated with "…"
    max_lines: int = 1       # >1 word-wraps, like SwiftUI's .lineLimit(2)


@dataclass
class Glyph:
    """A symbol the renderer draws from paths.

    SF Symbols have no cross-platform equivalent and a font that happens to
    carry "⟳" cannot be assumed, so the icon buttons are drawn rather
    than typeset. `kind` is "refresh", "power" or "close".
    """
    kind: str
    cx: float
    cy: float
    size: float
    color: tuple = TEXT


@dataclass
class Hit:
    """A clickable region, named for the action it triggers.

    "card:<provider>:<id>" is one of several non-action names: it covers a
    whole account card so the painted UIs can track "the pointer is on
    this card" (what makes the remove ✕ appear). "stale" and
    "update-held" follow the same convention -- they exist only so a
    front-end can show `tooltip` on hover (FINDING 7), not to be clicked.
    Clicks on any of them do nothing, and a card's real buttons are
    appended after its "card:" hit so they win hit-testing.

    `tooltip` is the explanatory text a front-end shows on hover, worded
    from PopoverView.swift / AccountCardView.swift's `.help()` strings so
    every platform says the same thing (FINDING 8). "" means none.
    """
    name: str                # "refresh" | "quit" | "update" | "switch:<n>"
                             # | "tab:<p>" | "card:<p>:<id>"
                             # | "remove:<p>:<id>" | "confirm-remove:<p>:<id>"
                             # | "cancel-remove" | "stale" | "update-held"
                             # | "dismiss-error"
    x: float
    y: float
    w: float
    h: float
    enabled: bool = True
    tooltip: str = ""

    def contains(self, px: float, py: float) -> bool:
        return (self.enabled and self.x <= px <= self.x + self.w
                and self.y <= py <= self.y + self.h)


@dataclass
class Layout:
    width: float
    height: float
    shapes: list = field(default_factory=list)   # Box | Dot | Label, draw order
    hits: list = field(default_factory=list)

    def hit(self, px: float, py: float):
        """Topmost enabled hit under the point, or None."""
        for candidate in reversed(self.hits):
            if candidate.contains(px, py):
                return candidate
        return None

    def tooltip_at(self, px: float, py: float) -> str:
        """Tooltip text under the point, or "" — DISABLED hits included.

        Deliberately not `self.hit(px, py).tooltip`: `contains()` refuses a
        disabled hit, and a disabled control is exactly the one whose
        tooltip matters most. The blocked "Make Active" button carries the
        only explanation of WHY it cannot be clicked ("Stored credential is
        dead — switching would log Claude Code out"), mirroring the
        `.help()` SwiftUI keeps on its own `.disabled()` button; routing
        tooltips through hit() would author that string and then make it
        unreachable on both painted front-ends.
        """
        for candidate in reversed(self.hits):
            if (candidate.x <= px <= candidate.x + candidate.w
                    and candidate.y <= py <= candidate.y + candidate.h):
                if candidate.tooltip:
                    return candidate.tooltip
        return ""
