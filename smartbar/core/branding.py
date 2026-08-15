"""What this application is called and what it looks like — defined once.

The name and the logo are consumed by things that have no other reason to
know about each other: a notification from either headless agent, a Linux
autostart entry, a Windows Start-up shortcut, a macOS bundle. Spelling them
out in each of those is how they drift.

Deliberately no rendering here and no cairo import. The logo is a committed
asset (see smartbar/paint/app_icon.py, which generates it); installers must
be able to place it long before this project's Python dependencies exist —
Windows needs the icon for a shortcut it creates around the same time it is
still building the venv, and macOS never pip-installs anything at all.

macOS notifications cannot use any of this, and that is not an oversight.
There a notification is credited to the BUNDLE of the posting process; a
launchd Python agent has no bundle, so osascript is the only mechanism
available and the system credits Script Editor. The app bundle cannot lend
its identity out either — see the measurements in UsageStore.swift's
`notify`. That one is bought with a Developer ID signature, not with code.
The macOS APP icon is unaffected and does use this asset; only the
notification sender is stuck.
"""
from __future__ import annotations

import os

APP_NAME = "AI smartbar"

# The freedesktop icon-theme NAME (no directory, no extension), which is what
# install/linux.sh installs the asset under and what `notify-send -i` and a
# .desktop `Icon=` both want. A theme name survives the asset moving; an
# absolute path into the checkout does not.
ICON_NAME = "ai-smartbar"


def icon_path() -> str:
    """Absolute path to the committed logo, or "" if it is not there.

    "" rather than a guess, because the callers must be able to tell: an
    icon flag pointing at a file that does not exist is worse than no icon
    flag at all — Windows raises out of the balloon entirely, and notify-send
    warns on stderr for every notification.

    This is the in-checkout asset, used where a theme lookup is not available
    (Windows) or where the install step that publishes the icon may not have
    run yet.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    icon = os.path.join(repo, "assets", "ai-smartbar.png")
    return icon if os.path.isfile(icon) else ""
