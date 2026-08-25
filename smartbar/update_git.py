"""Git plumbing for the self-updater: hardened env, repo facts, checkout.

Kept separate from update_runner.py so the runner stays about orchestration
(decide → apply → verify → roll back) and this file owns the awkward
realities of running git from a background agent:

* launchd and cron hand agents a bare PATH — the same trap that made v2's
  warmup silently never fire. Every call here rebuilds PATH.
* There is no TTY, so git must never be *able* to block on a credential
  prompt. GIT_TERMINAL_PROMPT=0 turns "ask the user" into a fast, logged
  failure instead of a job wedged forever.
* The AI_smartbar remote is typically PRIVATE, so a device needs a working
  non-interactive credential (macOS keychain helper, or an SSH key) or
  updates simply never apply. install/macos-update.sh probes for exactly
  that at install time rather than letting it fail silently later.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from smartbar.core import portable, update

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
GIT_TIMEOUT = 180
RESCUE_PREFIX = "refs/smartbar-rescue/"

_VERSION_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


class GitError(RuntimeError):
    """A git command this updater cannot proceed without."""


def env() -> dict:
    """Subprocess env with a usable PATH and no interactive git prompts."""
    result = dict(os.environ)
    # The bare-PATH trap this list works around is a launchd/cron thing: both
    # hand a POSIX agent a minimal PATH that skips wherever the user's shell
    # would have found git. Scheduled Tasks and Windows services inherit the
    # full user/system PATH instead, and Git for Windows' installer already
    # appends itself to it — so these entries would be inert at best there,
    # and at worst shadow a real (if oddly named) directory on whatever drive
    # the process happens to be running from.
    if sys.platform == "win32":
        prepend = []
    else:
        prepend = [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin",
                   "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    parts = []
    for part in prepend + result.get("PATH", "").split(os.pathsep):
        if part and part not in parts:
            parts.append(part)
    result["PATH"] = os.pathsep.join(parts)
    result["GIT_TERMINAL_PROMPT"] = "0"
    # GIT_TERMINAL_PROMPT stops TERMINAL prompts only. A desktop session
    # exporting SSH_ASKPASS/GIT_ASKPASS (KDE/GNOME, VS Code terminals) or
    # Git Credential Manager would still pop a GUI credential dialog — from
    # a background beat every five minutes. Force every route to fail fast
    # and quietly instead.
    result["GIT_ASKPASS"] = ""
    result["SSH_ASKPASS_REQUIRE"] = "never"
    result["GCM_INTERACTIVE"] = "never"
    result.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    result.setdefault("HOME", os.path.expanduser("~"))
    return result


def git_binary() -> str:
    """The git executable to run, resolved fresh on every call.

    "/usr/bin/git" is a sound last-resort default on POSIX: it is where the
    system git actually lives on every mac and most Linux distros even when
    PATH is broken. Windows has no equivalent fixed path — git.exe moves with
    the installer (per-user vs per-machine, Program Files vs Program Files
    (x86), scoop, winget) — so a literal fallback there would almost always
    be wrong. Raising hands callers one clear GitError to catch instead of a
    FileNotFoundError from deep inside subprocess.
    """
    found = shutil.which("git", path=env()["PATH"])
    if found:
        return found
    if sys.platform == "win32":
        raise GitError("git executable not found on PATH")
    return "/usr/bin/git"


def git(*args, check: bool = True, timeout: int = GIT_TIMEOUT) -> str:
    """Run git in the repo; stdout stripped. `check=False` yields "" on error."""
    try:
        # no_window: from a console-less pythonw tray (the manual check,
        # the update button) every git call popped a console on Windows.
        proc = subprocess.run([git_binary(), "-C", REPO_ROOT, *args],
                              capture_output=True, text=True,
                              timeout=timeout, env=env(),
                              **portable.no_window())
    # git_binary() is called inside the try above and now raises GitError on
    # win32, so it joins the pair that `check=False` is meant to swallow.
    except (OSError, subprocess.TimeoutExpired, GitError) as exc:
        if check:
            raise GitError(f"git {' '.join(args)}: {exc}") from exc
        return ""
    if proc.returncode != 0:
        if check:
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
            if not detail:
                # --quiet git calls fail with EMPTY output; five days of
                # "fetch failed: " with no reason came from exactly this.
                # A negative code names the signal that killed git.
                detail = f"exit {proc.returncode}"
            raise GitError(f"git {' '.join(args)} failed: {detail}")
        return ""
    return proc.stdout.strip()


def version_in_checkout() -> str:
    """__version__ read from disk, not imported — the file changes under us."""
    try:
        with open(os.path.join(REPO_ROOT, "smartbar", "__init__.py")) as handle:
            match = _VERSION_RE.search(handle.read())
    except OSError:
        return ""
    return match.group(1) if match else ""


def fetch() -> None:
    """Refresh remote refs AND tags; tags are how releases are discovered.

    --force: git >= 2.20 refuses to move an existing local tag, and a tag
    that HAS moved on origin (a re-cut release, the pre-public history
    rewrite) otherwise wedges every device's updater with exit 1 forever —
    the 08-18→08-23 silent failure on this very machine. Releases are
    discovered from local tags, so the moved tag must win.
    --prune-tags: a tag deleted on origin (a yanked release) must also stop
    being "newest" here, or devices keep re-applying it."""
    git("fetch", "--tags", "--force", "--prune", "--prune-tags", "--quiet",
        "origin")


def repo_state() -> update.RepoState:
    branch = git("rev-parse", "--abbrev-ref", "HEAD", check=False)
    unpushed = 0
    upstream = git("rev-parse", "--abbrev-ref", "@{upstream}", check=False)
    if upstream:
        unpushed = int(git("rev-list", "--count", f"{upstream}..HEAD",
                           check=False) or 0)
    return update.RepoState(
        head=git("rev-parse", "HEAD"),
        branch="" if branch == "HEAD" else branch,
        # Untracked files are ignored on purpose: build output and scratch
        # files must not be able to block a device's updates forever.
        dirty=bool(git("status", "--porcelain", "--untracked-files=no")),
        unpushed=unpushed,
        tags=git("tag", "--list").split(),
        head_tags=git("tag", "--points-at", "HEAD").split(),
        remote_main=git("rev-parse", "refs/remotes/origin/main", check=False),
        version=version_in_checkout(),
    )


def rescue_ref(now=None):
    """Park local work in refs so --reset never truly destroys anything.

    Returns `(head_ref, stash_ref)`. The two kinds of local work a --reset
    can discard need two DIFFERENT recovery verbs, so they are parked — and
    reported — separately:

    * `head_ref` always points at the HEAD commit. On channel=main a --reset
      discards UNPUSHED COMMITS, which `stash create` (uncommitted changes
      only) never saved. Recover them with `git branch <name> <head_ref>` —
      NOT `git stash apply`, which rejects a plain commit.
    * `stash_ref` is a `git stash create` object, or "" when the working tree
      was clean. Recover uncommitted changes with `git stash apply <stash_ref>`.

    The identity is forced inline: `stash create` writes a commit, and on a
    device where the user never set a global git identity it would otherwise
    fail — silently turning --reset into "discard without a rescue".
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    head_ref = RESCUE_PREFIX + stamp + "-head"
    git("update-ref", head_ref, "HEAD", check=False)
    sha = git("-c", "user.name=AI smartbar", "-c",
              "user.email=ai-smartbar@localhost", "stash", "create",
              check=False)
    stash_ref = ""
    if sha:
        stash_ref = RESCUE_PREFIX + stamp
        git("update-ref", stash_ref, sha, check=False)
    return head_ref, stash_ref


def checkout(plan, *, reset: bool = False):
    """Move the checkout onto plan.target_ref; returns the (head_ref,
    stash_ref) a --reset parked, or ("", "") when nothing was parked."""
    rescue = rescue_ref() if reset else ("", "")
    if plan.detach:
        # A release device is *pinned*: detached HEAD names its release.
        args = ["checkout"] + (["--force"] if reset else []) + \
               ["--detach", plan.target_ref]
        git(*args)
    elif reset:
        git("reset", "--hard", plan.target_ref)
    else:
        # channel=main is fast-forward only: never rewrites, never discards.
        git("merge", "--ff-only", plan.target_ref)
    return rescue


def restore(head: str, branch: str) -> None:
    """Put the checkout back where it was before a failed update."""
    if branch:
        git("checkout", "--force", branch, check=False)
        git("reset", "--hard", head, check=False)
    else:
        git("checkout", "--force", "--detach", head, check=False)
