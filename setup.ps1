<#
    Kept so older links, docs and setup programs still land somewhere: the
    install is install.ps1 now, and this hands straight over to it.

        powershell -ExecutionPolicy Bypass -File .\install.ps1

    Before 2.0 this script installed into the folder it stood in, with
    packages going into whatever Python it found. Now there is one install: a
    program folder of its own (%LOCALAPPDATA%\Programs\claude-voice) with a
    private, signed Python from python.org -- see install.ps1 and
    docs/install.md. The options that still mean something are passed on; the
    rest are said and ignored.
#>

param(
    [ValidateSet("qwen", "pocket", "breeze")]
    [string]$Engine,
    [string]$ProjectDir,
    [string]$StudioDir,
    [string]$ModelDir,
    [string]$PythonExe,
    [string]$Version,
    [string]$Build,
    [string]$PythonVersion,
    [switch]$NoShortcut,
    [switch]$NoNote,
    [switch]$NoPanel,
    [switch]$NoClaude,
    [switch]$UpdateChecks,
    [string]$DataDir,
    [switch]$Quiet,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$here = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }

# A setup program from before 2.0 runs this with -Quiet, for a game, in whatever
# folder it found -- once that was a developer's own checkout (2026-10-16). Such
# a folder is somebody's own, and the old program has already copied into it by
# the time this runs, so it is refused here as it always was.
$devCopy = Test-Path (Join-Path $here ".git")
if (-not $devCopy) {
    $reg = Get-ItemProperty "HKCU:\Software\claude-voice" -ErrorAction SilentlyContinue
    if ($reg -and $reg.Kind -eq "dev" -and $reg.InstallDir) {
        try {
            $devCopy = [System.IO.Path]::GetFullPath($reg.InstallDir).TrimEnd([char]92) -ieq
                       [System.IO.Path]::GetFullPath($here).TrimEnd([char]92)
        } catch { }
    }
}
if ($Quiet -and $devCopy) {
    Write-Host "This folder is a developer's own copy of claude-voice ($here), so a quiet install does not touch it." -ForegroundColor Red
    exit 3
}

Write-Host "setup.ps1 is install.ps1 now -- handing over." -ForegroundColor Cyan
$ignored = @()
if ($ProjectDir)    { $ignored += "-ProjectDir (the hooks go in your own Claude Code settings, so every project is heard)" }
if ($StudioDir -or $ModelDir) { $ignored += "-StudioDir/-ModelDir (an existing Studio and model are found where they usually are; -DataDir picks a folder)" }
if ($PythonExe -or $PythonVersion) { $ignored += "-PythonExe/-PythonVersion (it runs on a private Python of its own)" }
if ($NoShortcut)    { $ignored += "-NoShortcut (the Start menu entry is how it is found and removed)" }
if ($NoNote)        { $ignored += "-NoNote (nothing is written into CLAUDE.md any more)" }
if ($UpdateChecks)  { $ignored += "-UpdateChecks (afterwards: voice_cli.py update on)" }
foreach ($i in $ignored) { Write-Host "  ignored: $i" -ForegroundColor Yellow }

$pass = @{}
if ($Engine)   { $pass.Engine = $Engine }
if ($NoPanel)  { $pass.NoPanel = $true }
if ($NoClaude) { $pass.NoClaude = $true }
if ($DataDir)  { $pass.BigData = $DataDir }
if ($Quiet)    { $pass.Quiet = $true }
if ($WhatIf)   { $pass.WhatIf = $true }
& (Join-Path $here "install.ps1") @pass
if ($PSCommandPath) { exit $LASTEXITCODE }
