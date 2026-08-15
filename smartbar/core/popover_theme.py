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
RAIL_W = 2.5                # active-card leading rail width
RAIL_INSET = 3.0            # vertical inset so the rail reads as a mark,
                            # not a second border
# A row is two stacked lines now, not one: the label/pct/countdown line,
# then a bar that gets the card's FULL inner width instead of splitting it
# with a label column (see popover_layout._card_body). ROW_H is their sum
# (12 + 2 + 6 = 20), so card_height's row-counting arithmetic needs no
# change of its own beyond this constant moving.
ROW_LABEL_H = 12.0        # the label/pct/countdown line
ROW_LABEL_GAP = 2.0       # gap between that line and the bar under it
ROW_H = 20.0
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
# MetricBarRow's label/pct/countdown line still has a label column and two
# value sub-columns — only the BAR moved out from between them onto its own
# line below (see popover_layout._card_body). LABEL_W still caps the
# label's truncation width; VALUE_PCT_W/VALUE_COUNTDOWN_W still split the
# value into two independently right-anchored sub-columns — percentage,
# then countdown — instead of one right-anchored string (a single string
# jitters sideways every time the countdown's length changes, FINDING 3).
# Sized from real cairo metrics for the widest realistic value ("100%" /
# " · 23h 59m"), each with a few points of margin for other platforms'
# default mono fonts.
LABEL_W = 40.0             # MetricBarRow label column
VALUE_PCT_W = 28.0
VALUE_COUNTDOWN_W = 66.0
BAR_H = 6.0
# Gap between the label and the value area on the label line — the bar on
# the line below it has no such gap; it spans the card's full inner width.
BAR_GAP = 9.0              # Spacer(minLength: 9) in MetricBarRow's label line
# One "small glyph beside a line of text" size shared by every spot that
# needs it — the countdown's clock, a blocked account's warn triangle, and
# the footer's pause mark — rather than three near-identical pairs of
# constants for what is visually the same job three times.
COUNTDOWN_ICON = 9.0
COUNTDOWN_ICON_GAP = 3.0
# The pace caret marks "how far through this window are we" independently
# of the fill, which marks "how much is spent" — two different questions a
# single bar cannot answer. It stays a neutral hairline rather than joining
# the status ramp so it never competes with the fill for the user's first
# glance: the fill says "how worried should I be", the caret says "am I
# roughly on schedule for that", and conflating their colors would blur
# both answers into a bar even a burn-rate expert can't parse at a glance.
PACE_W = 1.5
PACE = (1.0, 1.0, 1.0, 0.38)
BUTTON_H = 18.0
TAB_H = 20.0               # provider tab row (Claude | OpenAI), when shown
TAB_GAP = 6.0              # gap between the two tab pills
TAB_TOP_GAP = 3.0          # tabs belong to the header, so they sit tighter
                           # under it than the SECTION_GAP between sections
# Each tab pill now carries its provider's mark to the LEFT of its label,
# not instead of it: the mark alone is not how most people tell Claude and
# OpenAI's tabs apart, and a label-less pill would stop being readable to
# anyone who doesn't recognise it on sight. TAB_MARK sizes the Glyph;
# TAB_MARK_GAP is the gap before the label starts (see popover_layout.build).
TAB_MARK = 11.0
TAB_MARK_GAP = 5.0
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
# Label and value share one optical size on purpose: they are about to sit
# on the SAME line (stage 02 moves the label off its own column), and a size
# step between them would read as a hierarchy that isn't there — they are
# one fact ("5h: 42% · 3h 12m"), not a heading over a body.
SIZE_ROW_LABEL = 10.5
SIZE_ROW_VALUE = 10.5      # monospaced
SIZE_CHIP = 9.0
SIZE_ICON = 12.5

# --- colors (r, g, b, a in 0..1) -------------------------------------------
# WINDOW_BG/CARD_BG are blue-shifted rather than macOS-neutral greys, so the
# panel reads as an instrument sitting on the desktop rather than a sheet of
# it. TEXT* stopped being white-with-alpha and became explicit cool greys at
# full alpha ("chalk"/"mist"/"dim"/spent-value), so the tint survives
# compositing instead of washing out over whatever sits behind the popover.
WINDOW_BG = (0.059, 0.071, 0.086, 1.0)
CARD_BG = (0.090, 0.110, 0.133, 1.0)
CARD_BORDER = (1.0, 1.0, 1.0, 0.06)        # hairline, same on every card now
TEXT = (0.914, 0.929, 0.949, 1.0)          # "chalk"
TEXT_SECONDARY = (0.596, 0.639, 0.690, 1.0)  # "mist"
TEXT_TERTIARY = (0.361, 0.400, 0.447, 1.0)   # "dim"
TEXT_SPENT = (0.725, 0.757, 0.796, 1.0)    # MetricBarRow's 100% row
# The active card used to be told apart by a 1.5pt pure-white border — the
# loudest mark on the panel, spent on information the ACTIVE chip already
# carries. A leading rail replaces it, and the rail borrows TEXT rather than
# joining the status ramp: colour on this panel is reserved for how much
# budget is left, and tinting "this is the active account" would make it
# compete with that signal instead of just marking presence.
RAIL = TEXT
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
    carry "⟳" cannot be assumed, so every symbol on the panel is drawn
    rather than typeset. `kind` is one of: "refresh", "close", "power",
    "quit" (the header's Quit button — the same power-ring shape as
    "power", under its own name because its Hit is also named "quit"),
    "overview", "claude", "openai" (the provider marks beside a tab's
    label), "clock" (a metric row's countdown), "pause" (the footer's
    "update held") and "warn" (a blocked account's state line).
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
