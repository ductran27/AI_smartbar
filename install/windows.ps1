<#
.SYNOPSIS
Install (default) or -Uninstall ai-smartbar on Windows. No admin needed.

.DESCRIPTION
The Windows counterpart of install/linux.sh, and deliberately a line-by-line
mirror of it: same flags, same channel read-back, same "always end with exactly
one tray running" contract. Re-running this script IS the update apply step
(smartbar/core/update.py's apply_targets re-runs the installers after a
checkout), so
every step here has to be idempotent.

Also installs the self-updater as a Scheduled Task unless -NoAutoUpdate is
given.

NOT VERIFIED ON WINDOWS. This was written against install/linux.sh on a Mac
with no Windows host and no PowerShell available to even parse-check it. See
docs/windows-bring-up.md before trusting any of it.

.PARAMETER Uninstall
Stop the tray, remove the Scheduled Task, the Startup shortcut and the cache
directory, then exit.

.PARAMETER NoAutoUpdate
Install the tray but register no update task.

.PARAMETER Channel
'release' (default) or 'main'. Omit to KEEP whatever this device already uses —
see the read-back below, which is load-bearing.
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$NoAutoUpdate,
    [string]$Channel = ''
)

# Stop on cmdlet errors. Note this does NOT cover native commands (git, pip,
# python): their failures only show up in $LASTEXITCODE, which is why every
# native call below goes through Invoke-Native. That asymmetry is the single
# easiest way to write a PowerShell installer that reports success after a
# failed pip install, so it is handled explicitly rather than assumed.
$ErrorActionPreference = 'Stop'

# Set-StrictMode is deliberately NOT enabled. It turns a typo into a hard
# runtime error, which is normally what you want -- but nothing in this file
# has ever been executed, so a strict-mode violation would be discovered by a
# user mid-install with their tray already stopped. Explicit checks below are
# the substitute. Revisit once bring-up has actually run this.

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Venv = Join-Path $Repo 'venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$VenvPythonW = Join-Path $Venv 'Scripts\pythonw.exe'
$Launcher = Join-Path $Repo 'bin\ai-smartbar'
$Shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'AI smartbar.lnk'
$IconPng = Join-Path $Repo 'assets\ai-smartbar.png'
# A .lnk IconLocation must point at an .ico or an .exe -- it cannot read a
# PNG -- so the shared asset is converted once, here, and kept beside the
# checkout rather than in the cache: a shortcut outlives any cache clear.
$IconIco = Join-Path $Repo 'assets\ai-smartbar.ico'

# Must match smartbar/update_runner.py:_win_task_file(), which stats
# %SystemRoot%\System32\Tasks\<name> to decide this device is installed. That
# path only holds for a task registered at the ROOT, so never add -TaskPath.
$TaskName = 'AI smartbar update'

# Must match smartbar/core/paths.py:cache_dir(), including its fallback for a
# machine where %LOCALAPPDATA% is somehow unset.
$Cache = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA 'ai-smartbar'
} else {
    Join-Path $HOME '.cache\ai-smartbar'
}

function Invoke-Native {
    <#
    Run a native command and fail loudly if it exits non-zero.

    $ErrorActionPreference='Stop' does nothing for native commands, so without
    this every git/pip/python failure would sail past and the script would go
    on to report a successful install.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @(),
        [string]$What = ''
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        $label = if ($What) { $What } else { "$Command $($Arguments -join ' ')" }
        throw "$label failed (exit $LASTEXITCODE)"
    }
}

function Get-TrayProcess {
    <#
    The running no-argument tray, if any -- and NOTHING else.

    The Linux side scopes its pkill to the pattern 'ai-smartbar$'
    (install/linux.sh's TRAY_PATTERN) precisely so that an in-flight `--update` run is
    never caught: the updater re-runs this installer, so a kill that matched
    every ai-smartbar process would have the update terminate itself halfway
    through, leaving the device with no tray and a half-applied checkout.

    The same hazard is worse here, because on Windows every one of those
    processes is called 'pythonw.exe'. Matching on the image name alone would
    kill the updater, any unrelated Python GUI the user happens to be running,
    and this script's own children. So the filter is: a python process whose
    command line ends with the launcher path and has no arguments after it.
    `--update` and `--warmup-once` invocations carry arguments and therefore
    never match, which is exactly the Linux behaviour.
    #>
    $pattern = [regex]::Escape($Launcher) + '"?\s*$'
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -in @('pythonw.exe', 'python.exe') -and
            $_.CommandLine -and $_.CommandLine -match $pattern
        }
}

