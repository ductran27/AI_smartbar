@echo off
REM Windows has no shebang dispatch, so `bin/ai-smartbar` (an extension-less
REM `#!/usr/bin/env python3` script) cannot be exec'd directly the way
REM `subprocess.run([LAUNCHER, ...])` does on POSIX -- that raises
REM WinError 193 ("%1 is not a valid Win32 application"). This shim gives
REM Windows callers (and PATH-based invocation, e.g. an installed Start Menu
REM shortcut or a scheduled task) a real .cmd entry point that hands off to
REM Python, forwarding every argument untouched via `%*`.
REM
REM `%~dp0` is the drive+path of this .cmd file itself, so both the launcher
REM and the venv are found relative to this shim regardless of the caller's
REM current directory.
REM
REM Prefer the venv interpreter over the ambient one: install/windows.ps1
REM creates `venv\` next to `bin\` and pip-installs the GUI dependencies
REM (pystray, tkinter, Pillow) ONLY into it -- the system interpreter merely
REM bootstrapped that venv and never received those packages. A PATH-based
REM caller that fell through to the ambient `python` here imported the tray
REM against an interpreter missing them and died with an ImportError. Fall
REM back to ambient `python` only when the venv is absent (e.g. running
REM straight from a source checkout that never ran the installer). `python`
REM (not `python3`) is the ambient interpreter name on Windows -- the
REM official python.org installer puts `python.exe` on PATH, not a
REM `python3.exe` alias.
setlocal
set "VENV_PYTHON=%~dp0..\venv\Scripts\python.exe"
if exist "%VENV_PYTHON%" goto :venv
python "%~dp0ai-smartbar" %*
goto :eof
:venv
"%VENV_PYTHON%" "%~dp0ai-smartbar" %*
