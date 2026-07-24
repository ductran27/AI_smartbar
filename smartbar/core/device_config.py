"""Per-device settings that survive a self-update.

Every knob in this app is read from the process environment, and every agent
that hosts it — the macOS LaunchAgents, the Linux autostart entry and systemd
units — is written from scratch by the installers. Applying an update IS
re-running those installers, so a variable added to an agent by hand lives
exactly until the next release, and on macOS a GUI app under launchd never
sees the shell environment at all. There was therefore no durable way to
configure a device.

`~/.config/ai-smartbar/config.env` is that place. It sits outside the
checkout and outside every generated unit, the installers fold it into each
agent they write, and because an update re-runs those installers the settings
are re-applied by construction rather than by anyone remembering to.

Pure: parsing and rendering only. Reading the file is the caller's job
(`bin/ai-smartbar --print-config`), which is what keeps this unit-testable.
"""
from __future__ import annotations

import re

FILENAME = "config.env"

#: Only this app's own knobs. A config file that could set PATH, or
#: DYLD_INSERT_LIBRARIES, inside a launchd agent is a privilege problem
#: dressed up as a feature — so anything else is reported and dropped.
KEY_RE = re.compile(r"^SMARTBAR_[A-Z0-9_]+$")

#: Owned by dedicated mechanisms that already exist and are already tested:
#: the installer bakes the checkout it built from into SMARTBAR_REPO_ROOT, and
#: the channel comes from `--channel` plus the installers' read-back of the
#: installed unit. A second source for either key is precisely how two halves
#: of a system end up disagreeing about the same setting.
RESERVED = ("SMARTBAR_REPO_ROOT", "SMARTBAR_UPDATE_CHANNEL")

#: One value charset for three unrelated quoting contexts: plist XML,
#: systemd `Environment="K=V"`, and a `.desktop` `Exec=env "K=V" …` line that
#: may also be re-emitted into a crontab. Excluding the characters that are
#: special in any of them (quote, backslash, backtick, dollar, percent for
#: cron, and every control character) means ONE renderer is safe in all of
#: them, instead of three escaping schemes that each have to be right.
_BAD_VALUE = re.compile(r'[\x00-\x1f\x7f"\\`$%]')

_XML = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def parse(text: str):
    """(settings, problems) — the accepted keys, and why anything was dropped.

    Problems are returned rather than raised: one bad line must not cost a
    device every other setting it had, and the installers surface the list
    so a typo is visible at install time instead of silently doing nothing.
    """
    settings: dict = {}
    problems: list = []
    for number, original in enumerate((text or "").splitlines(), 1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator:
            problems.append("line %d: no '=' in %r" % (number, line))
            continue
        # Quoting a value is the natural thing to write in a .env file, so
        # accept it and unwrap rather than treating the quotes as content.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not KEY_RE.match(key):
            problems.append(
                "line %d: %r is not a SMARTBAR_* setting" % (number, key))
        elif key in RESERVED:
            problems.append(
                "line %d: %s is set by the installer itself" % (number, key))
        elif _BAD_VALUE.search(value):
            problems.append(
                "line %d: %s has a character that cannot be passed safely to "
                "an agent (quote, backslash, backtick, $, %% or a control "
                "character)" % (number, key))
        else:
            settings[key] = value
    return settings, problems


def _escaped(value: str) -> str:
    for raw, entity in _XML:
        value = value.replace(raw, entity)
    return value


# Every line-oriented rendering LEADS with its newline and ends without one.
# That is not cosmetic: the installers splice these in through `$(…)`, and
# command substitution strips trailing newlines — so a trailing-newline style
# silently glues the first following line onto the last rendered one. In a
# plist that merely looks wrong (XML ignores whitespace); in a systemd unit it
# produced `Environment="…"ExecStart=…` and a unit that does nothing. Leading
# newlines make the empty case byte-identical to no splice at all, which is
# what the common "device has no config.env" path needs.
def render_plist(settings: dict, indent: str = "    ") -> str:
    """`<key>…</key><string>…</string>` lines for a LaunchAgent's env dict."""
    return "".join(
        "\n%s<key>%s</key><string>%s</string>" % (indent, key, _escaped(settings[key]))
        for key in sorted(settings))


def render_systemd(settings: dict) -> str:
    """`Environment="K=V"` lines for a systemd unit's [Service] section."""
    return "".join('\nEnvironment="%s=%s"' % (key, settings[key])
                   for key in sorted(settings))


def render_exec_prefix(settings: dict) -> str:
    """`env "K=V" ` to sit in front of a command, or "" when there is nothing.

    Emitting the `env` word here rather than in four installers means one
    place decides what an empty config looks like — and an empty config has
    to leave the command line byte-identical to what it was before.
    """
    if not settings:
        return ""
    return "env " + "".join('"%s=%s" ' % (key, settings[key])
                            for key in sorted(settings))


RENDERERS = {
    "plist": render_plist,
    "systemd": render_systemd,
    "exec": render_exec_prefix,
}


def render(fmt: str, settings: dict) -> str:
    try:
        return RENDERERS[fmt](settings)
    except KeyError:
        raise ValueError("unknown config format %r (want one of %s)"
                         % (fmt, ", ".join(sorted(RENDERERS))))
