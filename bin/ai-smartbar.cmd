@echo off
REM Windows has no shebang dispatch, so `bin/ai-smartbar` (an extension-less
REM `#!/usr/bin/env python3` script) cannot be exec'd directly the way
REM `subprocess.run([LAUNCHER, ...])` does on POSIX -- that raises
REM WinError 193 ("%1 is not a valid Win32 application"). This shim gives
REM Windows callers (and PATH-based invocation, e.g. an installed Start Menu
REM shortcut or a scheduled task) a real .cmd entry point that hands off to
REM the ambient python, forwarding every argument untouched via `%*`.
REM
REM `%~dp0` is the drive+path of this .cmd file itself, so the launcher is
REM found next to this shim regardless of the caller's current directory.
REM `python` (not `python3`) is the ambient interpreter name on Windows --
REM the official python.org installer puts `python.exe` on PATH, not a
REM `python3.exe` alias.
python "%~dp0ai-smartbar" %*