function Stop-Tray {
    foreach ($proc in @(Get-TrayProcess)) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Get-InstalledChannel {
    <#
    The channel this device is already on, or '' when it is a fresh install.

    WRITE/READ PAIR -- this function and the -Argument string built in
    Install-Updater below are two halves of one format and must change
    together. The task action's arguments are the only place a Scheduled Task
    can carry the setting (a task has no environment block, and a .lnk cannot
    carry one either), so the channel travels as a literal `--channel <name>`
    on the updater's own command line.

    Why this matters at all: without the read-back, every self-update would
    re-register the task with the default and silently flip a `main`
    development box onto `release`. install/linux.sh's channel read-back
    block (the `if [[ -z "$CHANNEL" ]]` ... `fi` guard) documents the same
    trap.

    -ErrorAction SilentlyContinue on Get-ScheduledTask is load-bearing. On a
    FRESH install there is no task, and under $ErrorActionPreference='Stop' the
    resulting error would abort the installer here -- before the venv, before
    the shortcut, before anything. That is not hypothetical: it is the exact
    bug install/linux.sh's `|| true` load-bearing EXISTING read-back
    describes shipping on the Linux side, where a
    missing unit made `sed` exit non-zero and killed every fresh install.
    #>
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { return '' }
    foreach ($action in @($task.Actions)) {
        $arguments = [string]$action.Arguments
        if ($arguments -match '--channel\s+(release|main)') {
            return $Matches[1]
        }
    }
    return ''
}

function Remove-Updater {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false `
        -ErrorAction SilentlyContinue
}

if ($Uninstall) {
    Stop-Tray
    Remove-Updater
    Remove-Item -LiteralPath $Shortcut -Force -ErrorAction SilentlyContinue
    # Generated from assets/ai-smartbar.png by this script, so it is ours
    # to remove; the PNG it came from is checked in and stays.
    Remove-Item -LiteralPath $IconIco -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Cache -Recurse -Force -ErrorAction SilentlyContinue
    # The venv lives inside the checkout and is this script's own artifact, so
    # it goes too. The CHECKOUT itself is never touched: it is what the updater
    # fast-forwards and what the user cloned, and linux.sh does not delete it
    # either.
    Remove-Item -LiteralPath $Venv -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host 'ai-smartbar uninstalled.'
    exit 0
}

# No explicit -Channel: keep whatever this device already uses. Re-running an
# installer (the updater does exactly that) must not flip a development box
# onto the release channel behind the user's back.
if (-not $Channel) {
    $Channel = Get-InstalledChannel
}
if (-not $Channel) { $Channel = 'release' }
if ($Channel -notin @('release', 'main')) {
    Write-Error "channel must be 'release' or 'main' (got '$Channel')"
    exit 2
}

# ---------------------------------------------------------------- venv

function Resolve-SystemPython {
    <#
    A system interpreter to build the venv with.

    The `py` launcher ships with python.org installs and is the documented way
    to pick a version; a Microsoft Store or custom install may only put
    `python` on PATH. Try both before giving up, and give an actionable error
    rather than a stack trace -- "no Python" is the single likeliest reason
    this script fails on a clean machine.
    #>
    $py = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($py) {
        $found = & $py.Source -3 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return $found.Trim() }
    }
    $python = Get-Command 'python' -ErrorAction SilentlyContinue
    if ($python) {
        # A Store stub named python.exe exists on stock Windows purely to open
        # the Store page; it runs and exits 9009/0 without being an
        # interpreter. Asking it for sys.executable weeds it out.
        $found = & $python.Source -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return $found.Trim() }
    }
    throw 'no Python 3 found. Install it from python.org, tick "Add to PATH", then re-run.'
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $systemPython = Resolve-SystemPython
    Write-Host "Creating venv at $Venv"
    Invoke-Native -Command $systemPython -Arguments @('-m', 'venv', $Venv) `
        -What 'python -m venv'
}
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "venv creation did not produce $VenvPython"
}

