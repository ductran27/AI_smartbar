"""Cross-platform shims for the three OS primitives the runners depend on.

`update_runner.py`, `warmup_runner.py` and `presence_runner.py` each need an
exclusive non-blocking lock so two beats of the same cron/launchd/systemd job
never race each other, and `presence_client.py` / the tray / the menubar each
need to fire off a detached helper process that keeps running after the
parent exits. All of that was written once, against `fcntl.flock` and
`start_new_session=True`, because the app only ran on macOS and Linux. Both
primitives are POSIX-only, and worse, `import fcntl` at module scope is
enough to make an entire module fail to import on Windows even if the
Windows code path never calls the function that needed it. This module is
the single place that branches on `sys.platform`, so every call site above
can stay platform-agnostic and every platform-only import stays inside a
function body instead of at module scope.
"""

from __future__ import annotations

import subprocess
import sys

#: Mutually exclusive Windows CreateProcess flags, kept here as a comment
#: because the two functions below each use exactly one of them:
#: `no_window()` uses CREATE_NO_WINDOW (host wants to see the child's output
#: land in ITS console, just with no console flash), while
#: `spawn_detached()` uses DETACHED_PROCESS (child gets no console at all,
#: because it is going to outlive the host and has nothing to attach to).
#: Passing both together is a documented Windows error.


def lock(path: str):
    """Take an exclusive, non-blocking advisory lock on `path`.

    Returns an open file handle on success, kept alive by the caller for as
    long as the lock should be held (closing it, or the process exiting,
    releases it). Returns None when another process already holds the lock,
    which every call site treats as "another run is in progress; skip".

    The three original call sites all did `open(path, "w")` before locking.
    That mode TRUNCATES the file, which was harmless under `fcntl.flock`:
    flock locks the whole open file description regardless of length, so a
    concurrent truncate by another opener does not change what is locked.
    `msvcrt.locking` is different — it locks a caller-chosen BYTE RANGE
    starting wherever the file position currently is, and a "w" truncates
    the file out from under a lock that another process is holding on
    bytes that no longer exist. Two racing `open(path, "w")` calls on
    Windows can each believe they hold a lock on an empty file while
    neither actually excludes the other. Opening in "a+" instead sidesteps
    this on both platforms: it creates the file if missing, never
    truncates, and leaves the file position at the end, so callers who
    still want a fresh view of the file need to seek(0) themselves — none
    of the three current call sites read the lock file's contents, they
    only use it as a mutex, so this is a no-op change for them.

    Precondition: the directory containing `path` already exists. Every
    call site creates it (`os.makedirs(CACHE_DIR, exist_ok=True)`) before
    ever calling this function, so it is not this function's job to do it
    again.
    """
    try:
        handle = open(path, "a+")
    except OSError:
        return None
    try:
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                handle.close()
                return None
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return None
    except Exception:
        handle.close()
        return None
    return handle


def spawn_detached(argv, **kwargs):
    """Start `argv` as a process that outlives this one and owns no console.

    On POSIX this is `start_new_session=True`, unchanged from every existing
    call site, so behaviour there is byte-identical to before. On Windows,
    `start_new_session` is not a `subprocess.Popen` keyword at all (it would
    raise), and the equivalent is the combination of DETACHED_PROCESS (the
    child gets no console, and does not inherit the parent's) and
    CREATE_NEW_PROCESS_GROUP (the child does not receive Ctrl+C/Ctrl+Break
    meant for the parent's console group, which matters for a helper that is
    meant to keep running after the launcher that started it exits or is
    killed). Callers pass their own stdout/stderr (every current call site
    sets DEVNULL) via `**kwargs`, which flow straight through.
    """
    if sys.platform == "win32":
        # subprocess only defines these two names on win32 builds of
        # CPython. Falling back to their documented numeric values (rather
        # than reading the attribute unconditionally) is what lets the test
        # suite fake sys.platform == "win32" on macOS/Linux CI and still
        # exercise this branch instead of AttributeError-ing on an
        # attribute the interpreter never defined on this OS.
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | detached | new_group
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def no_window() -> dict:
    """kwargs to splat into `subprocess.run`/`Popen` to suppress a console flash.

    On win32 this is `{"creationflags": subprocess.CREATE_NO_WINDOW}`: the
    child still runs attached to the parent's process the normal way (its
    output is still capturable), it just does not pop open a visible console
    window when the host is a windowed (non-console) GUI app. Elsewhere it is
    `{}` — POSIX has no console-window concept for a subprocess to flash.

    CREATE_NO_WINDOW and DETACHED_PROCESS (used by `spawn_detached` above)
    are documented by Microsoft as mutually exclusive creation flags; never
    OR them together into the same call. They solve different problems:
    this one is for short-lived children the caller still wants to wait on
    and read output from (`cswap.py`'s git/cswap subprocess calls,
    `presence_git.py`), `spawn_detached` is for a child meant to keep
    running on its own after this process exits.
    """
    if sys.platform == "win32":
        # Same reasoning as spawn_detached's fallback above: subprocess only
        # defines CREATE_NO_WINDOW on win32 builds, so a faked
        # sys.platform == "win32" on macOS/Linux CI needs the documented
        # numeric fallback to avoid an AttributeError that has nothing to
        # do with the branch under test.
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}
