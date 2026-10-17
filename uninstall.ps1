<#
    Kept so older links and docs still land somewhere. Uninstalling is Settings >
    Apps > claude-voice > Uninstall now, which runs installer.py on the
    program's own signed Python; this script finds that same command and runs it.

        .\uninstall.ps1            # asks first, as Settings > Apps does
        .\uninstall.ps1 -WhatIf    # says what it would remove, removes nothing
        .\uninstall.ps1 -Yes       # no questions; your settings and voices are kept

    A developer's checkout is never uninstalled: it is somebody's own folder.
    Its hooks and registration come out with `python voice_cli.py hooks remove`
    and `python voice_cli.py unregister`, and the folder is theirs to delete.
#>

param(
    [switch]$Yes,
    [switch]$WhatIf,
    [switch]$KeepPython
)

$ErrorActionPreference = "Stop"
$here = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }

if (Test-Path (Join-Path $here ".git")) {
    Write-Host "This is a git checkout ($here), which is never uninstalled."
    Write-Host "Its Claude Code hooks: python voice_cli.py hooks remove"
    Write-Host "Its registration for games: python voice_cli.py unregister"
    Write-Host "The installed program, if there is one: Settings > Apps > claude-voice."
    exit 1
}

# The program this file belongs to: <program>\app\uninstall.ps1, or the one
# Settings > Apps names when this is a loose copy.
$root = Split-Path $here
if (-not (Test-Path (Join-Path $root "install.json"))) {
    $entry = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\claude-voice" -ErrorAction SilentlyContinue
    $root = if ($entry) { $entry.InstallLocation } else { $null }
}
if (-not $root -or -not (Test-Path (Join-Path $root "install.json"))) {
    Write-Host "No installed claude-voice found (no install.json beside an app folder, and none in Settings > Apps)."
    Write-Host "An install from before 2.0 is removed by running install.ps1 once (it moves it to the new"
    Write-Host "layout) and then uninstalling from Settings > Apps."
    exit 1
}

$python = Join-Path $root "runtime\python.exe"
$argv = @((Join-Path $root "app\installer.py"), "uninstall", "--root", $root)
if ($WhatIf) { $argv += "--dry-run" }
if ($Yes)    { $argv += "--quiet" }
& $python @argv
if ($PSCommandPath) { exit $LASTEXITCODE }
