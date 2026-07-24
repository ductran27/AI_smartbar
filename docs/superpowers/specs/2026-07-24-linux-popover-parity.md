# Linux UI parity: the same panel, painted instead of themed

Date: 2026-07-24 (v0.4.0). 205 unit tests (8 painter tests skip without
pycairo) + 4 e2e suites green. Reviewed by rendering the real Linux panel to
PNG on the Mac and comparing it against the macOS popover screenshot.

## Problem reported

"The UI in linux is very simple and not same as the Mac one, can we have
same for all OS we are building?"

## Why it was simple — a protocol ceiling, not an omission

The Linux tray is AppIndicator3, i.e. the StatusNotifierItem spec, whose
menu is serialised to the panel's own process over DBus as **dbusmenu**.
dbusmenu carries labels, icons, checkmarks and separators and nothing else.
`Gtk.MenuItem.add(widget)` works in-process and is silently dropped in
transit, so cards, filled bars and the ACTIVE chip **cannot exist inside a
tray menu at any level of effort**. The flat `model.menu_row()` rows were
the ceiling, not a shortcut.

Two consequences shaped the fix:

1. Parity requires a real window that the tray opens.
2. StatusNotifier exposes no left-click callback — left-click always opens
   the menu. So the panel opens from a menu row plus
   `set_secondary_activate_target` (middle-click). `Gtk.StatusIcon` would
   allow single-click but is deprecated and dead on GNOME/Wayland; the user
   runs both XFCE/X11 and GNOME, so the session-agnostic route won.

## Painted, not built from widgets

The panel is one `Gtk.DrawingArea` painted with cairo rather than GTK
widgets + CSS. The deciding factor was verifiability: there is no Linux box
in this loop, and the complaint is specifically visual, so "matches the Mac"
had to be something demonstrable rather than asserted. pycairo installs
cleanly on macOS against Homebrew cairo, so the exact production drawing
code renders to PNG on the development machine —
`ai-smartbar --preview-popover [--demo]`, which never imports gi.

Secondary gains: nothing is themed, so XFCE/GNOME/KDE all get the same
pixels; and layout becomes a pure function, which makes hit-testing — the
part of a hand-drawn UI most likely to rot — unit-testable.

Costs, stated plainly: no native keyboard navigation or screen-reader
support inside the panel (the tray menu stays native), and cards are a solid
dark fill because cairo has no portable equivalent of macOS `.thinMaterial`.

## Structure

| File | Role | Imports |
|---|---|---|
| `core/popover_theme.py` | palette, metrics, primitives (Box/Dot/Label/Glyph/Hit) | nothing graphical |
| `core/popover_layout.py` | pure `build()` → positioned shapes + named hit rects | nothing graphical |
| `linux/popover_draw.py` | cairo painter, middle-truncation, 2-line wrap | cairo only |
| `linux/tray_icon.py` | the twin-pill badge, moved out of tray.py | cairo only |
| `linux/popover_window.py` | Gtk window, hit-tested input, placement, 30s tick | gi |
| `linux/tray.py` | indicator + launcher menu + fallback text rows | gi |

Values mirror PopoverView / AccountCardView / MetricBarRow 1:1. Two icons
(refresh, power) are stroked from paths because SF Symbols have no portable
twin and no font can be assumed to carry "⟳"/"⏻".

Layout estimates text widths from per-font advance factors so it stays free
of a font engine; the renderer then measures for real and centres labels
inside the boxes it was handed, which keeps a wrong estimate cosmetic rather
than misaligning a click target.

## Decisions worth remembering

- **Never fatal.** `_make_popover()` catches everything; if the window can't
  be created the menu falls back to the old text rows, which matters on a
  headless or misconfigured session.
- **Wayland placement is left alone.** Clients cannot position themselves —
  there are no global coordinates — so `_position()` no-ops off X11 rather
  than faking it.
- **Hover rebuilds the whole layout.** It is a pure function over ~50
  shapes; caching would buy nothing and could desync.
- **The rumps macOS fallback is deliberately untouched** (user's call): it
  serves Macs that cannot build the Swift app, has never been live-verified,
  and NSMenu custom views would be an equally unverifiable surface.

## Tests

`tests/test_popover_layout.py` (28) covers geometry and click targets: card
height per row count, bar fill proportion and clamping, purple at 100%,
hollow dot for dataless accounts, blocked switches having a hit rect that
refuses the click, no overlapping targets, nothing drawn outside the panel.
`tests/test_popover_draw.py` (8, skipped without pycairo) is the painter
smoke test across every state including hover, errors and a 120-character
email.

## Known limits

- Still not run on a real Linux desktop. The drawing is verified by
  rendering; the GTK hosting (window flags, focus-out, middle-click target,
  systemd tray behaviour) is written to spec and unverified.
- `tray.py` remains ~360 lines, over the repo's 200-line guideline. The
  badge renderer was extracted; splitting the remaining indicator/fetch/glue
  further would be artificial.
