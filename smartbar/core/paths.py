"""Where this app's cache and config files live, one place for every platform.

Five modules each grew their own copy of the same two expressions —
`presence_runner.py`, `warmup_runner.py`, `update_runner.py`,
`linux/tray.py` and `paint/popover_preview.py` — and one of them
(`linux/tray.py`) quietly dropped the `SMARTBAR_CACHE_DIR` override while
copying it, which is exactly the kind of drift that happens when a path
expression is pasted five times instead of named once. Phase 1 of the
Windows port needs a sixth caller (something that runs under `win32`,
where there is no `~/.cache`), which makes six copies one too many.

The two directories are kept separate on purpose, matching the XDG split
this app already followed on Linux: CACHE_DIR holds state that is fine to
lose (lock files, logs, the presence snapshot, the popover preview PNG)
and CONFIG_DIR holds the device identity and per-device settings that
should survive a `rm -rf` of the cache. On Windows that maps onto the same
split the OS itself uses — `%LOCALAPPDATA%` is not roamed or backed up,
`%APPDATA%` is — so the two stay distinct there too instead of collapsing
into one folder.

`SMARTBAR_CACHE_DIR` / `SMARTBAR_CONFIG_DIR` win on every platform (tests
and `tests/e2e-warmup.sh` depend on this), and an override of `""` falls
through to the platform default rather than resolving to the empty
string — the `or os.path.expanduser(...)` shape the three honest copies
already used, preserved here rather than upgraded to `is None` semantics
that would silently change behaviour for anyone who has `SMARTBAR_CACHE_DIR=`
set in an agent's environment today.
"""

from __future__ import annotations

import os
import sys


def cache_dir() -> str:
    """SMARTBAR_CACHE_DIR, else %LOCALAPPDATA%\\ai-smartbar or ~/.cache/ai-smartbar.

    win32 falls back further to the POSIX-shaped path if `%LOCALAPPDATA%`
    is itself unset, which happens on some minimal service accounts and
    in CI containers — better a directory under the (always-present)
    home drive than a crash before the app can even log why.
    """
    override = os.environ.get("SMARTBAR_CACHE_DIR")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return os.path.join(base, "ai-smartbar")
    return os.path.expanduser("~/.cache/ai-smartbar")


def tray_pid_file() -> str:
    """cache_dir()/tray.pid — where the Linux tray writes its own PID.

    `ai-smartbar --open-panel` (bin/ai-smartbar) reads this file to find a
    running instance to signal (see smartbar/linux/tray.py's SIGUSR1
    handler). Lives under CACHE_DIR rather than CONFIG_DIR: it is
    disposable, rewritten fresh on every startup, and nothing should ever
    treat a stale copy left behind by a crashed process as durable state.
    """
    return os.path.join(cache_dir(), "tray.pid")


def config_dir() -> str:
    """SMARTBAR_CONFIG_DIR, else %APPDATA%\\ai-smartbar or ~/.config/ai-smartbar.

    Same fallback reasoning as `cache_dir`: if `%APPDATA%` is unset the
    POSIX-shaped path under the home drive is still a directory this
    process can create and write to.
    """
    override = os.environ.get("SMARTBAR_CONFIG_DIR")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return os.path.join(base, "ai-smartbar")
    return os.path.expanduser("~/.config/ai-smartbar")
