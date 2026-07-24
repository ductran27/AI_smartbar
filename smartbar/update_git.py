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
from datetime import datetime, timezone

from smartbar.core import update

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
GIT_TIMEOUT = 180
RESCUE_PREFIX = "refs/smartbar-rescue/"

_VERSION_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


class GitError(RuntimeError):
    """A git command this updater cannot proceed without."""


def env() -> dict:
    """Subprocess env with a usable PATH and no interactive git prompts."""
    result = dict(os.environ)
    prepend = [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin",
               "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    parts = []
    for part in prepend + result.get("PATH", "").split(os.pathsep):
        if part and part not in parts:
            parts.append(part)
    result["PATH"] = os.pathsep.join(parts)
    result["GIT_TERMINAL_PROMPT"] = "0"
    result.setdefault("HOME", os.path.expanduser("~"))
    return result


def git_binary() -> str:
    return shutil.which("git", path=env()["PATH"]) or "/usr/bin/git"


def git(*args, check: bool = True, timeout: int = GIT_TIMEOUT) -> str:
    """Run git in the repo; stdout stripped. `check=False` yields "" on error."""
    try:
        proc = subprocess.run([git_binary(), "-C", REPO_ROOT, *args],
                              capture_output=True, text=True,
                              timeout=timeout, env=env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            raise GitError(f"git {' '.join(args)}: {exc}") from exc
        return ""
    if proc.returncode != 0:
        if check:
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
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
    """Refresh remote refs AND tags; tags are how releases are discovered."""
    git("fetch", "--tags", "--prune", "--quiet", "origin")


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


def rescue_ref(now=None) -> str:
    """Park local work in a ref so --reset never truly destroys anything.

    Recover with `git stash apply <ref>` — the ref is named for the moment
    it was taken and printed to update.log. The identity is forced inline:
    `stash create` writes a commit, and on a device where the user never set
    a global git identity it would otherwise fail — silently turning --reset
    into "discard without a rescue".
    """
    sha = git("-c", "user.name=AI smartbar", "-c",
              "user.email=ai-smartbar@localhost", "stash", "create",
              check=False)
    if not sha:
        return ""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    ref = RESCUE_PREFIX + stamp
    git("update-ref", ref, sha, check=False)
    return ref


def checkout(plan, *, reset: bool = False) -> str:
    """Move the checkout onto plan.target_ref; returns any rescue ref made."""
    rescue = rescue_ref() if reset else ""
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
