# Windows bring-up checklist

`smartbar/windows/` (tray + panel) has never run on a real Windows machine.
It was written to spec against `docs/superpowers/specs/` and unit-tested with
pystray, tkinter and pycairo faked (see `tests/test_windows_tray.py` and
friends) — the logic underneath the GUI is exercised, the GUI itself is not.
None of that is unit-testable, so this is the manual pass that stands in for
it. Run every item below on a real Windows box before telling anyone this
port works. If you find a failure, fix it and re-run the *whole* list, not
just the item that failed — several of these interact (see
[Known-unresolved issues](#known-unresolved-issues)).

Prerequisite: install first — `.\install\windows.ps1` — per the main
[README](../README.md#install).

## Before you start: get a console

The installer wires the Startup shortcut to `pythonw.exe`, which has no
console — a crash on that path leaves nothing but `tray.log`, and the
process just silently isn't there. For every item below, first quit the
installed tray (right-click → Quit, or `taskkill`) and run it by hand with a
real interpreter instead, from the repo root:

```powershell
venv\Scripts\python.exe bin\ai-smartbar
```

Leave that PowerShell window open and watch it while you work through the
checklist — a traceback there is often the fastest diagnosis. Also tail the
log the installed (pythonw) path writes to, since that's what a real login
session uses:

```powershell
Get-Content -Tail 20 -Wait $env:LOCALAPPDATA\ai-smartbar\tray.log
```

## Checklist

Each item is one concrete action and one observable pass/fail. Check DPI
items on whatever monitor(s) you have; if you can change scaling
(Settings → Display → Scale), do all three on the same monitor.

1. **Tray icon renders at 100% scale.** Set display scaling to 100%, log
   in fresh. Pass: the twin-pill icon appears in the tray, not a blank or
   generic-app icon, and is legible at normal size (not visibly blurred or
   cropped).
2. **Tray icon renders at 150% scale.** Set scaling to 150%, restart the
   tray (`venv\Scripts\python.exe bin\ai-smartbar`). Pass: icon is still
   sharp, not upscaled/blurry, and pill fill levels are still readable.
3. **Tray icon renders at 200% scale.** Same at 200%. Pass: same bar as
   above. Fail mode to watch for: icon rendered at the 100% pixel size and
   stretched by the OS (visibly blocky).
4. **Left-click opens the panel.** With the tray running, single left-click
   the icon once. Pass: the panel window appears near the tray icon within
   ~1 second, showing account cards. Fail: nothing happens, or the OS
   context menu opens instead.
5. **Focus-out dismisses it.** With the panel open, click anywhere else on
   the desktop (another window, the desktop itself). Pass: the panel
   disappears without being told to. Fail: it stays open, or it takes
   another click to close.
6. **Escape hides it.** Open the panel, press `Esc` without clicking
   elsewhere. Pass: the panel closes immediately. Fail: nothing happens, or
   it closes some other window instead.
7. **Hover states track the pointer.** Open the panel, move the mouse
   across an account card without clicking. Pass: the card under the
   pointer visibly highlights and the highlight moves card-to-card with the
   pointer, with no stale highlight left behind on a card the pointer has
   left.
8. **Clicking a card switches account.** With ≥2 accounts registered, open
   the panel and click a non-active card's `Make Active` control. Pass:
   that card becomes the outlined/ACTIVE one immediately (optimistic UI),
   and `cswap list --json` (or the next poll) confirms the switch stuck.
   Fail: nothing visibly changes, or the wrong card activates.
9. **A toast appears at ≥90%.** Force an account's active metric to ≥90%
   used (`SMARTBAR_TEST_THRESHOLD` from the README, or wait for a real
   account to cross it) and let a poll run. Pass: a Windows toast/balloon
   notification appears naming the account and the best account to switch
   to. Fail: no notification, or one with no actionable content.
10. **Login autostart survives a reboot.** After `.\install\windows.ps1`,
    reboot the machine (not just log off) and log back in. Pass: the tray
    icon is present without running anything by hand, and
    `%LOCALAPPDATA%\ai-smartbar\tray.log` shows a fresh startup entry timed
    at login. Fail: no icon, or the log's last entry predates the reboot.
11. **`--update` applies and restarts.** With a newer commit/tag available
    on the tracked channel, run `ai-smartbar --update` (or wait for the
    Scheduled Task). Pass: the script re-runs `install/windows.ps1`, the
    old tray process exits, and exactly one new tray process is running
    afterward with the new version (check `ai-smartbar --once` or the
    panel's version row). Fail: two trays running, zero trays running, or
    the version not moving.
12. **No console window flashes on any 60-second poll.** Leave the
    installed (pythonw-launched) tray running for several poll cycles
    (`SMARTBAR_INTERVAL`, default 60s) and watch the screen the whole time.
    Pass: no black console window ever flickers on screen, including during
    a `cswap` subprocess call or an update check. Fail: any visible flash,
    however brief — it means a subprocess was spawned without
    `smartbar.core.portable.no_window()`.
13. **Ctrl+Alt+A opens the panel without touching the tray icon.** With the
    tray running and keyboard focus in some other application, press
    Ctrl+Alt+A. Pass: the panel opens exactly as it does on a left-click of
    the tray icon, within about a second. Fail: nothing happens (check
    `tray.log` for a `RegisterHotKey` failure — most likely another
    application already owns that combination), or a different action
    fires.

## Known-unresolved issues

These are documented gaps in the Windows port, not surprises you're meant
to rediscover. A checklist that hides them is worse than none — read this
before you start, and don't file a bug for exactly these symptoms without
first checking whether they're already accounted for here. All three are
from the Phase 3 commit (`3a37538`, `feat(windows): tray front-end on
pystray + tkinter`).

- **Square panel vs. rounded corners.** The panel renders with square
  corners (`radius=0.0`, `smartbar/windows/popover_window.py:160-175`) —
  deliberately, not a bug: tkinter has no per-pixel-alpha window without
  `WS_EX_LAYERED` + `UpdateLayeredWindow` via `ctypes`, which the port
  didn't wire up. If the square panel looks wrong against real Windows
  chrome, the documented (but unimplemented) fallback is colour-key
  transparency via `wm_attributes("-transparentcolor", ...)`. **Checklist
  item to add once you've seen it live:** does square actually look wrong
  enough on real hardware to justify implementing that fallback?
- **The `_to_main` shutdown race.** `smartbar/windows/tray.py:248-284`
  guards cross-thread UI callbacks with a `self._shutdown` flag so a
  daemon thread's late callback doesn't hit a torn-down Tk root — but the
  guard is check-then-act: a daemon can read the flag as `False`, get
  descheduled, and resume after `_quit` has already set it and the
  mainloop has returned, landing the exact `RuntimeError` the guard exists
  to prevent. Closing it fully means joining the daemon threads with a
  timeout on the quit path; that wasn't done here. **What to watch for:**
  a traceback in the console (see [above](#before-you-start-get-a-console))
  mentioning `after_idle` or `WaitForMainloop` right around Quit, especially
  if you quit while a `--check-update` subprocess is still in flight.
- **The `icon.menu` / `DestroyMenu` race.** `smartbar/windows/tray.py:370-410`
  reassigns the whole pystray menu on every refresh that has new content
  (an account switch, a manual check finishing, a new update becoming
  available). Reassignment runs `DestroyMenu` on the previous menu handle
  synchronously with no lock; if the user has *just* right-clicked and
  pystray is mid-`TrackPopupMenuEx` on its own worker thread with that same
  handle, the two can collide. It's mitigated (routine polling refreshes
  are skipped via `_last_menu_signature` when nothing changed) but not
  closed — closing it fully would mean depending on pystray's private
  `_win32.py` internals, which this port chose not to do. **What to watch
  for:** right-click the tray icon repeatedly while triggering an account
  switch or update from another window/session; a crash or a garbled
  context menu right at that moment is this race, not a new bug.

### From Phase 4 (`install/windows.ps1`, the update path)

The installer was written against `install/linux.sh` on a Mac. It has never
been executed, and it was never even **parse-checked** — no PowerShell was
available on the authoring machine. Treat the first run as debugging, not
installing, and run it from a console (`powershell -File .\install\windows.ps1`)
so you can see what it says.

- **`Set-StrictMode` is deliberately off** (`install/windows.ps1:44-48`). It
  would normally be on, but a strict-mode violation in never-executed code is
  a hard error discovered mid-install, potentially with the old tray already
  stopped. Once the script has run clean end-to-end, turn it on and run it
  again — that is the point at which it starts paying for itself.
- **The non-interactive fetch probe is weaker than the Linux one**
  (`install/windows.ps1:Install-Updater`). `install/linux.sh:104` uses
  `env -i` to run `git` with a completely empty environment, which is what
  makes its probe airtight. Windows has no `env -i`, and
  `GIT_TERMINAL_PROMPT=0` only suppresses *terminal* prompts — Git Credential
  Manager is a GUI helper and will pop a window instead. `GCM_INTERACTIVE=never`
  covers current GCM, but a third-party helper could still prompt. **What to
  watch for:** the installer prints `Non-interactive fetch OK.` and updates
  still hang later. If that happens, the probe passed something it should have
  failed.
- **No tray liveness check after an update** (`smartbar/update_runner.py:verify`).
  On macOS, `verify()` asks launchd for a PID and rolls the update back if the
  app died on launch. Nothing tracks a PID for the Windows tray, and every
  stdlib-only proxy considered (matching `tasklist` by image name; a WMI
  command-line query) was judged too unreliable to hang a rollback on — a false
  "dead" would revert a perfectly good release. So the Windows arm only checks
  `--version` and logs. **Consequence to verify by hand:** apply an update, then
  confirm the tray actually came back. Nothing else will notice if it did not.
- **Tray-process matching is unverified** (`install/windows.ps1:Get-TrayProcess`).
  The installer must stop the old tray without killing the in-flight `--update`
  run that is executing it — the Linux side scopes its `pkill` to
  `ai-smartbar$` for exactly this reason. The Windows equivalent matches a
  `python(w).exe` whose command line *ends* with the launcher path. That regex
  has never been tested against a real `Win32_Process.CommandLine`, and the
  quoting Windows applies there is the part most likely to be wrong.
- **The install probe reads a path under `System32\Tasks`**
  (`smartbar/update_runner.py:_win_task_file`). `present_installers()` decides
  this device is installed by stat-ing `%SystemRoot%\System32\Tasks\AI smartbar
  update` — cheap and side-effect-free, which matters because it runs on every
  update pass. But it has only ever been exercised against a fake `SystemRoot`
  in a temp directory. Depending on how the task was registered, reading that
  file on real Windows can require elevation. **What to watch for:** an update
  run that logs `no installers detected` while the task is plainly visible in
  Task Scheduler. If that happens the probe needs the Startup-shortcut half
  (which lives under the user's own `%APPDATA%` and has no such problem) to
  carry it, or a `schtasks /query` fallback.

### Installer checklist

Run these in order, on top of the panel checklist above.

14. **Fresh install on a machine with no prior task.** Pass: completes and
    prints `ai-smartbar is running.` Fail: any abort before the shortcut is
    written — the fresh-install path is where `install/linux.sh:48-53` shipped
    a real bug, and this script's equivalent guard has never run.
15. **Second run is idempotent.** Re-run with no arguments. Pass: still exactly
    one tray, task still registered once, no error. Fail: a duplicate tray, or
    `Register-ScheduledTask` throwing because the task exists.
16. **Channel read-back survives a re-run.** `.\install\windows.ps1 -Channel main`,
    then re-run with **no** `-Channel`. Pass: `Update task registered
    (channel=main, …)`. Fail: it says `release` — that is the silent
    channel-flip this read-back exists to prevent, and it would quietly move a
    development box onto the release line.
17. **A path with a space.** Clone to `C:\Program Files\...` or any path with a
    space and install from there. Pass: everything works. Fail: anything at
    all — this is the single likeliest defect class in the whole script.
18. **`-Uninstall` is complete.** Pass: tray stopped, task gone from Task
    Scheduler, shortcut gone from `shell:startup`, `%LOCALAPPDATA%\ai-smartbar`
    gone, checkout untouched.
