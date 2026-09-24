<#
    Takes claude-voice off this machine again -- everything the setup put here, and nothing else.

        .\uninstall.ps1              # shows what it will remove, and asks
        .\uninstall.ps1 -WhatIf      # only says what it would remove
        .\uninstall.ps1 -Yes         # does not ask
        .\uninstall.ps1 -KeepPython  # leaves the Python it installed

    This is what "Uninstall" in Settings > Apps runs. It reads installed.json, the list setup.ps1
    keeps of what IT installed, as against what it found and used: a Python, a Qwen-TTS Studio or
    a Breeze that was here before is never taken, and neither is anything belonging to another
    copy of claude-voice on the same machine.

    A git working copy is refused outright. That is somebody's own checkout -- their voices, their
    settings, their history -- and not an install to be cleared away.
#>

param(
    [switch]$Yes,
    [switch]$WhatIf,
    [switch]$KeepPython
)

$ErrorActionPreference = "Continue"
$repo = $PSScriptRoot
$log = Join-Path $env:TEMP "claude-voice-uninstall.log"
Set-Content -Path $log -Value "claude-voice uninstall, $(Get-Date)" -Encoding UTF8

function Say($msg) { Write-Host $msg; Add-Content -Path $log -Value $msg -Encoding UTF8 }

Add-Type -AssemblyName System.Windows.Forms
function Tell($text, $buttons = "OK", $icon = "Information") {
    if ($WhatIf) { Say $text; return "OK" }
    return [System.Windows.Forms.MessageBox]::Show($text, "claude-voice", $buttons, $icon).ToString()
}

