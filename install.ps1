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
# On a machine whose execution policy blocks local scripts, run:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = 'Stop'
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

function Resolve-Interpreter {
    # Return the absolute interpreter path when the command runs Python 3.9+.
    param([string[]]$Command)
    $executable = $Command[0]
    $prefix = @()
    if ($Command.Length -gt 1) { $prefix = $Command[1..($Command.Length - 1)] }
    $probe = 'import sys; print(sys.executable); print(sys.version_info >= (3, 9))'
    try {
        $output = & $executable @prefix -c $probe 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0 -or -not $output) { return $null }
    $lines = @($output)
    if ($lines.Length -ge 2 -and $lines[1].Trim() -eq 'True') {
        return $lines[0].Trim()
    }
    return $null
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
    $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    # The WindowsApps entries are Store aliases that open the Store instead
    # of running Python when no interpreter is installed.
    if ($command -and $command.Source -notlike '*\WindowsApps\*') {
        $candidates.Add(@($command.Source))
    }
}

$python = $null
foreach ($candidate in $candidates) {
    $python = Resolve-Interpreter -Command $candidate
    if ($python) { break }
}
if (-not $python) {
    Write-Host '[install] FAIL: no Python 3.9+ interpreter was found.'
    Write-Host '[install] Install Python 3.12 or newer, for example with'
    Write-Host '[install]   winget install Python.Python.3.12'
    Write-Host '[install] then rerun .\install.ps1.'
    exit 1
}
Write-Host "[install] Python: $python"

$env:THEKNOWLEDGE_MANAGED_INSTALL_STAGE1 = '1'
& $python $stageTwo @args
exit $LASTEXITCODE
