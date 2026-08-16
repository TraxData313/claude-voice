<#
    Wires Claude Code up to speak.

    Writes config.json (paths for this machine), installs the Stop hook into a
    project's .claude\settings.json, drops the /voice slash command next to it,
    puts the note about writing to be heard into ~\.claude\CLAUDE.md, and puts a
    shortcut to the panel on the Desktop.

    No administrator rights, by design: every path it writes to belongs to the
    user already, and Program Files is only ever read from, looking for Studio.
    Keep the repo somewhere you own -- config.json lives beside the code.

        .\install.ps1                                  # this repo's own folder as the project
        .\install.ps1 -ProjectDir C:\code\my-project   # speak in that project
        .\install.ps1 -StudioDir "D:\qwen-tts-studio"  # if it is not auto-found
        .\install.ps1 -NoShortcut                      # skip the Desktop icon
        .\install.ps1 -NoNote                          # leave CLAUDE.md alone
        .\install.ps1 -UpdateChecks                    # allow one look a week for a new version
        .\install.ps1 -WhatIf                          # show, do not write

    Hooks are read at session start: restart Claude Code afterwards.
#>

param(
    [string]$ProjectDir,
    [string]$StudioDir,
    [string]$PythonExe,
    [switch]$NoShortcut,
    [switch]$NoNote,
    [switch]$UpdateChecks,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

# There is no script file when this is piped into Invoke-Expression, so
# $PSScriptRoot is empty -- and that pipe is the documented way past an
# execution policy that refuses to run script files at all. Stand where the
# user is standing instead.
$repo = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
if (-not $ProjectDir) { $ProjectDir = $repo }

function Say($msg, $colour = "Gray") { Write-Host $msg -ForegroundColor $colour }

# Out-File -Encoding utf8 writes a BOM in PowerShell 5.1, and a BOM at the top
# of settings.json makes a strict JSON parser reject the whole file -- which
# looks exactly like "hooks silently do nothing". Write it clean.
function Write-Utf8($path, $text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

# The mirror of it, and the same bug from the other end: Get-Content -Raw in
# PowerShell 5.1 decodes a file with no BOM as the system ANSI codepage, so an
# em dash read out of a UTF-8 file and written straight back comes out as three
# characters of nonsense. Every file this script reads was written as UTF-8.
function Read-Utf8($path) {
    return [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
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
# voice_cli writes config.json back holding only what differs from its own
# defaults, so a path we wrote last time is simply absent the next time if it
# happened to match. Assigning to a property that is not there throws, which
# turned a second run of the installer into an error. Set it either way.
function Set-Prop($obj, $name, $value) {
    if ($null -ne $obj.PSObject.Properties[$name]) { $obj.$name = $value }
    else { $obj | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}

$configPath = Join-Path $repo "config.json"
if (Test-Path $configPath) {
    $cfg = Read-Utf8 $configPath | ConvertFrom-Json
    Say "config.json   : updating paths, keeping your settings"
} else {
    $cfg = Read-Utf8 (Join-Path $repo "config.example.json") | ConvertFrom-Json
    $cfg.PSObject.Properties.Remove("_comment")
    $cfg.PSObject.Properties.Remove("_extraVoicesDirs")
    Say "config.json   : creating"
}
Set-Prop $cfg "studioDir" $StudioDir
Set-Prop $cfg "modelDir" $modelDir
Set-Prop $cfg "talker" $talker

# Whether it may look for a newer version of itself. Off unless asked, because
# everything else here runs on this machine and tells nobody about it, and that
# is a promise worth more than the convenience. Said out loud either way: this
# is the moment the user is deciding what this thing gets to do.
if ($UpdateChecks) {
    Set-Prop $cfg "updateCheck" $true
    Say "update checks : on -- one look a week at GitHub, logged in logs\update.log" "Green"
} else {
    Say "update checks : off -- nothing is contacted. '/voice update' looks when asked" "Green"
}

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
    $settings = Read-Utf8 $settingsPath | ConvertFrom-Json
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
    $template = (Read-Utf8 (Join-Path $repo "commands\voice.md")).Replace("__PYTHON__", $PythonExe).Replace("__REPO__", $repo.Replace('\','/'))
    Write-Utf8 (Join-Path $cmdDir "voice.md") $template
    Say "slash command : $(Join-Path $cmdDir 'voice.md')" "Green"
}

# --- the note every session reads -----------------------------------------
# Without this the whole TL;DR contract is a secret: a session has no way to
# know it is being listened to, so it writes for the screen and the listener
# gets a wall of paths read out at them. This is the part that makes an answer
# worth hearing, so it is installed rather than left in the documentation.
#
# Always the user-level file, whichever project the hooks went into: the voice
# is one setting shared by every session, and voice_lib.announce_voice keeps
# the current voice's name up to date in this same block from now on.
if (-not $NoNote) {
    $notePath = Join-Path $env:USERPROFILE ".claude\CLAUDE.md"
    $note = (Read-Utf8 (Join-Path $repo "speaking-notes.md")).TrimEnd() + "`n"
    $open, $close = "<!-- claude-voice -->", "<!-- /claude-voice -->"

    $existing = if (Test-Path $notePath) { Read-Utf8 $notePath } else { "" }
    $start, $end = $existing.IndexOf($open), $existing.IndexOf($close)
    if ($start -ge 0 -and $end -gt $start) {
        # Replace our block and leave every word around it alone.
        $merged = $existing.Substring(0, $start) + $note + $existing.Substring($end + $close.Length)
        $what = "updated"
    } else {
        $merged = if ($existing.Trim()) { $existing.TrimEnd() + "`n`n" + $note } else { $note }
        $what = "added to"
    }

    Say "spoken-answer : $what $notePath" "Green"
    if (-not $WhatIf) {
        New-Item -ItemType Directory -Force -Path (Split-Path $notePath) | Out-Null
        Write-Utf8 $notePath $merged
        # Fills in the current-voice line inside the block just written, using
        # the same path the CLI and the panel take, so the three cannot drift.
        $voice = if (Has-Prop $cfg "voice") { $cfg.voice } else { "abby" }
        & $PythonExe (Join-Path $repo "voice_cli.py") set $voice | Out-Null
    }
}

# --- something to double click --------------------------------------------
# The panel is the only part of this with a window, and until now the only way
# to open one was to type a command -- which is a poor answer to "I closed it,
# how do I get it back".
if (-not $NoShortcut) {
    & (Join-Path $repo "make_shortcut.ps1") -PythonExe $PythonExe -WhatIf:$WhatIf
}

Say ""
if ($WhatIf) {
    Say "-WhatIf: nothing was written." "Yellow"
} else {
    Say "Done. Restart Claude Code -- hooks are read at session start." "Green"
    Say "Then: /voice on" "Green"
}