function Same($a, $b) {
    if (-not $a -or -not $b) { return $false }
    try {
        return [System.IO.Path]::GetFullPath($a).TrimEnd('\') -ieq [System.IO.Path]::GetFullPath($b).TrimEnd('\')
    } catch { return $false }
}

# --- never a working copy -------------------------------------------------
if (Test-Path (Join-Path $repo ".git")) {
    Tell ("This copy of claude-voice is a git working copy:`n$repo`n`n" +
          "It is somebody's own checkout, so it is not uninstalled. Delete the folder by hand if " +
          "that is really what you want.") "OK" "Warning" | Out-Null
    exit 1
}

# --- what the setup installed ---------------------------------------------
$claims = $null
$claimsPath = Join-Path $repo "installed.json"
if (Test-Path $claimsPath) {
    try { $claims = [System.IO.File]::ReadAllText($claimsPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json } catch { }
}
function Claimed($name) {
    if ($claims -and $claims.PSObject.Properties[$name]) { return $claims.$name }
    return $null
}

$plan = New-Object System.Collections.ArrayList   # each: @{ What = "..."; Do = { ... } }
function Plan($what, [scriptblock]$do) { [void]$plan.Add(@{ What = $what; Do = $do }) }

# The Breeze folder, the Studio and the models, when they are ours.
$breeze = Claimed "breeze"
if ($breeze -and (Test-Path $breeze)) {
    Plan "Breeze, the engine that laughs (about 11 GB): $breeze" { Remove-Item -LiteralPath $breeze -Recurse -Force }
}
$studio = Claimed "studio"
if ($studio -and (Test-Path $studio)) {
    Plan "Qwen-TTS Studio, the speech engine: $studio" { Remove-Item -LiteralPath $studio -Recurse -Force }
}
$models = Claimed "models"
if ($models -and (Test-Path $models)) {
    $files = @("qwen-talker-1.7b-base-Q8_0.gguf", "qwen-tokenizer-12hz-Q8_0.gguf") |
             ForEach-Object { Join-Path $models $_ } | Where-Object { Test-Path $_ }
    if ($files) {
        Plan "the Qwen voice model (about 2 GB): $models" {
            foreach ($f in $files) { Remove-Item -LiteralPath $f -Force }
            # Only folders left empty -- Studio's own app may keep other models beside ours.
            foreach ($d in @($models, (Split-Path $models))) {
                if ((Test-Path $d) -and -not (Get-ChildItem -LiteralPath $d -Force)) { Remove-Item -LiteralPath $d -Force }
            }
        }
    }
}

# Pocket's weights, fetched by the engine the first time it loaded.
if (Claimed "pocket") {
    $hub = if ($env:HF_HOME) { Join-Path $env:HF_HOME "hub" } else { Join-Path $env:USERPROFILE ".cache\huggingface\hub" }
    $weights = @(Get-ChildItem -LiteralPath $hub -Directory -Filter "models--kyutai--pocket*" -ErrorAction SilentlyContinue)
    if ($weights) {
        Plan "Pocket's voice model: $hub\models--kyutai--pocket*" { foreach ($w in $weights) { Remove-Item -LiteralPath $w.FullName -Recurse -Force } }
    }
}

# Python, found through its own uninstaller: the per-user installer lists itself under HKCU with
# a quiet uninstall string, and running that is the only clean way to take it away.
$python = Claimed "python"
$pythonUninstall = $null
if ($python -and -not $KeepPython) {
    $home_ = Split-Path $python
    $entries = Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue |
               ForEach-Object { Get-ItemProperty $_.PSPath } |
               Where-Object { $_.DisplayName -like "Python 3*" -and $_.QuietUninstallString -and $_.BundleCachePath }
    # The bundle entry is the whole of that Python; its version is in the folder name (Python313).
    $tag = Split-Path $home_ -Leaf
    $pythonUninstall = $entries | Where-Object { $tag -match ("Python" + ($_.DisplayVersion -replace '^(\d+)\.(\d+).*', '$1$2')) } |
                       Select-Object -First 1
    if ($pythonUninstall) {
        Plan "$($pythonUninstall.DisplayName), installed for claude-voice" {
            $cmd = $pythonUninstall.QuietUninstallString
            Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmd -Wait -WindowStyle Hidden
        }
    }
}

# Shortcuts, only the ones that open THIS copy.
$shell = New-Object -ComObject WScript.Shell
$places = @([Environment]::GetFolderPath("Desktop"), (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))
foreach ($dir in $places) {
    foreach ($leaf in @("Abby for Claude.lnk", "claude-voice.lnk")) {
        $lnk = Join-Path $dir $leaf
        if (-not (Test-Path $lnk)) { continue }
        $s = $shell.CreateShortcut($lnk)
        if (("$($s.TargetPath) $($s.Arguments) $($s.WorkingDirectory)") -like "*$repo*") {
            $target = $lnk
            Plan "the shortcut: $lnk" { Remove-Item -LiteralPath $target -Force }.GetNewClosure()
        }
    }
}

# Claude Code's hooks and /voice, when this install was made for Claude Code and wired into a
# project outside its own folder (inside it, they go with the folder).
if ((Claimed "claude") -eq $true) {
    $project = Claimed "project"
    if ($project -and -not (Same $project $repo)) {
        $settings = Join-Path $project ".claude\settings.json"
        if (Test-Path $settings) {
            Plan "claude-voice's speech hooks in $settings" {
                $fwd = $repo.Replace('\', '/')
                $json = [System.IO.File]::ReadAllText($settings, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
                if ($json.hooks) {
                    foreach ($event in @($json.hooks.PSObject.Properties.Name)) {
                        $kept = @($json.hooks.$event | Where-Object {
                            -not ($_.hooks | Where-Object { $_.command -like "*$fwd*speak_hook.py*" })
                        })
                        if ($kept.Count -eq 0) { $json.hooks.PSObject.Properties.Remove($event) }
                        else { $json.hooks.$event = $kept }
                    }
                }
                [System.IO.File]::WriteAllText($settings, ($json | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding $false))
                $cmd = Join-Path $project ".claude\commands\voice.md"
                if ((Test-Path $cmd) -and ((Get-Content $cmd -Raw) -like "*$fwd*")) { Remove-Item -LiteralPath $cmd -Force }
            }
        }
    }
}

# The folder chosen for the big things, once they are out of it -- only if nothing else is left.
$data = Claimed "data"
if ($data -and (Test-Path $data)) {
    Plan "the voice-files folder, once empty: $data" {
        if (-not (Get-ChildItem -LiteralPath $data -Force -ErrorAction SilentlyContinue)) { Remove-Item -LiteralPath $data -Force }
    }
}

# Where the game and the panel look for it, when it points here.
$whereDir = Join-Path $env:LOCALAPPDATA "claude-voice"
$wherePath = Join-Path $whereDir "where.json"
if (Test-Path $wherePath) {
    try { $root = ([System.IO.File]::ReadAllText($wherePath) | ConvertFrom-Json).root } catch { $root = $null }
    if (Same $root $repo) {
        Plan "the note that tells games where it is: $wherePath" {
            Remove-Item -LiteralPath $wherePath -Force
            foreach ($f in "setup-status.json", "setup-cancel") {
                $p = Join-Path $whereDir $f
                if (Test-Path $p) { Remove-Item -LiteralPath $p -Force }
            }
            if (-not (Get-ChildItem -LiteralPath $whereDir -Force -ErrorAction SilentlyContinue)) { Remove-Item -LiteralPath $whereDir -Force }
        }
    }
}

$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\claude-voice"
$listed = (Test-Path $key) -and (Same (Get-ItemProperty $key -ErrorAction SilentlyContinue).InstallLocation $repo)
Plan "claude-voice itself, and its voices and settings: $repo" { }

# --- ask ------------------------------------------------------------------
$list = ($plan | ForEach-Object { "  - " + $_.What }) -join "`n"
if ($WhatIf) {
    Say "Would remove:`n$list"
    exit 0
}
if (-not $Yes) {
    $answer = Tell ("Remove claude-voice from this computer?`n`nThis takes away:`n$list`n`n" +
                    "Anything that was here before claude-voice's setup ran is left alone.") "YesNo" "Question"
    if ($answer -ne "Yes") { exit 0 }
}

# --- stop it --------------------------------------------------------------
# Every process running out of this folder or out of our Breeze: the server, the panel, the engine.
Say "stopping claude-voice"
$ours = @($repo) + @($breeze | Where-Object { $_ })
# Not this one, though: its own command line names this folder too, and the first test of this
# stopped itself right here.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessId -ne $PID } |
    Where-Object { $cl = "$($_.CommandLine) $($_.ExecutablePath)"; $ours | Where-Object { $cl -like "*$_*" } } |
    ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch { } }
Start-Sleep -Seconds 2

# --- take it away ---------------------------------------------------------
$failed = @()
foreach ($step in $plan) {
    Say "removing: $($step.What)"
    try { & $step.Do } catch { $failed += "$($step.What): $($_.Exception.Message)"; Say "  failed: $($_.Exception.Message)" }
}
if ($listed) { Remove-Item -Path $key -Recurse -Force -ErrorAction SilentlyContinue }

# The folder last, and from outside it: this script is running out of it. A short-lived cmd
# waits for this process to end, then removes the lot.
$cmd = "ping 127.0.0.1 -n 4 > nul & rmdir /s /q `"$repo`""
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmd -WindowStyle Hidden

if ($failed) {
    Tell ("claude-voice is removed, except for:`n`n" + ($failed -join "`n") +
          "`n`nThose can be deleted by hand. The details are in $log") "OK" "Warning" | Out-Null
} elseif (-not $Yes) {
    Tell "claude-voice is removed. Thank you for having her." | Out-Null
}
