# Windows stage-one installer for Ubersight.
#
# This is the PowerShell counterpart of install.sh. It selects a Python
# interpreter and hands every argument to scripts/install-stage-2.py, which
# performs the install. It runs in Windows PowerShell 5.1 and PowerShell 7.
#
# Usage, from the checkout:
#   .\install.ps1                  # per-user install; launchers in ~\.local\bin
#   .\install.ps1 --mode dev       # repository .venv for development
#   .\install.ps1 --help           # all stage-two options
#
# Interpreter selection, first match wins:
#   1. THEKNOWLEDGE_BOOTSTRAP_PYTHON, when set;
#   2. the pinned runtime from python-environments.json via the `py` launcher;
#   3. the newest Python 3 the `py` launcher reports;
#   4. `python` or `python3` on PATH, skipping the Microsoft Store aliases.
# Each candidate must run and report Python 3.9 or newer. Stage two enforces
# the stricter runtime floor where it applies.
#
# Stage two runs through the same command that passed the version probe. The
# interpreter path is never read back from Python's output: piped output goes
# through code-page conversions that garble non-ASCII paths, such as a
# per-user Python under C:\Users\<name> for a name with accented letters.
#
# On a machine whose execution policy blocks local scripts, run:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = 'Stop'
# Native commands report failure through $LASTEXITCODE, which this script
# checks itself. PowerShell 7.3+ can also turn a nonzero exit code into an
# error when this preference is enabled, so it is switched off here.
$PSNativeCommandUseErrorActionPreference = $false
Set-StrictMode -Version 3.0

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$stageTwo = Join-Path $repoRoot 'scripts\install-stage-2.py'

function Get-PinnedRuntimeVersion {
    # Return the pinned runtime as major.minor, or $null when unreadable.
    $configPath = Join-Path $repoRoot 'python-environments.json'
    try {
        $config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
        $parts = ([string]$config.runtime.base_version).Split('.')
        if ($parts.Length -ge 2) { return '{0}.{1}' -f $parts[0], $parts[1] }
    } catch {
        return $null
    }
    return $null
}

function Get-CommandPrefix {
    # Return the arguments that follow the executable in a candidate command.
    param([string[]]$Command)
    if ($Command.Length -gt 1) { return $Command[1..($Command.Length - 1)] }
    return @()
}

function Test-Interpreter {
    # Return whether the candidate command runs Python 3.9 or newer.
    #
    # Only the ASCII result of the version check crosses stdout.
    param([string[]]$Command)
    # Windows PowerShell 5.1 turns native stderr into error records, and the
    # script-wide 'Stop' would make the first one fatal. A working
    # interpreter that prints a warning to stderr, for example about a
    # broken .pth file, would then be rejected. $LASTEXITCODE decides
    # instead. This assignment is local to the function.
    $ErrorActionPreference = 'Continue'
    $executable = $Command[0]
    $prefix = @(Get-CommandPrefix -Command $Command)
    $probe = 'import sys; print(sys.version_info >= (3, 9))'
    try {
        $output = & $executable @prefix -c $probe 2>$null
    } catch {
        # The executable is missing or cannot be started.
        return $false
    }
    if ($LASTEXITCODE -ne 0) { return $false }
    $lines = @($output | ForEach-Object { ([string]$_).Trim() })
    return ($lines.Length -gt 0 -and $lines[$lines.Length - 1] -eq 'True')
}

$candidates = New-Object System.Collections.Generic.List[object]
if ($env:THEKNOWLEDGE_BOOTSTRAP_PYTHON) {
    $candidates.Add(@($env:THEKNOWLEDGE_BOOTSTRAP_PYTHON))
}
$pinned = Get-PinnedRuntimeVersion
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    if ($pinned) { $candidates.Add(@($launcher.Source, "-$pinned")) }
    $candidates.Add(@($launcher.Source, '-3'))
}
foreach ($name in @('python', 'python3')) {
    # Get-Command lists every match in PATH order. The WindowsApps entries
    # are Store aliases that open the Store instead of running Python when
    # no interpreter is installed, so they are dropped before the first
    # match is taken; a real interpreter later on PATH still counts.
    $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
        Select-Object -First 1
    if ($command) {
        $candidates.Add(@($command.Source))
    }
}

$selected = $null
foreach ($candidate in $candidates) {
    if (Test-Interpreter -Command $candidate) {
        $selected = $candidate
        break
    }
}
if (-not $selected) {
    Write-Host '[install] FAIL: no Python 3.9+ interpreter was found.'
    Write-Host '[install] Install Python 3.12 or newer, for example with'
    Write-Host '[install]   winget install Python.Python.3.12'
    Write-Host '[install] then rerun .\install.ps1.'
    exit 1
}
Write-Host "[install] Python: $($selected -join ' ')"

$env:THEKNOWLEDGE_MANAGED_INSTALL_STAGE1 = '1'
$executable = $selected[0]
$prefix = @(Get-CommandPrefix -Command $selected)
# Stage two and its children, such as git and pip, write progress and
# notices to stderr. Under 'Stop', Windows PowerShell 5.1 would abort on the
# first such line whenever the installer's output is redirected, for example
# with *> install.log or 2>&1 | Tee-Object. The exit code decides instead.
$ErrorActionPreference = 'Continue'
& $executable @prefix $stageTwo @args
exit $LASTEXITCODE
