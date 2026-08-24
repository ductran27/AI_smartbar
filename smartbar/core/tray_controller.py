"""Platform-neutral tray/menu-bar state machine, shared by linux/tray.py,
windows/tray.py and macos/menubar.py.

Why this exists: those three files were never one implementation with two
ports. Each grew its own copy of the fetch/apply/alert/recapture/check-
update state machine, and copies drift the moment one of them gets a fix
the others don't. The concrete case this codebase already lived through is
commit 4c931bd: Windows grew a scrolling viewport for an overtall popover
in one batch, and Linux — same bug, same fix shape — got nothing, because
nothing forced the two behaviour layers to match. That drift was in the
popover's own paint/layout code (out of scope here — see this module's own
"risks" in the design notes), but the failure mode is exactly the one this
file exists to close off for the LAYER it does own: whether a stale fetch
can overwrite a fresh snapshot, whether a check-update result sticks around
for the right number of seconds, whether an alert fires once per threshold
window instead of every poll, whether a switch/remove failure is reported
instead of swallowed. Pin that state machine ONCE, here, with one set of
tests, and the three front-ends stop being three chances for the same bug
to happen twice more.

Toolkit-free by construction, the same discipline smartbar/paint/
popover_draw.py already follows for the painter: this module imports
nothing that only exists on one platform (no gi, no pystray, no tkinter,
no rumps, no cairo). Every place a front-end's behaviour genuinely has to
differ — how a UI touch reaches the thread that is allowed to make it, how
an icon gets repainted, whether a card panel exists at all — is a method
on the `host` object passed into TrayController, never a `sys.platform`
check in here. A host that does not have some capability (macOS's missing
card panel, today) says so through the contract itself (`has_panel`, the
optional panel triad) rather than the controller silently assuming every
host has every capability.

`TrayHost` below documents that contract. It is deliberately NOT an
abc.ABC — nothing else in this codebase pays for heavy typing machinery
(core/recapture.py, core/alerts.py and the rest are plain classes with
docstrings carrying the contract), and a host here is still duck-typed:
linux/tray.py, windows/tray.py and macos/menubar.py's own `Tray`/
`SmartBarApp` classes are free to satisfy this shape without inheriting
from it. `TrayHost` exists so the shape has ONE place to be read and
tests/support/stubs.py-style fakes have one contract to fake against.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time

from smartbar import presence_client, sysmon_runner
from smartbar.core import codex, cswap, model, plan, portable, presence, sysmon
from smartbar.core import update as update_core
from smartbar.core.alerts import Alert, AlertManager
from smartbar.core.recapture import RecapturePolicy

# A manual check runs `git fetch`. Bounded so a stalled network leaves the row
# saying "Check failed" rather than "Checking…" for ever. Same literal value
# as the three front-ends define separately today (see the design notes'
# shared_methods entry for _check_update) — kept as one named constant now.
CHECK_TIMEOUT = 120
# How long a manual check's outcome sits in the menu before the row reverts.
CHECK_RESULT_SECONDS = 20

log = logging.getLogger("ai-smartbar")


# --- the host contract -----------------------------------------------------

class TrayHost:
    """Documents the shape a front-end's Tray/SmartBarApp must satisfy.

    Never instantiated or inherited from by production code — see the
    module docstring for why this stays duck-typed rather than an ABC.
    Every method below raises NotImplementedError so that a test double
    which forgets to override one fails loudly at the call site instead of
    silently doing nothing.

    Two methods here (`check_update_argv`, `schedule`) are NOT in the
    approved host_protocol design as handed down — they were added while
    implementing it, and are called out again in this project's report:
    the design's own shared_methods entry for `_check_update` says the
    only per-platform difference is "the subprocess prefix (sys.executable
    on Windows)"; a controller that hard-codes that prefix would need an
    `if sys.platform` check, which instruction #3 rules out, so the argv
    itself has to come from the host. `schedule` exists for the same
    reason on the `_checked` -> `_clear_check_result` timer: `notify`/
    `call_on_ui_thread` are both immediate-dispatch, and nothing in the
    approved design offers a DELAYED one, but GLib.timeout_add_seconds /
    root.after / threading.Timer are three genuinely different primitives
    with no shared shape beyond "run this again in N seconds" — exactly
    the kind of divergence the design's own host_protocol pattern exists
    to carry.
    """

    #: True for a host with a real card panel (Linux, Windows today).
    #: False (the default) means show_panel/hide_panel/panel_visible/
    #: refresh_panel are never called — see the "Panel/popover existence"
    #: divergence: this is a genuine, currently-permanent three-way split,
    #: not something the controller may paper over by assuming a panel.
    has_panel = False

    def set_icon(self, states: list, update_pending: bool) -> None:
        """Repaint the tray icon from pill states plus the update-pending
        badge. See linux/tray.py's _set_icon, windows/tray.py's _set_icon
        for the two real implementations; macOS has none yet (a real gap,
        not a divergence to preserve — see the design's per_platform note)."""
        raise NotImplementedError

    def set_title(self, text: str) -> None:
        """Update the tray tooltip / menu-bar text."""
        raise NotImplementedError

    def rebuild_menu(self) -> None:
        """Recompute and (re)install the launcher/actions menu."""
        raise NotImplementedError

    def call_on_ui_thread(self, callback, *args) -> None:
        """Marshal callback(*args) onto whichever thread may touch UI
        state. The unified thread -> UI handoff: GLib.idle_add on Linux,
        root.after_idle on Windows, a queue+timer poll on macOS. Must be
        safe to call whether or not the caller is already on that thread
        (windows/tray.py's own _to_main docstring already relies on this
        for its "quit reached from the tk thread too" case) — this is what
        lets a single controller call site work under every host's
        threading model without the controller ever needing to know which
        one it is talking to."""
        raise NotImplementedError

    def schedule(self, seconds: float, callback, *args) -> None:
        """Run callback(*args) on the UI thread after `seconds`. See this
        class's own docstring for why this exists beyond the approved
        design: it is the delayed-dispatch counterpart to
        call_on_ui_thread, needed by _checked's CHECK_RESULT_SECONDS
        timer."""
        raise NotImplementedError

    def notify(self, alert: Alert, urgency: str = "critical") -> None:
        """Show a native notification. `urgency` distinguishes an
        interrupting limit alert ('critical') from a user-requested
        confirmation ('normal', e.g. a manual check's result) — hosts
        whose notification API has no such concept (pystray, rumps) are
        free to ignore the parameter; the controller must not assume it
        always has an observable effect (see the design's own divergence
        note on this)."""
        raise NotImplementedError

    def check_update_argv(self) -> list:
        """The argv for `--check-update --json` on this host. See this
        class's own docstring for why this is a host method rather than a
        controller-owned constant."""
        raise NotImplementedError

    # -- optional panel triad -------------------------------------------
    # Only ever called when has_panel is True. Hosts without a panel (the
    # default) never need to override these no-ops.

    def show_panel(self) -> None:
        pass

    def hide_panel(self) -> None:
        pass

    def panel_visible(self) -> bool:
        return False

    def refresh_panel(self) -> None:
        """Repaint the panel's current content in place (Popover's own
        refresh_layout()). Not named in the approved host_protocol's panel
        triad (show_panel/hide_panel/panel_visible) but required by
        _apply_snapshot/_apply_error/_set_action_error's "popover.
        refresh_layout() if visible" step in the shared_methods design —
        added here for the same reason check_update_argv/schedule were:
        the design named the capability's EXISTENCE (panel-having is
        optional) without naming this particular method, and a controller
        cannot repaint a panel it can only show/hide/query."""
        pass


# --- the controller ---------------------------------------------------------

class TrayController:
    """Owns the generation-guarded fetch/apply state machine, alert firing,
    recapture pacing, the manual update-check flow, and the switch/remove
    worker-thread tail — the parts the design's shared_methods section
    measured at 88-100% identical across linux/tray.py and windows/tray.py
    (and, for several of them, macos/menubar.py too) before this extraction.

    Every method that mutates host-visible state (icon, title, menu, panel,
    notifications) is reached either directly on the UI thread (because its
    caller already arrived there via host.call_on_ui_thread/host.schedule)
    or by explicitly handing off through one of those two methods first — a
    background thread in this file never touches `self.host` directly. That
    invariant is what tests/test_tray_controller.py's
    TestEveryUiTouchIsMarshalled pins.

    Deliberately NOT owned here, per the design's own divergence notes:
      * `self.provider` / `self.confirm` (panel tab / two-step remove
        state) — these only make sense where a panel exists, and hoisting
        them into the controller's core state ahead of macOS actually
        growing a panel was explicitly called out as premature.
      * The optimistic flip's actual state mutation (moving
        account.active flags, repainting) — that is `flip_active`, a
        callable the caller supplies to `on_switch`, because it is
        exactly the set_icon/set_title/rebuild_menu/panel-refresh
        sequence every host already owns through the rest of this
        contract; the controller only owns WHEN it runs (guarded,
        marshaled) not WHAT it does.
    """

    def __init__(self, host: TrayHost):
        self.host = host
        self.alerts = AlertManager()
        self.recapture = RecapturePolicy()

        self.snapshot = None
        self.failures = 0
        self.last_error = ""
        self.action_error = ""   # switch/remove failure; sticky until the
                                  # next attempt (mirrors UsageStore.swift)
        self.refreshing = False  # a usage fetch is in flight

        self.generation = 0      # stamps fetches so stale ones get dropped
        self._generation_lock = threading.Lock()  # see _start_fetch's own
                                  # docstring: a lock-free increment is only
                                  # safe when call_on_ui_thread is
                                  # synchronous-with-caller for every host,
                                  # which is not guaranteed (Windows is not)
        self.last_fetch_at = 0.0  # monotonic; guards menu-open refreshes

        self.presence_started = False  # first beat waits for the first fetch

        self.update_pending = ""  # "" or "X.Y.Z"
        self.update_blocked = ""

        self.checking = False    # a manual update check is in flight
        self.check_result = ""   # its outcome, shown in the row for a while
        self.check_token = 0     # so a stale timer cannot clear a newer one

        self.system = None       # latest System-tab payload, or None (off)
        self._sysmon_fired = set()   # alert keys already notified (fire-once)

    # --- pending-update reading -----------------------------------------

    def _pending_update(self) -> str:
        """The release the updater found waiting, or "" — never raises.

        Also records why an update is being held back (dirty checkout,
        unpushed commits) so the panel footer/menu row can say so.
        """
        # update_runner stays a LAZY import: it pulls in the lock shims and
        # the git layer, and a tray must not pay for those at import time.
        from smartbar import update_runner
        pending, self.update_blocked = update_runner.pending_for_ui()
        self.update_pending = pending
        return pending

    # --- the fetch/apply cycle -------------------------------------------

    def _start_fetch(self) -> None:
        """Bump the generation, spawn the worker. Safe to call from ANY
        thread: everything below the lock is a plain attribute write plus
        Thread.start() — mirrors windows/tray.py's own justification for
        calling this straight from a worker thread (the end of a switch's
        or a recapture's run()) with no marshal. The lock scopes strictly
        to the increment-and-capture, per this class's own risk note on
        self.generation above."""
        with self._generation_lock:
            self.generation += 1
            generation = self.generation
        self.last_fetch_at = time.monotonic()
        self.refreshing = True
        threading.Thread(target=self._fetch, args=(generation,),
                         daemon=True).start()

    def _tick(self) -> bool:
        """One recurring-poll firing. Return value is GLib's "repeat me"
        convention; hosts that reschedule themselves (Windows' root.after,
        macOS's rumps.Timer) simply ignore it."""
        self._start_fetch()
        return True

    def _fetch(self, generation: int) -> None:
        """Worker thread: does the I/O, touches nothing on `self.host`
        directly — every outcome is handed to the UI thread instead."""
        try:
            snap = cswap.fetch(fresh=True)
        except cswap.CswapError as exc:
            log.warning("fetch failed: %s", exc)
            self.host.call_on_ui_thread(self._apply_error, str(exc), generation)
            return
        self.host.call_on_ui_thread(self._apply_snapshot, snap, generation)

    def _apply_snapshot(self, snap, generation: int) -> None:
        """Runs on the UI thread — reached only via host.call_on_ui_thread
        from _fetch's worker, so every host touch below is safe.

        Ordering pinned verbatim from the design's shared_methods entry:
        generation guard -> counters reset -> snapshot swap -> presence/
        plan/codex decoration -> first-snapshot presence beat -> schema-
        warning log -> update_pending -> set_icon -> set_title ->
        rebuild_menu -> panel refresh (if any) -> alerts -> recapture.
        Every platform's own _apply_snapshot did this in this order before
        extraction; reordering here would silently change what all three
        eventually show.
        """
        if generation != self.generation:
            return  # superseded (e.g. a pre-switch fetch landing late)
        self.refreshing = False
        self.failures = 0
        self.last_error = ""
        self.snapshot = snap
        # Stamp the device counts and plan badges before anything renders:
        # the menu rows and the panel both name accounts through
        # model.account_label.
        presence.apply_counts(snap, presence_client.counts())
        plan.apply_plans(snap, plan.plans_by_email())
        # ChatGPT accounts ride the snapshot's separate list (cheap local
        # reads, mtime-cached); a panel's OpenAI tab renders them.
        snap.openai = codex.accounts()
        if not self.presence_started:
            # First real snapshot: announce this device now rather than
            # after a whole interval, and do it with accounts already in
            # hand so the beat costs no cswap call.
            self.presence_started = True
            presence_client.beat(snap)
        if snap.schema_warning:
            log.warning("%s", snap.schema_warning)
        account = snap.active_account
        # Cheap local file read; keeps the badge and the menu row in step
        # with whatever the update agent last decided.
        self._pending_update()
        self.host.set_icon(model.pill_states(account), bool(self.update_pending))
        self.host.set_title(model.title_line(account))
        self.host.rebuild_menu()
        if self.host.has_panel and self.host.panel_visible():
            self.host.refresh_panel()
        for alert in self.alerts.check(snap):
            self.host.notify(alert)
        self._maybe_recapture(snap)

    def _apply_error(self, message: str, generation: int) -> None:
        """Runs on the UI thread — see _apply_snapshot's docstring."""
        if generation != self.generation:
            return  # superseded
        self.refreshing = False
        self.failures += 1
        self.last_error = message
        if self.failures >= 3:
            self.host.set_icon([], bool(self.update_pending))
            self.host.set_title(f"AI smartbar — cswap error: {message[:80]}")
        self.host.rebuild_menu()
        if self.host.has_panel and self.host.panel_visible():
            self.host.refresh_panel()

    def _maybe_recapture(self, snap) -> None:
        # `cswap add` keeps stored credentials alive: it registers an
        # unregistered /login, heals an active slot whose backup died
        # (relogin_required), and periodically re-captures the live login
        # so Claude Code's token rotations never orphan the backup. The
        # policy paces all three; SMARTBAR_AUTO_ADD/SMARTBAR_RECAPTURE=off
        # disable them.
        action = self.recapture.action(snap, time.monotonic())
        if action is None:
            return

        def run():
            try:
                cswap.add()
            except cswap.CswapError as exc:
                log.info("cswap add (%s) skipped: %s", action, exc)
                return
            log.info("cswap add ran (%s): current login re-captured", action)
            if action != "refresh":  # registration/heal changes what we show
                self._start_fetch()
        threading.Thread(target=run, daemon=True).start()

    # --- switch / remove worker-thread tail -------------------------------

    def on_switch(self, number: int, flip_active) -> None:
        """Optimistic ACTIVE flip plus the switch's worker-thread tail —
        the two pieces the design's shared_methods/divergences sections
        both point at: the tail (try cswap.switch/except/call_on_ui_thread/
        _start_fetch) is byte-identical across linux/tray.py and
        windows/tray.py; the flip step itself is genuinely platform-bound
        (Linux applies it synchronously from GTK's own thread today,
        Windows must marshal because pystray dispatches account-row clicks
        on its own worker thread — Decision D1), so it is supplied here as
        `flip_active(number)` and always routed through
        host.call_on_ui_thread, which is required (see TrayHost.
        call_on_ui_thread's docstring) to work whether or not the caller
        is already on the UI thread.

        flip_active is REPAINT-ONLY: the generation bump that makes a
        pre-switch fetch get dropped is done here, under the controller's
        own lock, so that no host can forget it or guard it differently.
        See the comment on that bump below for the race this closed.

        The switch_blocked re-check that only windows/tray.py's
        _begin_switch used to perform is promoted here for every host — a
        strictly safer superset, since a caller that reaches this point
        without the menu's own disabled-row protection (a popover card
        click, say) must not be allowed to try switching to a dead
        credential.
        """
        account = None
        if self.snapshot is not None:
            account = next((a for a in self.snapshot.accounts
                            if a.number == number), None)
        if account is not None and model.switch_blocked(account):
            self.host.call_on_ui_thread(
                self._set_action_error,
                f"Cannot switch: {model.state_text(account)}")
            return
        self.action_error = ""
        # The generation bump lives HERE, not inside the host's flip_active.
        # It is controller state guarded by a controller-private lock, and
        # leaving it to each host reproduced exactly the drift this module
        # exists to prevent: linux/tray.py bumped it with a bare `+=`,
        # windows/tray.py bumped it under _generation_lock, macos/menubar.py
        # never bumped it at all. The unguarded one was a real race, because
        # flip_active is marshalled and GLib.idle_add ALWAYS defers: the flip
        # could land on the GTK main loop while the worker below was already
        # inside _start_fetch's own locked increment, so one of the two
        # updates was lost -- and a lost update lets a stale pre-switch fetch
        # match self.generation again and overwrite the fresh post-switch
        # snapshot. Bumping synchronously here, before any thread exists to
        # race it, also restores the ordering this had before the extraction.
        with self._generation_lock:
            self.generation += 1
        self.host.call_on_ui_thread(flip_active, number)

        def run():
            try:
                cswap.switch(number)
            except cswap.CswapError as exc:
                log.exception("switch failed")
                self.host.call_on_ui_thread(
                    self._set_action_error, f"Switch failed: {exc}")
            self._start_fetch()
        threading.Thread(target=run, daemon=True).start()

    def on_remove(self, token: str) -> None:
        """Confirmed removal ("<provider>:<id>"): drop the card now, remove
        in the background through the ONE core function per provider, then
        refetch — the truth that resurrects the card if the removal failed.
        Byte-identical worker-thread tail across linux/tray.py and
        windows/tray.py per the design's shared_methods entry; macOS has no
        removal UI today, so nothing calls this there yet, which is a gap
        to close in front-end work, not a reason to leave it out here.

        Does NOT touch a host's own `confirm` (two-step-remove) state — see
        this class's own docstring for why that field stays host-owned;
        callers clear it themselves before/after calling this.
        """
        provider, _, ident = token.partition(":")
        self.action_error = ""
        if self.snapshot is not None:
            if provider == "claude":
                self.snapshot.accounts = [
                    a for a in self.snapshot.accounts
                    if str(a.number) != ident]
            else:
                self.snapshot.openai = [
                    a for a in self.snapshot.openai if a.email != ident]

        def run():
            try:
                if provider == "claude":
                    cswap.remove_account(int(ident))
                else:
                    codex.remove_account(ident)
            except (cswap.CswapError, ValueError, OSError) as exc:
                log.exception("remove failed")
                self.host.call_on_ui_thread(
                    self._set_action_error, f"Remove failed: {exc}")
            self._start_fetch()
        threading.Thread(target=run, daemon=True).start()

    # --- System tab -----------------------------------------------------

    def sysmon_tick(self) -> None:
        """One System-tab poll, scheduled by the host on its own timer.
        Samples in a worker (the sample sleeps ~0.5 s, which must never
        block the UI thread) and marshals the payload back."""
        if not sysmon.enabled():
            self.system = None
            return
        threading.Thread(target=self._sysmon_fetch, daemon=True).start()

    def _sysmon_fetch(self) -> None:
        try:
            payload = sysmon_runner.background_tick()
        except Exception:                       # a probe hiccup is not fatal
            log.exception("sysmon tick failed")
            return
        self.host.call_on_ui_thread(self._apply_system, payload)

    def _apply_system(self, payload) -> None:
        """UI thread: store the payload, fire any leftover notifications
        (once per alert key, re-armed when the key disappears), and repaint
        the panel if it is open on the System tab."""
        self.system = payload
        alerts = payload.get("alerts", [])
        current = {a.get("key", a["title"]) for a in alerts}
        for alert in alerts:
            key = alert.get("key", alert["title"])
            if key in self._sysmon_fired:
                continue    # already notified for this exact situation
            self._sysmon_fired.add(key)
            self.host.notify(Alert(alert["title"], alert["body"]))
        self._sysmon_fired &= current   # re-arm what cleared
        if self.host.has_panel and self.host.panel_visible():
            self.host.refresh_panel()

    def on_kill(self, token: str) -> None:
        """Confirmed kill: drop the row now (optimistic), signal in the
        background through the one guarded runner, then re-tick — the truth
        that restores the row if the kill was refused or failed. Mirrors
        on_remove's shape."""
        self.action_error = ""
        if self.system is not None:
            for group in ("leftovers", "busy"):
                block = self.system.get(group)
                if block:
                    block["rows"] = [row for row in block["rows"]
                                     if row.get("token") != token]
        if self.host.has_panel and self.host.panel_visible():
            self.host.refresh_panel()

        def run():
            ok, error = sysmon_runner.kill(token)
            if not ok:
                self.host.call_on_ui_thread(self._set_action_error,
                                            f"Kill failed: {error}")
            self.host.call_on_ui_thread(self.sysmon_tick)
        threading.Thread(target=run, daemon=True).start()

    def _set_action_error(self, message: str) -> None:
        """host.call_on_ui_thread target: a worker thread must never poke
        host state directly, so a switch/remove failure lands here."""
        self.action_error = message
        if self.host.has_panel and self.host.panel_visible():
            self.host.refresh_panel()

    # --- the manual "check for updates" row -------------------------------

    def _check_row(self):
        """(label, clickable) for the manual update-check row. Two-tuple,
        not three: every host already knows its own idle-state callback is
        `self._on_check_update` (or a thin per-toolkit wrapper around it),
        so the only fact this needs to hand over is whether the row should
        be wired up at all — the same shape macOS's rumps row already
        used, generalized to all three."""
        if self.checking:
            return "⇅ Checking for updates…", False
        if self.check_result:
            return self.check_result, False
        return "⇅ Check for updates", True

    def _on_check_update(self) -> None:
        if self.checking:
            return
        self.checking = True
        self.check_result = ""
        self.check_token += 1
        self.host.call_on_ui_thread(self.host.rebuild_menu)
        threading.Thread(target=self._check_update, args=(self.check_token,),
                         daemon=True).start()

    def _check_update(self, token: int) -> None:
        """Worker thread — this does a network fetch (`--check-update
        --json` runs `git fetch`). `--check-update --json` reports only;
        it decides what to SAY, so no front-end's wording can drift from
        another's."""
        try:
            done = subprocess.run(self.host.check_update_argv(),
                                  capture_output=True, text=True,
                                  timeout=CHECK_TIMEOUT, **portable.no_window())
            answer = json.loads(done.stdout)
        except Exception:
            log.exception("update check could not run")
            answer = None
        self.host.call_on_ui_thread(self._checked, token, answer)

    def _checked(self, token: int, answer) -> None:
        if token != self.check_token:
            return  # superseded by a newer check
        self.checking = False
        self._pending_update()   # also sets update_blocked
        if not isinstance(answer, dict) or not answer.get("label"):
            failure = update_core.check_outcome(failed=True)
            answer = {"label": failure.label, "title": failure.title,
                      "body": failure.body}
        self.check_result = answer["label"]
        # Clicking a row closes the menu, so the label alone would be
        # invisible until the user opened it again — the notification is
        # the real feedback.
        self.host.notify(Alert(title=answer.get("title", "AI smartbar"),
                               body=answer.get("body", "")),
                         urgency="normal")
        # Repaint: the update-pending badge is driven by update_pending, so
        # a check that just found a release has to make it appear.
        account = self.snapshot.active_account if self.snapshot else None
        self.host.set_icon(model.pill_states(account) if account else [],
                           bool(self.update_pending))
        self.host.rebuild_menu()
        self.host.schedule(CHECK_RESULT_SECONDS, self._clear_check_result, token)

    def _clear_check_result(self, token: int) -> None:
        if token == self.check_token and self.check_result:
            self.check_result = ""
            self.host.rebuild_menu()
