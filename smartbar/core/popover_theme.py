"""Palette, metrics and drawing primitives for the cross-platform popover.

The macOS popover is SwiftUI; the Linux one is painted with cairo. To make
them the same UI rather than two lookalikes, the geometry and colors live
here as plain numbers, `popover_layout.build()` turns them into positioned
primitives, and each platform only rasterises what it is handed. Nothing in
this module imports a graphics library, so it is unit-testable anywhere.

Values mirror PopoverView.swift / AccountCardView.swift / MetricBarRow.swift
1:1. macOS cards used to be `.thinMaterial`, whose blur has no portable cairo
equivalent, leaving `card_bg` as the solid shade it happened to resolve to;
they are now that solid colour on every platform. The pace notch is what
settled it — it is drawn as the card's own ground showing through the bar
(see PACE_W), which only lands exactly if that ground is a known colour
rather than whatever a blur resolves to over the desktop behind it.

Geometry is module-level and shared; COLOUR is per-appearance and lives in a
Scheme (DARK / LIGHT). The split is the point: the panel follows the system
appearance, and because only colour varies, a layout is the same shape in
both — nothing re-measures, and reviewing one appearance reviews the other's
geometry too.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from smartbar.core.model import RGB, RGB_LIGHT

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
# A row is two stacked lines: the label/pct/countdown line, then a bar that
# gets the card's FULL inner width instead of splitting it with a label
# column (see popover_layout._card_body). ROW_H is their sum
# (12 + 2 + 6 = 20), so card_height's row-counting arithmetic needs no
# change of its own beyond this constant moving.
#
# A three-line row — name, bar, then a "45% used" readout under it — was
# tried and reverted. It read well in isolation and cost more than it was
# worth in place: at four accounts the panel it produced was over 700pt
# tall, so a card that used to be glanceable became something to scroll,
# and the whole point of a menu-bar panel is that it answers a question
# before you have finished opening it. Density IS the feature here.
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
# MetricBarRow's label/pct/countdown line has a label column and two value
# sub-columns — only the BAR sits on its own line below (see
# popover_layout._card_body). LABEL_W caps the label's truncation width;
# VALUE_PCT_W/VALUE_COUNTDOWN_W split the value into two independently
# right-anchored sub-columns — percentage, then countdown — instead of one
# right-anchored string (a single string jitters sideways every time the
# countdown's length changes, FINDING 3). Sized from real cairo metrics for
# the widest realistic value ("100%" / " · 23h 59m"), each with a few points
# of margin for other platforms' default mono fonts.
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
# single bar cannot answer. It never joins the status ramp, so it cannot
# compete with the fill for the first glance: the fill says "how worried
# should I be", the caret says "am I roughly on schedule for that", and
# giving them related colours would blur both answers into a bar even a
# burn-rate expert can't parse quickly.
#
# It is drawn as a NOTCH — the card's own ground, cut through the bar —
# rather than as a translucent hairline over it. A hairline has to be
# legible against two different backgrounds at once (bare track AND
# saturated fill) and was washing out over both; a notch is a hole, so it
# contrasts with whatever it interrupts, automatically. It also gets
# sharper exactly as the fill grows past it, which is when "am I ahead of
# schedule" starts to matter.
#
# It stays a HAIRLINE's width: an opaque notch earns its legibility from
# being a hole rather than from being wide, and widening it starts to read
# as a bar cut into two segments rather than one bar with a mark on it.
PACE_W = 1.5
BUTTON_H = 18.0
TAB_H = 20.0               # provider tab row (Claude | OpenAI), shown only
                           # when both providers actually have accounts
TAB_GAP = 6.0              # gap between adjacent tab pills
TAB_TOP_GAP = 3.0          # tabs belong to the header, so they sit tighter
                           # under it than the SECTION_GAP between sections
# Each tab pill carries its provider's mark to the LEFT of its label, not
# instead of it: the mark alone is not how most people tell Claude and
# OpenAI's tabs apart, and a label-less pill would stop being readable to
# anyone who doesn't recognise it on sight. TAB_MARK sizes the Glyph;
# TAB_MARK_GAP is the gap before the label starts (see popover_layout.build).
#
# Stacking the mark ABOVE the label at 16pt was tried and reverted with the
# three-line row: a 40pt tab row is a lot of the panel's height spent on a
# control that is only shown at all when two providers are installed.
TAB_MARK = 11.0
TAB_MARK_GAP = 5.0
CHIP_H = 15.0
REMOVE_HIT = 18.0          # square hit target for the per-card remove ✕
REMOVE_ICON = 10.0         # drawn size of the ✕ glyph inside that target
# Gap after a header-row label, e.g. title -> "Updated ..." -> "stale".
# popover_layout derives both offsets from this plus text_width() instead
# of hardcoding an x position (see build()).
HEADER_LABEL_GAP = 6.0

BUTTON_PAD_H = 8.0
FOOTER_H = 20.0
ICON_BUTTON_W = 22.0

# --- type sizes ------------------------------------------------------------
SIZE_TITLE = 13.0          # .headline
SIZE_EMAIL = 12.0          # .callout.weight(.semibold)
SIZE_CAPTION = 10.0        # .caption2
# Label and value share one optical size on purpose: they sit on the SAME
# line, and a size step between them would read as a hierarchy that isn't
# there — they are one fact ("5h: 42% · 3h 12m"), not a heading over a body.
# A parity test pins the equality, because it is the relationship the row
# depends on rather than either number on its own.
SIZE_ROW_LABEL = 10.5
SIZE_ROW_VALUE = 10.5      # monospaced
SIZE_CHIP = 9.0
SIZE_ICON = 12.5

# --- colors (r, g, b, a in 0..1) -------------------------------------------
# Colour lives in a Scheme, not in module constants, because the popover
# follows the system appearance instead of forcing dark. Everything ABOVE is
# geometry and is shared: a layout built for one scheme is exactly the same
# shape as the other, so a front-end can hand build() whichever appearance
# the user is in without re-measuring anything, and a review of one is a
# review of both.


@dataclass(frozen=True)
class Scheme:
    """Every colour the popover paints, for one appearance.

    The two grounds (window/card) and four inks stay blue-shifted rather
    than host-neutral in BOTH appearances, so the panel reads as the same
    instrument sitting on the desktop rather than a sheet of it — a light
    panel that borrowed the system's warm greys would be a different
    product wearing the same layout. The inks are explicit cool greys at
    full alpha, never ink-with-alpha, so the tint survives compositing
    instead of washing out over whatever sits behind the popover.

    `status` is the used-ramp (model.RGB / model.RGB_LIGHT) and is the ONE
    place colour carries meaning here, which is why nothing else in this
    class is allowed to be saturated. The light ramp is darkened rather
    than reused: the dark values were tuned against a near-black ground and
    its green and amber fall below usable contrast on white.
    """
    name: str
    window_bg: tuple
    card_bg: tuple
    card_border: tuple
    text: tuple
    text_secondary: tuple
    text_tertiary: tuple
    text_spent: tuple
    bar_track: tuple
    pace: tuple
    button_bg: tuple
    button_bg_hover: tuple
    button_border: tuple
    button_disabled: tuple
    # Tab pills read as faded / not-faded rather than coloured: the selected
    # provider is full strength, the other recedes. These are ink-over-ground
    # overlays and therefore genuinely different per appearance — the dark
    # scheme's white-alpha values are invisible on a light card, which is the
    # single reason they had to leave module scope and move in here.
    tab_bg_selected: tuple
    tab_bg_hover: tuple
    tab_bg: tuple
    accent: tuple
    accent_hover: tuple
    accent_text: tuple
    danger: tuple
    danger_hover: tuple
    warning: tuple
    status: dict

    def status_rgba(self, name: str, alpha: float = 1.0) -> tuple:
        """Metric colour by status name, as RGBA (see model.RGB)."""
        red, green, blue = self.status[name]
        return (red, green, blue, alpha)


# One cool grey doing two jobs: it is the SECONDARY ink on light and the
# TERTIARY ink on dark. Not a copy-paste — the middle of a single grey
# scale genuinely lands in different places depending on which end the
# ground sits at, and naming it once is what keeps the two ramps a system
# rather than two hand-tuned lists.
_SLATE = (0.361, 0.400, 0.447, 1.0)
# The pace notch is the card's own ground showing through the bar, so it is
# named once per scheme and used for BOTH `card_bg` and `pace` — the two
# cannot be allowed to drift, or the notch stops being a hole and becomes a
# faint grey stripe that happens to sit near the card colour.
_DARK_CARD = (0.090, 0.110, 0.133, 1.0)
_LIGHT_CARD = (1.0, 1.0, 1.0, 1.0)

DARK = Scheme(
    name="dark",
    window_bg=(0.059, 0.071, 0.086, 1.0),
    card_bg=_DARK_CARD,
    card_border=(1.0, 1.0, 1.0, 0.06),      # hairline, same on every card
    text=(0.914, 0.929, 0.949, 1.0),             # "chalk"
    text_secondary=(0.596, 0.639, 0.690, 1.0),   # "mist"
    text_tertiary=_SLATE,                        # "dim"
    text_spent=(0.725, 0.757, 0.796, 1.0),       # a 100%-used readout
    bar_track=(1.0, 1.0, 1.0, 0.09),
    pace=_DARK_CARD,
    button_bg=(1.0, 1.0, 1.0, 0.12),
    button_bg_hover=(1.0, 1.0, 1.0, 0.20),
    button_border=(1.0, 1.0, 1.0, 0.18),
    button_disabled=(1.0, 1.0, 1.0, 0.05),
    tab_bg_selected=(1.0, 1.0, 1.0, 0.16),
    tab_bg_hover=(1.0, 1.0, 1.0, 0.12),
    tab_bg=(1.0, 1.0, 1.0, 0.06),
    accent=(0.0, 0.48, 1.0, 1.0),           # .borderedProminent
    accent_hover=(0.15, 0.56, 1.0, 1.0),
    accent_text=(1.0, 1.0, 1.0, 1.0),
    # Destructive confirm ("Remove") — the status ramp's critical red, so
    # both renderers and the Swift tint (Status.critical) agree on one red.
    danger=(0.80, 0.184, 0.184, 1.0),
    danger_hover=(0.88, 0.25, 0.25, 1.0),
    warning=(1.0, 0.62, 0.20, 1.0),
    status=RGB,
)

LIGHT = Scheme(
    name="light",
    window_bg=(0.949, 0.957, 0.969, 1.0),
    # White, so a card LIFTS off the window rather than being outlined onto
    # it. On dark the two grounds are close and the hairline does the
    # separating; on light the value gap does it, so the hairline can be
    # quieter than a naive inversion of the dark one.
    card_bg=_LIGHT_CARD,
    card_border=(0.0, 0.0, 0.0, 0.07),
    text=(0.086, 0.106, 0.133, 1.0),             # "ink"
    text_secondary=_SLATE,
    text_tertiary=(0.545, 0.588, 0.639, 1.0),    # "haze"
    text_spent=(0.263, 0.294, 0.341, 1.0),
    bar_track=(0.0, 0.0, 0.0, 0.08),
    pace=_LIGHT_CARD,
    button_bg=(0.0, 0.0, 0.0, 0.06),
    button_bg_hover=(0.0, 0.0, 0.0, 0.11),
    button_border=(0.0, 0.0, 0.0, 0.14),
    button_disabled=(0.0, 0.0, 0.0, 0.05),
    tab_bg_selected=(0.0, 0.0, 0.0, 0.12),
    tab_bg_hover=(0.0, 0.0, 0.0, 0.09),
    tab_bg=(0.0, 0.0, 0.0, 0.05),
    accent=(0.0, 0.42, 0.90, 1.0),
    accent_hover=(0.0, 0.36, 0.82, 1.0),
    accent_text=(1.0, 1.0, 1.0, 1.0),
    danger=(0.76, 0.145, 0.145, 1.0),
    danger_hover=(0.66, 0.11, 0.11, 1.0),
    warning=(0.72, 0.42, 0.04, 1.0),
    status=RGB_LIGHT,
)

SCHEMES = {"dark": DARK, "light": LIGHT}


def scheme_for(name: str) -> Scheme:
    """The Scheme called `name`, defaulting to dark for anything unknown.

    Front-ends pass through whatever the host reports its appearance to be,
    which is a different string on every platform and can be absent
    entirely; an unrecognised one has to land somewhere, and dark is where
    this panel spent its whole life before light existed.
    """
    return SCHEMES.get((name or "").strip().lower(), DARK)


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
    color: tuple             # no default: ink depends on the Scheme now, and
                             # a default would silently paint one appearance's
                             # colour onto the other
    size: float = SIZE_CAPTION
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
    "claude", "openai" (the provider marks beside a tab's label), "clock"
    (a metric row's countdown), "pause" (the footer's "update held") and
    "warn" (a blocked account's state line).
    """
    kind: str
    cx: float
    cy: float
    size: float
    color: tuple             # see Label.color — no default, same reason


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
    # The window ground this layout's colours were chosen against. It rides
    # on the layout rather than being passed alongside it because the shapes
    # already have their Scheme baked in: a painter handed a light layout and
    # a dark background would render light cards on a dark window, and there
    # would be nothing in either argument to say which one was wrong.
    background: tuple = DARK.window_bg

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
