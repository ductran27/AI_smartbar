# Open-panel hotkey: one feature, three genuinely different answers

Date: 2026-08-16. 1017+ unit tests green (`python3 -m unittest discover -s
tests`) plus the new tests this feature adds. `swift build` compiles clean.
Reviewed by reading each platform's tray host in full before touching it
(smartbar/windows/tray.py's marshaling discipline, smartbar/linux/tray.py's
GLib main loop, AISmartbarApp.swift's MenuBarExtra) rather than assuming one
platform's shape would transfer to the other two.

## Problem

Open the AI smartbar panel from a keyboard shortcut, without clicking the
tray/menu-bar icon first. Asked once, but it is not one problem — macOS,
Windows and Linux each expose a different (or no) system-wide hotkey
primitive, and SwiftUI's `MenuBarExtra` in particular has no public API to
open its own window at all. This doc exists because the honest answer is
"three different mechanisms, one of them a workaround with no public API,
one of them not really a hotkey at the OS level" — not because any one of
them was hard to write.

## What ships, per platform

| Platform | Mechanism | Verified how |
|---|---|---|
| macOS | `NSEvent.addGlobalMonitorForEvents(.keyDown)` in `AppDelegate`, ⌃⌥A, opens the panel via `NSStatusItem.button.performClick(nil)` | `swift build` compiles; source-scrape parity tests pin the wiring. **Not** run in a live, permission-granted interactive session — see [Known limits](#known-limits). |
| Windows | `ctypes` + `user32.RegisterHotKey` on a dedicated message-loop thread, Ctrl+Alt+A, routes into the existing `_on_open` marshal | Mocked unit tests for the testable seam; the real `RegisterHotKey`/`GetMessageW` loop cannot run off real Win32 at all — added to `docs/windows-bring-up.md`'s manual checklist (item 13), same as every other Windows-only behavior in this port. |
| Linux | `ai-smartbar --open-panel` signals an already-running tray over `SIGUSR1` (PID file); no in-process hotkey — the user binds the command in their own DE | Unit-tested (PID file round-trip, signal-handler delegation, CLI subprocess contract). The signal-delivery path genuinely cannot be exercised without a live Linux desktop; see [Known limits](#known-limits). |

Three different shapes, on purpose: forcing one design onto all three would
have meant either a Linux dependency this repo doesn't carry (a hotkey
daemon, or a toolkit with system-wide-shortcut support neither GTK nor
AppIndicator gives for free) or pretending Windows and Linux could share
macOS's very platform-specific `NSStatusItem` trick.

## macOS: the part with no public API at all

`NSEvent.addGlobalMonitorForEvents(matching:handler:)` is public and does
exactly what a global key monitor should — but the handler firing is only
half the feature. The other half, opening `MenuBarExtra`'s popover
programmatically, has **no public API surface whatsoever**. SwiftUI hands
the developer a `Scene`, not a window, a status item, or any handle that
would let code say "open."

Three approaches were weighed:

1. **KVC into the private window's `statusItem` property** (what shipped).
   SwiftUI wraps `MenuBarExtra(.window)`'s content in a private `NSWindow`
   subclass that happens to carry its owning `NSStatusItem` under the
   `"statusItem"` key, reachable via `value(forKey:)`. A zero-size
   `NSViewRepresentable` planted in the popover content grabs a live
   `NSWindow` reference the moment SwiftUI attaches one, and the hotkey
   handler calls `statusItem.button?.performClick(nil)` — simulating the
   exact click that already opens the popover. **Chosen**: least code,
   reuses the real open path (no parallel "make the window visible" logic
   that could drift from what a click does), and degrades safely — a
   renamed/missing key returns `nil` through the `as?` cast, not a crash.
   **Risk, stated plainly**: this is undocumented behavior of a private
   window class. It is not guaranteed to survive a macOS or Swift version
   bump. If it breaks, the failure mode is exactly the scoped-down outcome
   the feature request explicitly allowed: the hotkey fires and is logged,
   nothing visibly opens.
2. **Walk `NSApp.windows` for one whose class name contains
   "MenuBarExtraWindow"** and call `makeKeyAndOrderFront`/toggle visibility
   directly. Rejected: still private-API-shaped, and bypassing the status
   item's own click handling risks a window that opens without the
   positioning/focus-behind-icon logic AppKit already does correctly for a
   real click — more code, no more stability than approach 1.
3. **A parallel, hand-built popover window** shown by the hotkey instead of
   `MenuBarExtra`'s own. Rejected outright: two windows for one feature,
   and the whole point of `MenuBarExtra(.window)` was reusing the same
   `PopoverView` everywhere.

**Permission handling.** `NSEvent`'s global key monitor only ever invokes
its handler once macOS has granted the app Accessibility trust (surfaced as
Input Monitoring on some macOS versions) — there is no separate
request/callback the way there is for e.g. location. Without it the handler
is registered and simply never called; no exception, no crash, nothing
observable except "the hotkey does nothing." `AXIsProcessTrusted()` is
checked once at launch purely for diagnosis (one `NSLog` line either way),
not to gate registration — a permission granted from System Settings
*after* launch starts working immediately, since macOS re-checks trust per
event rather than once at registration time, so gating registration on the
launch-time check would have made a legitimately-granted permission require
a relaunch it does not actually need.

**Why ⌃⌥A.** Control+Option is a modifier pair almost nothing in macOS's
own shortcuts or common third-party apps' default bindings claims —
compare bare ⌥ (dozens of system shortcuts), ⌘ (nearly everything), ⇧⌘
(most "alternate" actions). Matched by physical key code (`kVK_ANSI_A =
0x00`), not by the character a layout produces, the same way every
system-wide-hotkey library keys off a virtual key code.

## Windows: reusing the tray's own marshaling discipline

`smartbar/windows/tray.py` already runs two threads with a documented
marshaling contract (`call_on_ui_thread` == `root.after_idle`, see that
method's own docstring on Decision D1 and the shutdown race it guards
against). `RegisterHotKey` needs a **third**: `WM_HOTKEY` is delivered only
to the thread that registered the hotkey, and only while that thread is
pumping messages with `GetMessageW` — neither the tk thread (owns its own
loop via `root.mainloop()`) nor the pystray worker thread (owns `Icon.run()`'s
own loop) is free for this.

The dedicated thread's `WM_HOTKEY` handler calls `tray._on_open()` directly
— the exact same method `_menu_open` already reaches from the pystray
worker thread (itself not the tk thread). `_on_open` already marshals its
own popover touch through `call_on_ui_thread` internally (see its own
docstring), so calling it from a third non-tk thread is reusing the
existing marshal, not adding a new one — the instruction this feature was
built against ("trigger that exact same open action through whatever
marshaling mechanism this file already uses ... do not bypass it") is
satisfied by delegation, not by re-deriving the marshal from scratch.

`RegisterHotKey` failing outright (`0` return — most likely another app
already owns Ctrl+Alt+A) is logged and the thread simply exits; unlike
`_run_icon`'s failure path, it does **not** call `tray._quit()` — losing
the hotkey is a degraded feature (the tray icon itself is unaffected), not
grounds to take the whole tray down.

## Linux: no dependency-free system-wide hotkey exists

Checked first, per this feature's own instructions: `grep -rn
"signal.signal\|SIGUSR\|single.instance\|pidfile"` across `smartbar/` and
`bin/` found no existing signal-handling or single-instance-IPC
precedent — the one hit (`presence_client.py`'s `SIGCHLD` comment) is
prose, not code. Nothing to build on top of; this feature is what
introduces both.

GNOME, KDE, XFCE and friends each bind global keyboard shortcuts through
their own, mutually incompatible mechanism (GSettings schemas, KGlobalAccel,
etc.), and none of them expose a portable library-level API a Python GTK
app can register against without a new dependency this repo doesn't carry.
Building a "real" cross-desktop hotkey would mean picking one desktop's
mechanism and silently not working on the others, or adding a dependency
(e.g. a keyboard-grab library) to solve a problem the desktop environments
themselves already solve for every other app on the system.

So the honestly-scoped version: `ai-smartbar --open-panel` (`bin/
ai-smartbar`) is the *building block* a keyboard shortcut runs, not the
shortcut itself.

- `smartbar/linux/tray.py`'s `main()` writes this process's PID to
  `smartbar.core.paths.tray_pid_file()` (`~/.cache/ai-smartbar/tray.pid`)
  on startup, and removes it on a clean quit.
- It registers `SIGUSR1` via `GLib.unix_signal_add` — **not** a raw
  `signal.signal()` handler. `unix_signal_add` integrates the signal into
  the same GLib main loop everything else in this file already runs on
  (self-pipe under the hood), so the handler can touch GTK/`self.popover`
  directly, exactly like every other callback in this file. A bare
  `signal.signal` handler runs between bytecode instructions on whatever
  thread owns the signal and would have to somehow wake the GTK loop
  itself — GLib already solves this, so there is no reason to reinvent it.
- `bin/ai-smartbar --open-panel` reads the PID file and sends `SIGUSR1`,
  refusing cleanly (named path, distinct messages) when no PID file exists,
  the file is garbage, or the named PID isn't running (a crash that skipped
  cleanup). It refuses outright on macOS/Windows, which each have a real
  in-process hotkey instead and no use for this command.
- The actual key binding is the user's own DE keyboard settings, documented
  in the README's [Linux panel](../../README.md#the-linux-panel) section
  with concrete GNOME/KDE steps.

## What each platform's tests actually pin

- **macOS**: source-scrape (no Swift toolchain in CI) — the monitor is
  installed from `applicationDidFinishLaunching` (not merely defined),
  `AXIsProcessTrusted()` is consulted with a log line for both outcomes,
  the button lookup is optional (`?.`) rather than force-unwrapped, and the
  key code is the documented `kVK_ANSI_A` value. What this does **not**
  prove: that `performClick` actually opens the window on a real Mac, or
  that the `"statusItem"` KVC key still resolves on whatever macOS version
  a reader is running — see below.
- **Windows**: `_on_hotkey_message(tray)` (the seam between the
  unreachable-in-tests `RegisterHotKey`/`GetMessageW` loop and the rest of
  the tray) is pinned to call `tray._on_open()`, and the Win32 constants
  handed to `RegisterHotKey` (`MOD_ALT`, `MOD_CONTROL`, `MOD_NOREPEAT`,
  `VK_A`, `WM_HOTKEY`) are pinned against their documented values so a typo
  cannot silently register the wrong combination. The loop itself cannot
  run in this suite — `ctypes.windll` does not exist off win32 at all,
  unlike this file's tkinter/PIL/pystray fakes.
- **Linux**: `_write_pid_file`/`_remove_pid_file` round-trip a real
  temp-directory PID file and degrade to a logged, non-raising failure on
  an unwritable path; `_on_open_panel_signal` delegates to the exact same
  `_on_open` a click reaches and always returns `True` (a GLib source
  callback returning falsy is *removed*, so this is what keeps the next
  press working); `Tray._quit` is pinned to call `_remove_pid_file`.
  `bin/ai-smartbar --open-panel`'s CLI contract (help text, "no running
  tray" with the checked path, a dead-PID's number surfacing in the
  message, macOS/Windows refusal) is pinned via subprocess, following
  `tests/test_device_config.py`'s existing pattern for this launcher.

## Known limits

Stated as plainly as `docs/windows-bring-up.md` states its own gaps —
this is written-to-spec, unit-tested-with-mocks work, not live-verified:

- **macOS**: never run in a real, Accessibility-granted interactive
  session. `swift build` proves it compiles; the source-scrape tests prove
  the wiring is present and shaped correctly. Whether `performClick(nil)`
  actually opens the window, and whether the `"statusItem"` KVC key still
  resolves on the macOS version a reader is running, are both unverified.
  If the KVC lookup ever silently starts returning `nil` (an OS update
  changes the private class), the visible symptom is "the hotkey does
  nothing" with an `NSLog` line explaining exactly that — not a crash.
- **Windows**: per `docs/windows-bring-up.md`'s own framing, this repo has
  never run `smartbar/windows/` on real hardware at all. This feature adds
  one more manual checklist item (13) to that existing gap rather than
  closing it.
- **Linux**: no live GTK/GLib main loop in this development sandbox either
  (the whole tray is tested via faked `gi`/`GLib`/`AppIndicator3` modules,
  same as every other Linux tray test in this repo). The PID-file and
  signal-*handler* logic is unit-tested; POSIX signal *delivery* between
  two real processes, and `GLib.unix_signal_add`'s actual self-pipe
  integration, are not exercised by anything in `tests/`.
- **Linux, by design, not by gap**: there is no true system-wide hotkey —
  a user who does not bind the shortcut in their DE gets no hotkey at all,
  which is the honestly-scoped outcome this doc's own "no dependency-free
  option exists" section explains.
