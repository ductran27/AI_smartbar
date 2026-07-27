"""Toolkit-free cairo painting, shared by every platform's front-end.

Nothing in this package imports gi, rumps, pystray or tkinter — only cairo
and smartbar.core. That was already true of these modules while they lived
under smartbar/linux/ (popover_draw.py's docstring called it out as a
deliberate choice), but the directory said otherwise, and a Windows tray
importing `smartbar.linux.popover_draw` would have been absurd. Moving them
here makes the seam the layout is built on visible in the tree: core/
decides WHAT to draw (popover_layout positions primitives and names hit
rects), paint/ turns that into pixels, and each platform package owns only
its windowing and event loop.

The payoff is that all three platforms render from one painter, so the panel
cannot drift between them the way the model layer already has between Python
and macos-swift.
"""
