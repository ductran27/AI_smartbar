# Device presence: counting the machines on an account, without a server

Date: 2026-07-24 (v0.6.0). 300 unit tests (8 painter tests skip without
pycairo) + 5 e2e suites green. Verified live against the real private
remote: publish → replace → withdraw → re-join.

## Ask

> "if user using many devices so tracking how many devices using that
> account would be useful too, which we can format in the bar like:
> syu3cs@virginia.edu (1)"

Chosen semantics (user's call): **N = devices that have this account as
their live cswap slot**, i.e. "currently using". A nice property falls out
— exactly one account is active per device, so the badges across all cards
sum to the number of live devices, which makes a wrong number checkable.

## Why a git ref, and not the obvious alternatives

There is no server, so the first question was what channel these devices
already share:

| Candidate | Why not |
|---|---|
| Anthropic's API | Exposes usage windows, not sessions or devices. Nothing to read. |
| LAN discovery (mDNS/UDP) | Only sees the same network. A laptop elsewhere vanishes — wrong exactly when the answer matters. |
| Cloud drive (iCloud/Dropbox) | Not present on Linux by assumption; a new dependency for one integer. |
| Hosted service / Gist token | A new secret to distribute to every device, and new infrastructure to own. |
| **The repo itself** | Every device already proves `git ls-remote` works without a prompt before `install/linux.sh` or `install/macos-update.sh` will install an updater at all. |

The last row is not a trick: a working non-interactive git credential is
already a hard precondition on every device that self-updates.

Given the repo, three shapes were possible. Committing to a branch races
(every device pushes the same ref) and grows history forever. A ref per
device pointing at a commit whose blob holds the payload works, but mints
three loose objects per device per beat — roughly 400 a day accumulating
in a source repo. So: **put the payload in the ref NAME**, and point it at
a sha the remote already has.

```
refs/smartbar/p1/<device>/<label>/<epoch>/<sha256(email)[:16]>
```

- the push transfers **zero objects**; `git gc` never has anything to do
- reading is one `git ls-remote` — no fetch, nothing written locally
- ref names are disjoint per device, so two devices cannot conflict
- outside `refs/heads` and `refs/tags`: invisible in GitHub's UI, not
  fetched by a clone, untouched by `fetch --tags --prune`, and invisible
  to `install/release.sh`'s clean+main+synced gate
- addresses never leave the machine, only a hash

**Verified before any of it was written**, because the whole design rests
on it: GitHub accepts a custom `refs/smartbar/*` namespace, an atomic
create+delete in one push, and a delete. (It does. A first probe failed
misleadingly — zsh applies history modifiers to `$SHA:refs/...`, eating
the `:r`. Brace the variable.)

The sha comes from the same `ls-remote`, never from local HEAD: on a
checkout with unpushed commits, pushing local HEAD would quietly upload
that private work as a hidden ref.

## Where the work runs

`bin/ai-smartbar --presence-beat` does everything and writes a small JSON
file; every UI reads that file on its normal poll. This is exactly the
arrangement `update_runner.py` already uses for the update badge, and it
is what keeps the Swift side a **dictionary lookup instead of a second
implementation** of ls-remote, atomic ref replacement and clock-skew
rules. The state file is keyed by plain address (it never leaves the
machine), so Swift never hashes anything.

The beat takes the account list on **stdin** from the UI that has just
fetched, so a heartbeat costs one ls-remote and one push and **no
usage-API traffic at all**.

| File | Role |
|---|---|
| `core/presence.py` | pure policy: hashing, ref encode/decode, liveness, dedupe, counts |
| `presence_git.py` | ls-remote, atomic replace, sweep, withdraw |
| `presence_runner.py` | one beat: lock, read, count, publish, save; `--presence-status` |
| `presence_client.py` | the UI side: spawn a beat, read the counts |
| `PresenceStatus.swift` | the same reader/spawner for the Swift app |

## Accuracy, failure by failure

The count is worse than useless if it is quietly wrong, so each way it can
lie has a named answer:

- **A device that died with its ref parked** — 15-minute TTL (three missed
  beats), plus `--presence-leave` on a deliberate quit for the instant case.
- **Clock skew.** A machine whose clock is slow publishes an epoch that
  looks already-expired and would silently vanish — an undercount, the
  failure hardest to notice. So the reader also tracks, on *its own*
  clock, when each device's ref last **changed**; a ref seen moving is
  alive whatever its epoch claims. A clock more than a day ahead is not
  believed at all, or a wrecked RTC would count forever.
- **First sight is not proof of life.** The rescue above only counts a ref
  that changed *between two of our reads*. Stamping first sight — which
  the first draft did — resurrects every abandoned device for a full
  window after any fresh install. `tests/e2e-presence.sh` scenario E
  caught it.
- **A leaked duplicate ref** (a half-landed push) — collapse by device,
  newest epoch wins, so a leak is cosmetic rather than a double count.
- **Two clones on one machine** — the device id lives in `~/.config`, not
  the checkout and not the cache: one machine is one device, and clearing
  caches does not mint a new identity.
- **A read-only credential** — we always count ourselves from the live
  snapshot and ignore our own beacon, so a device that can read but not
  push still sees everyone, and never waits for its own ref to round-trip.
- **An unreachable remote** — the last good answer stands while it is
  inside the window, then every badge disappears. Rendering `(1)` from
  local knowledge alone would assert "only this device" at precisely the
  moment we cannot see the others.
- **Only devices running AI smartbar are visible.** Unfixable — there is
  no Anthropic API for sessions — so it is stated in the README and the
  macOS tooltip rather than papered over.

## Decisions worth remembering

- **Publish and sweep are separate pushes.** The replace is atomic so the
  namespace never shows a device twice or not at all; deleting month-dead
  litter is best-effort and non-atomic, because losing that race must
  never cost a device its own heartbeat.
- **No withdrawal on SIGTERM.** The updater stops and restarts the UI, and
  a withdrawal racing the new instance's first beat could delete the ref
  it had just published. Explicit quit only; a killed process ages out.
- **`(0)` is never drawn.** An absent badge reads as "nobody is on it",
  and cannot be mistaken for a measured zero when the truth is "we could
  not see anyone".
- **Appended, not prefixed.** Both the cairo painter and SwiftUI truncate
  a long address in the MIDDLE, so `(2)` survives on a card too narrow to
  show the address itself.

## Tests

`tests/test_presence.py` (54) covers the policy, including every failure
above, and asks **git itself** — `git check-ref-format` — to validate the
refs generated from adversarial hostnames rather than assuming the rules.
`tests/e2e-presence.sh` runs two real clones against a real bare origin
through ten scenarios: publish, a second device making it 2, replacement
without accumulation, a dead ref ageing out, withdrawal, a push-rejecting
remote, an unreachable remote, the kill switch, and assertions that beats
transfer **zero objects** and create no branch or tag.

A second physical device is the one thing this project cannot test — but
at the ref level a second clone with its own device id *is* a second
device, which is why the e2e drives two.

## Known limits

- Not yet seen on a real second device. The mechanism is verified live
  against the real remote from this Mac, and two-device behaviour is
  verified against a local origin; the Linux box joining is still ahead.
- Ref epochs have one-second resolution, so two beats in the same second
  push an identical refspec and git answers "everything up-to-date". Real
  beats are five minutes apart; only the e2e had to be made deterministic
  about it.
