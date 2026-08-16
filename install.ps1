<#
    Wires Claude Code up to speak.

    Writes config.json (paths for this machine), installs the Stop hook into a
    project's .claude\settings.json, and drops the /voice slash command next to it.

        .\install.ps1                                  # this repo's own folder as the project
        .\install.ps1 -ProjectDir C:\code\my-project   # speak in that project
        .\install.ps1 -StudioDir "D:\qwen-tts-studio"  # if it is not auto-found
        .\install.ps1 -WhatIf                          # show, do not write

    Hooks are read at session start: restart Claude Code afterwards.
#>

param(
    [string]$ProjectDir = $PSScriptRoot,
    [string]$StudioDir,
    [string]$PythonExe,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

function Say($msg, $colour = "Gray") { Write-Host $msg -ForegroundColor $colour }

# Out-File -Encoding utf8 writes a BOM in PowerShell 5.1, and a BOM at the top
# of settings.json makes a strict JSON parser reject the whole file -- which
# looks exactly like "hooks silently do nothing". Write it clean.
function Write-Utf8($path, $text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

# --- python ---------------------------------------------------------------
# The hook is spawned without a shell profile, so a bare "python" is not enough:
# anything installed by conda or the Windows Store alias will not resolve there.
# Record the absolute interpreter path instead.
if (-not $PythonExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Could not find python.exe. Pass -PythonExe C:\path\to\python.exe"
}
if ($PythonExe -match 'WindowsApps') {
    throw "That python ($PythonExe) is the Microsoft Store alias, which will not run from a hook. Install real Python or pass -PythonExe."
}
Say "python        : $PythonExe" "Green"

# --- Qwen-TTS Studio ------------------------------------------------------
if (-not $StudioDir) {
    $guesses = @(
        "$env:USERPROFILE\Downloads\qwen-tts-studio",
        "$env:LOCALAPPDATA\Programs\qwen-tts-studio",
        "$env:ProgramFiles\qwen-tts-studio",
        "C:\qwen-tts-studio"
    )
    $StudioDir = $guesses | Where-Object { Test-Path (Join-Path $_ "runtime\bin\server\jvm.dll") } | Select-Object -First 1
}
if (-not $StudioDir -or -not (Test-Path (Join-Path $StudioDir "runtime\bin\server\jvm.dll"))) {
    throw "Qwen-TTS Studio not found. Pass -StudioDir pointing at the folder containing app\ and runtime\."
}
Say "studio        : $StudioDir" "Green"

$modelDir = Join-Path $env:USERPROFILE ".qwen-tts-studio\models"
$talker = "qwen-talker-1.7b-base-Q8_0.gguf"
if (Test-Path $modelDir) {
    $found = Get-ChildItem $modelDir -Filter "*talker*.gguf" -ErrorAction SilentlyContinue
    if ($found -and -not ($found.Name -contains $talker)) { $talker = $found[0].Name }
    Say "talker model  : $talker" "Green"
} else {
    Say "talker model  : $modelDir not found -- download a model in Studio first" "Yellow"
}

# --- config.json ----------------------------------------------------------
$configPath = Join-Path $repo "config.json"
if (Test-Path $configPath) {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    Say "config.json   : updating paths, keeping your settings"
} else {
    $cfg = Get-Content (Join-Path $repo "config.example.json") -Raw | ConvertFrom-Json
    $cfg.PSObject.Properties.Remove("_comment")
    $cfg.PSObject.Properties.Remove("_extraVoicesDirs")
    Say "config.json   : creating"
}
$cfg.studioDir = $StudioDir
$cfg.modelDir = $modelDir
$cfg.talker = $talker

if (-not $WhatIf) {
    Write-Utf8 $configPath ($cfg | ConvertTo-Json -Depth 10)
}

# --- the Stop hook --------------------------------------------------------
$claudeDir = Join-Path $ProjectDir ".claude"
$settingsPath = Join-Path $claudeDir "settings.json"
$hookScript = Join-Path $repo "speak_hook.py"

# Quote only when needed: a command that begins with a quote sends cmd.exe into
# its own quirky parsing rules, and unquoted is fine when nothing has a space.
$command = if ("$PythonExe$hookScript" -match '\s') { "`"$PythonExe`" `"$hookScript`"" }
           else { "$PythonExe $hookScript" }

# On an object with no properties at all, PSObject.Properties.Name is null in
# PowerShell 5.1 and calling .Contains() on it throws -- which is precisely the
# fresh-install case. Ask the property bag directly instead.
function Has-Prop($obj, $name) { return $null -ne $obj.PSObject.Properties[$name] }

if (Test-Path $settingsPath) {
    $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
} else {
    $settings = [PSCustomObject]@{}
}
if (-not (Has-Prop $settings "hooks")) {
    $settings | Add-Member -NotePropertyName hooks -NotePropertyValue ([PSCustomObject]@{})
}

# Stop catches the finished answer; PreToolUse catches the short lines said
# mid-work, which Stop never sees because the turn has not ended.
foreach ($event in @("Stop", "PreToolUse")) {
    # PreToolUse entries are matched against the tool name and want a "matcher";
    # Stop fires once per turn and takes none. An entry missing the field its
    # event expects can invalidate the whole hooks block, silencing both.
    if ($event -eq "PreToolUse") {
        $entry = [PSCustomObject]@{
            matcher = "*"
            hooks = @([PSCustomObject]@{ type = "command"; command = $command; timeout = 15 })
        }
    } else {
        $entry = [PSCustomObject]@{
            hooks = @([PSCustomObject]@{ type = "command"; command = $command; timeout = 15 })
        }
    }

    # Keep any hooks on this event that are not ours; replace ours if present.
    $existing = @()
    if (Has-Prop $settings.hooks $event) {
        $existing = @($settings.hooks.$event | Where-Object {
            -not ($_.hooks | Where-Object { $_.command -like "*speak_hook.py*" })
        })
    }
    $merged = @($existing) + @($entry)

    if (Has-Prop $settings.hooks $event) {
        $settings.hooks.$event = $merged
    } else {
        $settings.hooks | Add-Member -NotePropertyName $event -NotePropertyValue $merged
    }
}

Say "hook command  : $command" "Green"
Say "hook events   : Stop (answers), PreToolUse (narration)" "Green"
Say "settings      : $settingsPath"

if (-not $WhatIf) {
    New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null
    Write-Utf8 $settingsPath ($settings | ConvertTo-Json -Depth 10)

    $cmdDir = Join-Path $claudeDir "commands"
    New-Item -ItemType Directory -Force -Path $cmdDir | Out-Null
    $template = (Get-Content (Join-Path $repo "commands\voice.md") -Raw).Replace("__PYTHON__", $PythonExe).Replace("__REPO__", $repo.Replace('\','/'))
    Write-Utf8 (Join-Path $cmdDir "voice.md") $template
    Say "slash command : $(Join-Path $cmdDir 'voice.md')" "Green"
}

Say ""
if ($WhatIf) {
    Say "-WhatIf: nothing was written." "Yellow"
} else {
    Say "Done. Restart Claude Code -- hooks are read at session start." "Green"
    Say "Then: /voice on" "Green"
}
