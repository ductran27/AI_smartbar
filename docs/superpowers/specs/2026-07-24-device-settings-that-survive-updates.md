# Per-device settings that survive an update

**Shipped** 2026-07-24, alongside platform tags in presence labels.

## The problem

Every knob this app has is read from the process environment, and there was no
durable way to set one.

- On macOS a GUI app started by launchd inherits **no shell environment at
  all**, so exporting `SMARTBAR_INTERVAL` in a profile did nothing.
- Every agent that hosts the app — `com.ductran.ai-smartbar.plist`, the warmup
  and update agents, the Linux `autostart` entry and systemd units — is written
  **from scratch** by the installers.
- Applying an update *is* re-running those installers. So a variable added to
  an agent by hand survived exactly until the next release, silently.

The Linux autostart entry carried no environment whatsoever, and the macOS app
plist carried only `SMARTBAR_REPO_ROOT`. Only the update channel was preserved,
by a dedicated read-back of the installed unit.

## The shape

`~/.config/ai-smartbar/config.env`, `KEY=VALUE` per line. It sits outside the
checkout **and** outside every generated unit, and each installer folds it into
the agent it writes. Because an update re-runs the installers, the settings are
re-applied *by construction* rather than by anyone remembering to.

Nothing in the app changed. The app still reads its environment exactly as
before, which is what kept this to one core module plus four splices.

Alternative considered and rejected: read back and preserve whatever is already
in the agent. Smaller, but macOS-only (Linux's `.desktop` had no environment to
preserve), leaves the user editing XML, gives no discoverable place to look,
and a key once set can never be removed.

## Rules, and why

- **Only `^SMARTBAR_[A-Z0-9_]+$`.** These land in a launchd agent's
  environment; a config file that could set `PATH` or `DYLD_INSERT_LIBRARIES`
  is a privilege problem wearing a feature's clothes.
- **`SMARTBAR_REPO_ROOT` and `SMARTBAR_UPDATE_CHANNEL` are reserved.** Both
  already have a mechanism that is tested (`--channel` plus the unit read-back;
  the checkout the installer built from). A second source for one key is how
  two halves of a system come to disagree — the exact failure mode that cost a
  day elsewhere in this repo.
- **One value charset for three quoting contexts.** Values reject `"`, `\`,
  `` ` ``, `$`, `%` and control characters, so a *single* renderer is safe in
  plist XML, in `Environment="K=V"`, and in a `.desktop` `Exec=env …` line that
  may be re-emitted into a crontab (`%` is a newline there). Three escaping
  schemes that each have to be right is three chances to emit a unit that will
  not load, on a device nobody is looking at.
- **Problems are returned, not raised.** One typo must not cost a device every
  other setting, and the installers print the list so it is visible at install
  time instead of silently doing nothing.
- **Never fatal.** A device with an unreadable config still gets a working
  agent. Losing a setting is a papercut; failing to install is not.

Line breaks need no filtering: the format is line-based with no continuation
syntax, so a value simply ends at the line end and the remainder becomes an
ordinary bad line. That is what makes multi-line injection unrepresentable
rather than merely filtered.

## Two bugs the e2e found, neither by reasoning

**Leading newlines are load-bearing.** The installers splice through `$(…)`,
and **command substitution strips trailing newlines**. A trailing-newline
rendering therefore glued the next line of the unit onto the last rendered one:
in a plist that merely looked wrong (XML ignores whitespace, so `plutil -lint`
passed), but in a systemd unit it produced `Environment="…"ExecStart=…` — a
unit that does nothing. Every line-oriented renderer now *leads* with its
newline and ends without one, which also makes the empty case byte-identical to
no splice at all. That empty case is the common one: most devices have no
config file.

**`install/linux.sh` could not complete a fresh install.** Under
`set -euo pipefail`, its channel read-back ran `sed` on a unit file that does
not exist yet; `sed` exits non-zero, `pipefail` propagates it out of the
assignment, and the installer died *before* the symlink, the autostart entry,
anything. macOS had `|| true` on its PlistBuddy read-back; Linux did not. Fixed
on both read-backs (`sed` and `crontab -l`). Pre-existing, unrelated to this
feature, and only ever reachable on a device with no updater unit — which is
precisely a brand-new one.

## Tests

`tests/test_device_config.py` (21) covers parsing and, mostly, what is
*refused*. The output is checked by handing it to real parsers rather than by
matching strings: `plutil -lint` plus a JSON round-trip proves the XML escaping,
and `shlex.split` proves each assignment reaches `env` as exactly one argument
(a value with a space is the sharp case — otherwise `env` treats the tail of a
path as the command).

`tests/e2e-config.sh` runs the **real installers** and reads back the files they
wrote, because a splice point in the wrong place is invisible to unit tests. It
is contained by `PATH` order, not by trust: `launchctl`, `systemctl`, `crontab`,
`pkill`, `setsid`, `nohup`, `cswap`, `claude` and `pgrep` are shadowed by no-op
stubs and `HOME` points into a temporary directory, so it cannot touch real
agents, the crontab or running processes. Scenarios: settings reach each agent /
the reserved channel stays authoritative / Linux's two files both carry them and
`Exec` splits into a sane argv / no config leaves the units clean and
byte-identical across two runs / non-`SMARTBAR_` keys cannot reach an agent.

Added to `install/release.sh`'s always-run gate: these files ship to every
device at once, and a malformed unit is not a degraded feature but a device that
stops running the app.

## Platform tags in presence labels

`--presence-status` now names devices `mac-<host>` / `linux-<host>`. A beacon
otherwise says nothing about what a machine *is*, and `sanitize_label` drops
the domain that might have hinted at it — so "which of my devices is that, and
is my Linux box even in the loop?" could not be answered from the count.

The platform goes in the **label**, not a new ref component. The label is
display-only (identity is the device id), so every existing `p1` reader accepts
a prefixed label unchanged. Adding a component would change the ref *shape*,
and an older device's decoder rejects a shape it does not know: it would quietly
stop counting every upgraded device until it upgraded too — the exact undercount
presence exists to prevent. Truncation eats the hostname, not the prefix.
`SMARTBAR_PRESENCE_LABEL` still replaces the whole name.
