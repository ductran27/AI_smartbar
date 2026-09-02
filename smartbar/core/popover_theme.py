"""Palette, metrics and drawing primitives for the cross-platform popover.

The macOS popover is SwiftUI; the Linux one is painted with cairo. To make
them the same UI rather than two lookalikes, the geometry and colors live
here as plain numbers, `popover_layout.build()` turns them into positioned
primitives, and each platform only rasterises what it is handed. Nothing in
this module imports a graphics library, so it is unit-testable anywhere.

Values mirror PopoverView.swift / AccountCardView.swift / MetricBarRow.swift
1:1. macOS cards used to be `.thinMaterial`, whose blur has no portable cairo
equivalent, leaving `card_bg` as the solid shade it happened to resolve to;
they are now that solid colour on every platform, so a card is the same
object on macOS, Linux and Windows rather than a blur and two imitations of
one.

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
# Every number below has been scaled up twice from the original table —
# ~15%, then a further 10% — as deliberate, uniform scale-ups (not
# redesigns) so the panel reads comfortably on a retina display without
# changing any proportion or layout formula.
# Scale the WHOLE table together if it ever needs to move again; picking
# just one constant back to its old size throws every relationship below
# out of step with its Swift/cairo twin.
#
# That second pass shipped at 20% and was dialled back to 10% on sight —
# 456pt is simply too much panel for a menu-bar popover. The table here is
# re-derived from the ~15% values, NOT shrunk from the 20% ones, so the two
# roundings cannot compound; do the same if it moves again.
#
# Scaled values are SNAPPED to the nearest 0.5pt, which is why the ratios
# are not all exactly 1.10: half-points land on retina half-pixel
# boundaries and stay crisp, where a 12.65 or 15.4 does not. The hairlines
# (PACE_W, DOT_STROKE, RAIL_INSET, PACE_H) are the deliberate exception —
# at a 0.5pt quantum a 10% step does not move them at all, so they kept the
# values the 20% pass gave them. That is correct rather than an oversight:
# they are marks whose whole job is to stay hairline-thin, and a caret
# scaled in proportion starts to read as a second, empty bar.
WIDTH = 418.0
PAD = 14.0                 # PopoverView .padding(14)
SECTION_GAP = 10.0         # VStack(spacing: 10)
HEADER_H = 27.5            # equal-size refresh/quit buttons
CARD_GAP = 9.0             # VStack(spacing: 9)
CARD_RADIUS = 15.5
CARD_PAD_V = 11.5
CARD_PAD_H = 14.0
CARD_HEADER_H = 25.5
CARD_INNER_GAP = 9.0
RAIL_W = 3.5                # active-card leading rail width
RAIL_INSET = 4.0            # vertical inset so the rail reads as a mark,
                            # not a second border
# A row is two stacked lines: the label/reset/pct line, then a bar that
# gets the card's FULL inner width instead of splitting it with a label
# column (see popover_layout._card_body). ROW_H is COMPUTED as their sum
# (15.5 + 3 + 7.5 = 26) rather than its own literal — it used to be a
# separate number that had to be hand-kept equal to this sum, with a unit
# test as the only thing catching drift; deriving it removes the chance of
# drift entirely. card_height's row-counting arithmetic still needs no
# change of its own beyond these three constants moving.
#
# A three-line row — name, bar, then a "45% used" readout under it — was
# tried and reverted. It read well in isolation and cost more than it was
# worth in place: at four accounts the panel it produced was over 700pt
# tall, so a card that used to be glanceable became something to scroll,
# and the whole point of a menu-bar panel is that it answers a question
# before you have finished opening it. Density IS the feature here.
ROW_LABEL_H = 15.5         # the label/reset/pct line
ROW_LABEL_GAP = 3.0        # gap between that line and the bar under it
BAR_H = 7.5
ROW_H = ROW_LABEL_H + ROW_LABEL_GAP + BAR_H
ROW_GAP = 9.0
STATE_ROW_H = 20.5         # "Re-login required…" line on a data-less card
STATE_LINE_H = 16.5        # extra height per wrapped line (.lineLimit(2))
STATE_MAX_LINES = 2
# Remove-confirm header: "Remove <full label>?" (see popover_layout._card).
# Wider and bolder than a caption, so it gets its own wrap cap and its own
# per-line height rather than reusing STATE_MAX_LINES/STATE_LINE_H.
CONFIRM_MAX_LINES = 2
CONFIRM_LINE_H = 18.5
DOT_R = 4.5                # 9pt circle
DOT_STROKE = 2.0
# MetricBarRow's first line reads left to right as: which window, when it
# refills, how much is spent — only the BAR sits on the line below (see
# popover_layout._card_body). LABEL_W caps the window name's truncation
# width; VALUE_PCT_W is the percentage's right-anchored column, sized from
# real cairo metrics for "100%" with a few points of margin for other
# platforms' default mono fonts.
#
# The countdown used to be a second right-anchored column here, sitting
# directly above the bar's right END. That is what made people read the bar
# as a clock: whatever number is anchored over the end of a bar looks like
# the thing the bar is counting towards. It is now the "resets in …" caption
# beside the LABEL, left-anchored in secondary ink, because the window it
# belongs to is the label — and the percentage is left as the only value
# over the bar, which is correct: it IS the bar's readout.
#
# 46 was sized for "5h"/"7d" only. A real Codex scoped rate-limit name
# ("codex_bengalfox" -> "Bengalfox") is the longest label that actually
# needed the truncation LABEL_W was built for (see 5757ba0's tail/middle
# parity fix). The row has room to spare (its "resets in …" caption already
# renders far short of reset_w), so this fits that name and similar-length
# ones with a little margin rather than truncating text that fits the row.
#
# This is the ONE constant in the table that is sized to a specific STRING
# rather than to its neighbours, so it does not survive a blind snap to the
# nearest 0.5pt — and text_width() is NOT the thing that will judge it.
# That estimator (len x size x factor) is only used to reserve height; the
# painted platforms truncate against real cairo metrics, and CI's
# default sans is WIDER than macOS's, so this value has to be sized for the
# widest renderer rather than the one on the machine making the change.
#
# The bound comes from CI's own history rather than a guess: LABEL_W = 66
# passed there at SIZE_ROW_LABEL = 12, so cairo's "Bengalfox" is at most
# 66pt at 12pt type; advances scale linearly with font size, so at 13pt it
# is at most 66 * 13/12 = 71.5pt. (The 20% pass learned this the hard way:
# it sized LABEL_W from text_width() instead, came out a quarter-point
# short, and CI truncated the name to "Bengalf…" while macOS rendered it in
# full.) 73 clears the 13pt bound with ~1.5pt to spare, which is more
# headroom than the old 66 actually had.
#
# The row can afford it: the label line spends LABEL_W + BAR_GAP + caption
# + BAR_GAP + VALUE_PCT_W out of a 362pt inner width, and even with a full
# "resets in 1h 37m" caption that leaves ~117pt unspent. Re-measure this
# against a Linux run, don't just scale it, if the table moves again.
LABEL_W = 73.0             # MetricBarRow label column
VALUE_PCT_W = 35.0
# BAR_H itself lives above, next to ROW_LABEL_H/ROW_LABEL_GAP — the three
# together are what ROW_H sums.
# Gap after the label column on the label line: it separates the window name
# from the "resets in …" caption that follows it, and is reused as the FLOOR
# on the gap before the right-anchored percentage, so the caption truncates
# rather than colliding with it. The bar on the line below has no such gap —
# it spans the card's full inner width.
BAR_GAP = 11.5             # Spacer(minLength: 11.5) in MetricBarRow's label line
# One "small glyph beside a line of text" size shared by every spot that
# needs it — a blocked account's warn triangle and the footer's pause mark —
# rather than a near-identical pair of constants for what is visually the
# same job twice. Named for the countdown because that is where it started:
# the countdown wore a clock mark until the countdown became words ("resets
# in 1h 37m"), which say what a clock glyph next to them would only repeat.
INLINE_ICON = 11.5
INLINE_ICON_GAP = 4.0
# The pace caret marks "how far through this window are we" independently
# of the fill, which marks "how much is spent" — two different questions a
# single bar cannot answer. It never joins the status ramp, so it cannot
# compete with the fill for the first glance: the fill says "how worried
# should I be", the caret says "am I roughly on schedule for that", and
# giving them related colours would blur both answers into a bar even a
# burn-rate expert can't parse quickly.
#
# It hangs UNDER the bar, flush with its bottom edge. It used to be a notch
# — the card's own ground cut through the bar — which is legible for the
# same reason a hole always is, and which was also the whole problem: a
# fill interrupted at 72% while the readout says 79% reads as a bar that
# ENDS at 72%. People asked what the extra segment meant. Nothing may cut
# the fill, because the fill's end is the one thing on this row that has to
# be unambiguous; a mark below the bar is an annotation pointing AT a
# position instead, and it costs no height — ROW_GAP already leaves 9pt
# between one row's bar and the next row's label.
#
# Being outside the bar, it is now real ink (Scheme.pace) rather than the
# card's ground: a hole in nothing is invisible. It stays a HAIRLINE's
# width, and short — it is a tick under a bar, and anything taller starts
# to read as a second, empty bar of its own.
PACE_W = 2.0
PACE_H = 4.0
BUTTON_H = 23.0
TAB_H = 25.5               # provider tab row (Claude | OpenAI), shown only
                           # when both providers actually have accounts
TAB_GAP = 7.5              # gap between adjacent tab pills
TAB_TOP_GAP = 4.0          # tabs belong to the header, so they sit tighter
                           # under it than the SECTION_GAP between sections
# Each tab pill carries its provider's mark to the LEFT of its label, not
# instead of it: the mark alone is not how most people tell Claude and
# OpenAI's tabs apart, and a label-less pill would stop being readable to
# anyone who doesn't recognise it on sight. TAB_MARK sizes the Glyph;
# TAB_MARK_GAP is the gap before the label starts (see popover_layout.build).
#
# Stacking the mark ABOVE the label at 16pt was tried and reverted with the
# three-line row: a tab row twice this tall is a lot of the panel's height
# spent on a control that is only shown at all when two providers are
# installed.
TAB_MARK = 14.0
TAB_MARK_GAP = 6.5
CHIP_H = 18.5
REMOVE_HIT = 23.0          # square hit target for the per-card remove ✕
REMOVE_ICON = 12.5         # drawn size of the ✕ glyph inside that target
# Gap after a header-row label, e.g. title -> "Updated ..." -> "stale".
# popover_layout derives both offsets from this plus text_width() instead
# of hardcoding an x position (see build()).
HEADER_LABEL_GAP = 7.5

BUTTON_PAD_H = 10.5
FOOTER_H = 25.5
ICON_BUTTON_W = 27.5

# --- System tab (machine vitals + process rows) ----------------------------
# Two shapes carry two different questions, and the split is deliberate:
#
#  * Per-core CPU is a snapshot ACROSS SPACE — one column per core, right now —
#    so it stays a strip of Boxes (an htop meter IS a bar). Each column takes
#    the used-ramp colour of its own value.
#  * The 60-minute CPU and memory histories are one value OVER TIME, so they
#    are Area charts: a filled trend under a stroked top edge, the way
#    Activity Monitor draws a load graph. The fill is a VERTICAL used-ramp
#    gradient (green low, amber, red high), so the curve's height at any
#    minute sits at exactly the colour a bar of that value would be — the
#    Area is the old column strip made continuous, and the panel keeps its
#    one rule: saturation only ever means "how much is spent".
#
# Bars-for-space, a line-for-time is also how the eye tells the two apart at a
# glance without reading a label.
SYS_CORES_H = 22.0         # height of the per-core column strip
SYS_CORE_GAP = 2.0         # gap between core columns
SYS_HIST_H = 34.0          # height of a 60-minute trend chart (CPU and memory)
SYS_AREA_RADIUS = 3.0      # corner radius of a trend chart's track panel
SYS_AREA_LINE = 1.5        # stroke width of the trend's top edge
SYS_AREA_FILL_ALPHA = 0.32  # the fill is a wash under a full-strength edge, so
                            # the ramp reads as a glow and the top line stays
                            # the crisp, unambiguous readout of the value
SYS_ROW_H = 26.0           # one-line process row (Busy: name · meta · ✕)
SYS_ROW_TALL = 38.0        # two-line leftover row: name+meta, then sub below
SYS_MAX_CORES = 32         # cores past this are averaged into columns (model)
SYS_HISTORY = 60           # minutes of history drawn
PROC_KIND_W = 48.0         # width of a row's kind chip column
PROC_MAX_ROWS = 8          # process rows shown per card before "+N more"

# --- type sizes ------------------------------------------------------------
SIZE_TITLE = 16.5          # .headline
SIZE_EMAIL = 15.5          # .callout.weight(.semibold)
# Snapped DOWN, not up, for the same reason LABEL_W is snapped up: this
# size is load-bearing for a specific STRING. The first-run "Current login
# isn't registered…" line has to fit its full-width slot on ONE line
# (tests/test_popover_layout.py::TestFirstRunMessage), and it clears that
# slot by only a few points. 12.65 rounded up to 13.0 overflows and wraps
# it to two — reserving height for a second line that then renders empty.
SIZE_CAPTION = 12.5        # .caption2
# Label and value share one optical size on purpose: they sit on the SAME
# line, and a size step between them would read as a hierarchy that isn't
# there — they are one fact ("5h: 42% · 3h 12m"), not a heading over a body.
# A parity test pins the equality, because it is the relationship the row
# depends on rather than either number on its own.
SIZE_ROW_LABEL = 13.0
SIZE_ROW_VALUE = 13.0      # monospaced
SIZE_CHIP = 11.5
SIZE_ICON = 16.0

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
    rail: tuple
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


# The active card is told apart by a leading rail down its left edge. The
# rail borrows the scheme's own `text` rather than joining the status ramp:
# colour here is reserved for how much budget is left, and tinting "this is
# the active account" would make it compete with that signal instead of just
# marking presence. That reasoning is appearance-independent, which is why
# both schemes below point `rail` at their own `text` — named once each so
# the two cannot drift apart.
_CHALK = (0.914, 0.929, 0.949, 1.0)
_INK = (0.086, 0.106, 0.133, 1.0)
# One cool grey doing two jobs: it is the SECONDARY ink on light and the
# TERTIARY ink on dark. Not a copy-paste — the middle of a single grey
# scale genuinely lands in different places depending on which end the
# ground sits at, and naming it once is what keeps the two ramps a system
# rather than two hand-tuned lists.
_SLATE = (0.361, 0.400, 0.447, 1.0)
_HAZE = (0.545, 0.588, 0.639, 1.0)
# The pace tick is the quietest ink in its scheme — `text_tertiary`, named
# once and used for both, so the two cannot drift. It has to be quiet: it
# annotates the bar, and any louder and it would compete with the fill for
# the first glance (see PACE_W). It has to be INK, though, and not the
# card's ground: it hangs below the bar now, where a ground-coloured mark
# would simply be invisible.

DARK = Scheme(
    name="dark",
    window_bg=(0.059, 0.071, 0.086, 1.0),
    card_bg=(0.090, 0.110, 0.133, 1.0),
    card_border=(1.0, 1.0, 1.0, 0.06),      # hairline, same on every card
    text=_CHALK,
    text_secondary=(0.596, 0.639, 0.690, 1.0),   # "mist"
    text_tertiary=_SLATE,                        # "dim"
    text_spent=(0.725, 0.757, 0.796, 1.0),       # a 100%-used readout
    rail=_CHALK,
    bar_track=(1.0, 1.0, 1.0, 0.09),
    pace=_SLATE,
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
    card_bg=(1.0, 1.0, 1.0, 1.0),
    card_border=(0.0, 0.0, 0.0, 0.07),
    text=_INK,
    text_secondary=_SLATE,
    text_tertiary=_HAZE,
    text_spent=(0.263, 0.294, 0.341, 1.0),
    rail=_INK,
    bar_track=(0.0, 0.0, 0.0, 0.08),
    pace=_HAZE,
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
class Area:
    """A value-over-time trend chart: a rounded track panel, the area under
    the sampled curve filled with a vertical gradient, and the curve's top
    edge stroked in that same gradient.

    `values` are 0..100 (or None), oldest first, mapped evenly left→right so
    the newest sample sits at the right edge — time flows the one way on every
    render. A None is an honest gap: the curve BREAKS there (the run splits)
    and only the track shows, never a line smeared across missing minutes.

    `stops` is the vertical gradient as [(offset, rgba)] with offset 0 at the
    BOTTOM (value 0) and 1 at the top (value 100); the layout builds it from
    the shared used-ramp so both painters glow the same colour at the same
    height. `track` is the empty-panel fill. Colours live on the shape (never
    a painter default) for the same reason Label.color does — the appearance
    is baked in when the layout is built."""
    x: float
    y: float
    w: float
    h: float
    values: list
    stops: list
    track: tuple
    line_width: float = SYS_AREA_LINE
    radius: float = SYS_AREA_RADIUS


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
    max_width: float = 0.0   # 0 = unlimited; else truncated with "…"
    max_lines: int = 1       # >1 word-wraps, like SwiftUI's .lineLimit(2)
    # "tail" is SwiftUI's own default for a plain .lineLimit(1) Text, which
    # covers every truncatable Label except one. "middle" exists only for
    # the account-address line (popover_layout._card), the sole Text in the
    # whole Swift tree that opts into .truncationMode(.middle)
    # (AccountCardView.swift's cardHeader) — see FINDING 1/2 in
    # test_popover_layout.py for why every other label wraps or reserves
    # width instead of falling into this.
    mode: str = "tail"


@dataclass
class Glyph:
    """A symbol the renderer draws from paths.

    SF Symbols have no cross-platform equivalent and a font that happens to
    carry "⟳" cannot be assumed, so every symbol on the panel is drawn
    rather than typeset. `kind` is one of: "refresh", "close", "power",
    "quit" (the header's Quit button — the same power-ring shape as
    "power", under its own name because its Hit is also named "quit"),
    "claude", "openai", "system" (the provider marks beside a tab's label;
    "system" is a pulse line for the System tab), "pause" (the footer's
    "update held") and "warn" (a blocked account's state line).
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
