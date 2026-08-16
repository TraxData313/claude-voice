<#
    Puts claude-voice on the Desktop, so the panel can be opened without a
    terminal.

        .\make_shortcut.ps1                     # Desktop
        .\make_shortcut.ps1 -StartMenu          # and in the Start menu
        .\make_shortcut.ps1 -Remove             # take it away again
        .\make_shortcut.ps1 -WhatIf             # say what it would do

    install.ps1 runs this for you; it is here on its own because a shortcut is
    the sort of thing you want back after tidying your Desktop, and that should
    not mean reinstalling anything.

    The shortcut opens the panel, not the engine. The panel is instant, and it
    has a button to load the engine when the engine is not up -- which is the
    right way round, because loading the model takes the better part of a
    minute and is not what you want from a double click that was meant to show
    you what is being said.
#>

param(
    [string]$PythonExe,
    [switch]$StartMenu,
    [switch]$Remove,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
# What the window calls itself, so the icon and the taskbar entry agree.
$name = "Abby for Claude.lnk"
# What it used to be called. Left behind, it is a second icon for the same app.
$was = "claude-voice.lnk"

function Say($msg, $colour = "Gray") { Write-Host $msg -ForegroundColor $colour }

$places = @([Environment]::GetFolderPath("Desktop"))
if ($StartMenu) {
    $places += Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
}

if ($Remove) {
    foreach ($dir in $places) {
        foreach ($leaf in @($name, $was)) {
            $path = Join-Path $dir $leaf
            if (Test-Path $path) {
                if (-not $WhatIf) { Remove-Item $path -Force }
                Say "removed       : $path" "Yellow"
            }
        }
    }
    return
}

# --- what it points at ----------------------------------------------------
# pythonw, not python: python.exe would leave a console window sitting behind
# the panel for as long as it is open, which is both ugly and easy to close by
# accident, taking the panel with it.
if (-not $PythonExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Could not find python.exe. Pass -PythonExe C:\path\to\python.exe"
}
$pythonw = Join-Path (Split-Path $PythonExe) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $PythonExe }

$panel = Join-Path $repo "panel.py"
$icon = Join-Path $repo "docs\icons\abby.ico"

Say "opens         : $panel"
Say "with          : $pythonw"

foreach ($dir in $places) {
    $path = Join-Path $dir $name
    if ($WhatIf) {
        Say "would write   : $path" "Yellow"
        continue
    }
    # Sweep up the old name, so an upgrade does not leave two icons that open
    # the same window.
    $stale = Join-Path $dir $was
    if (Test-Path $stale) {
        Remove-Item $stale -Force
        Say "replaced      : $stale" "Yellow"
    }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $pythonw
    # Quoted, because this one is read by the shell rather than by cmd.exe, and
    # a repo path with a space in it would otherwise arrive as two arguments.
    $link.Arguments = "`"$panel`""
    $link.WorkingDirectory = $repo
    $link.Description = "Abby, and what she is saying -- the claude-voice panel"
    if (Test-Path $icon) { $link.IconLocation = "$icon,0" }
    $link.Save()
    Say "shortcut      : $path" "Green"
}