# pycairo is the reason the Windows panel is pixel-identical to the mac and
# Linux one instead of a second painter that drifts: PyPI ships win_amd64
# wheels (cp39-cp313) that statically bundle cairo, so there is no MSYS2 step.
Invoke-Native -Command $VenvPython `
    -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip') -What 'pip upgrade'
Invoke-Native -Command $VenvPython `
    -Arguments @('-m', 'pip', 'install', 'pystray', 'pillow', 'pycairo') `
    -What 'pip install'

New-Item -ItemType Directory -Path $Cache -Force | Out-Null

# ------------------------------------------------------- device settings

# This device's settings. The Scheduled Task and the shortcut below are both
# rewritten from scratch on every update (applying an update IS re-running this
# script), so anything edited into them by hand is lost. config.env is the
# durable place. Windows cannot bake env into either artifact -- a task has no
# environment block and a .lnk cannot carry one -- so unlike the Linux and
# macOS installers this one does not splice the settings in at all: the process
# loads them itself at startup (bin/ai-smartbar:_load_windows_env). Rendering
# them here is purely so a bad line is VISIBLE at install time, which is the
# other half of what --print-config buys the other installers.
# Never fatal: a config this script cannot read must still leave a working
# install, so a failure here is reported and stepped over.
$settingsSummary = ''
try {
    $settingsSummary = (& $VenvPython $Launcher --print-config winenv | Out-String)
} catch {
    Write-Warning "could not read config.env: $_"
}
if ($settingsSummary.Trim()) {
    Write-Host 'Settings from config.env (loaded at startup by the app itself):'
    foreach ($line in $settingsSummary.Trim() -split "`r?`n") {
        Write-Host "  $line"
    }
}

# How often to check for a release. The CLI resolves it (env, then config.env,
# then the default, floored at 5 min) and converts to whole minutes, so Task
# Scheduler's minute-resolution arithmetic is unit-tested in
# smartbar/core/update.py rather than open-coded here. 360 min = the documented
# 6 h default, used only if the CLI itself cannot run.
$intervalMinutes = 360
try {
    $printed = (& $VenvPython $Launcher --update-interval minutes | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $printed -match '^\d+$') {
        $intervalMinutes = [int]$printed
    }
} catch {
    Write-Warning "could not resolve the update interval, using $intervalMinutes min"
}
if ($intervalMinutes -lt 1) { $intervalMinutes = 1 }

# ------------------------------------------------------------- updater

function Install-Updater {
    # Prove a non-interactive fetch works before promising updates; skipped
    # when the updater is re-running this script (it just fetched, and a
    # network blip must not turn a good update into a rollback).
    #
    # HONEST LIMITATION: install/linux.sh's install_updater's `env -i ...
    # git ls-remote` probe uses `env -i` to run git with an
    # EMPTY environment, which is what makes its probe airtight -- no
    # credential helper can be reached. There is no `env -i` on Windows, and
    # PowerShell cannot easily launch a child with a cleared environment. Worse,
    # GIT_TERMINAL_PROMPT=0 only suppresses TERMINAL prompts: Git Credential
    # Manager is a GUI helper and will happily pop a window instead. Setting
    # GCM_INTERACTIVE=never covers the current GCM, but an unknown third-party
    # helper could still prompt. So this probe is weaker than the Linux one and
    # a machine that passes it can still hang on a GUI prompt at update time.
    # Narrowing that gap needs a real Windows box to test against.
    if ($env:SMARTBAR_UPDATE_APPLY -ne '1') {
        if (-not (Get-Command 'git' -ErrorAction SilentlyContinue)) {
            Write-Error 'git not found on PATH -- install Git for Windows, then re-run.'
            return $false
        }
        $savedPrompt = $env:GIT_TERMINAL_PROMPT
        $savedGcm = $env:GCM_INTERACTIVE
        $env:GIT_TERMINAL_PROMPT = '0'
        $env:GCM_INTERACTIVE = 'never'
        try {
            & git -C $Repo ls-remote --quiet origin HEAD 2>&1 | Out-Null
            $fetchOk = ($LASTEXITCODE -eq 0)
        } finally {
            $env:GIT_TERMINAL_PROMPT = $savedPrompt
            $env:GCM_INTERACTIVE = $savedGcm
        }
        if ($fetchOk) {
            Write-Host 'Non-interactive fetch OK.'
        } else {
            Write-Error ('cannot fetch origin without a prompt -- add an SSH key ' +
                         'or a stored credential helper, then re-run this installer.')
            return $false
        }
    }

    # WRITE half of the write/read pair documented on Get-InstalledChannel.
    # The launcher path is quoted because the checkout may sit under a path
    # with a space; --channel is a literal so the read-back regex can find it.
    $arguments = '"{0}" --update --channel {1}' -f $Launcher, $Channel
    $action = New-ScheduledTaskAction -Execute $VenvPythonW `
        -Argument $arguments -WorkingDirectory $Repo
    # -Once + RepetitionInterval is the only way to express "every N minutes"
    # in Task Scheduler; the 2-minute offset mirrors the systemd unit's
    # OnBootSec=2min so a login does not fire a fetch immediately.
    $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2)) `
        -RepetitionInterval (New-TimeSpan -Minutes $intervalMinutes)
    # StartWhenAvailable is systemd's Persistent=true: run a missed check once
    # the machine is back rather than skipping the window entirely.
    # IgnoreNew matches the runner's own file lock -- two overlapping update
    # passes are pointless, and the second would just exit on the lock anyway.
    $taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew
    # -Force makes this idempotent: it replaces an existing registration
    # instead of throwing, which matters because the updater re-runs this
    # script on every single release.
    Register-ScheduledTask -TaskName $TaskName -Action $action `
        -Trigger $trigger -Settings $taskSettings -Force | Out-Null
    Write-Host ("Update task registered (channel=$Channel, every " +
                "$intervalMinutes min).")
    return $true
}

if (-not $NoAutoUpdate) {
    $installed = $false
    try {
        $installed = Install-Updater
    } catch {
        Write-Warning "could not register the update task: $_"
    }
    if (-not $installed) {
        Write-Warning 'this device will not self-update (see above).'
    }
} else {
    # -NoAutoUpdate on a device that already had a task must actually remove
    # it, or the flag silently does nothing on re-install.
    Remove-Updater
}

# ------------------------------------------------------------ autostart

# pythonw.exe, not python.exe: the tray polls every 60 s and each poll shells
# out to cswap, so a console-attached host would flash a window continuously.
# For bring-up, run venv\Scripts\python.exe bin\ai-smartbar by hand instead --
# see docs/windows-bring-up.md.
# The shortcut's icon. Never fatal: System.Drawing is present on every
# Windows PowerShell, but this runs unattended from the updater too, and a
# missing icon is a cosmetic loss where a thrown exception would cost the
# user their startup entry. GetHicon() caps out around 256px, which is why
# the 1024 asset is resized first rather than converted straight across.
try {
    if (Test-Path -LiteralPath $IconPng) {
        Add-Type -AssemblyName System.Drawing
        $src = New-Object System.Drawing.Bitmap $IconPng
        $sized = New-Object System.Drawing.Bitmap $src, 256, 256
        $icon = [System.Drawing.Icon]::FromHandle($sized.GetHicon())
        $fs = [System.IO.File]::Create($IconIco)
        $icon.Save($fs)
        $fs.Close()
        $src.Dispose(); $sized.Dispose()
    }
} catch {
    Write-Warning "Could not build $IconIco - the shortcut keeps the default icon."
}

$wscript = New-Object -ComObject WScript.Shell
$link = $wscript.CreateShortcut($Shortcut)
$link.TargetPath = $VenvPythonW
$link.Arguments = '"{0}"' -f $Launcher
$link.WorkingDirectory = $Repo
$link.Description = 'Claude usage limits in the system tray'
if (Test-Path -LiteralPath $IconIco) { $link.IconLocation = "$IconIco,0" }
$link.Save()

# --------------------------------------------------------- exactly one

# Stop BEFORE start, and stop before anything else touches the checkout: a
# running executable cannot be replaced on Windows, and two trays would fight
# over the same cache files.
Stop-Tray
Start-Sleep -Seconds 1
Start-Process -FilePath $VenvPythonW -ArgumentList ('"{0}"' -f $Launcher) `
    -WorkingDirectory $Repo | Out-Null
Start-Sleep -Seconds 2
if (@(Get-TrayProcess).Count -gt 0) {
    Write-Host 'ai-smartbar is running.'
} else {
    Write-Error "FAILED to start -- check $Cache\tray.log"
    exit 1
}
