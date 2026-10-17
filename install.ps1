<#
    Installs claude-voice on this computer as a program: per-user, no administrator.

    From a copy of the project (a git clone, or the zip GitHub offers):

        powershell -ExecutionPolicy Bypass -File .\install.ps1

    Or straight from GitHub, with nothing downloaded first:

        powershell -c "irm https://raw.githubusercontent.com/TraxData313/claude-voice/main/install.ps1 | iex"

    Why the first one names an execution policy: Windows ships with running
    script files switched off, and a script from the internet is refused even
    where they are on. -ExecutionPolicy Bypass on the command line is
    Microsoft's documented way to run one script you chose; it lasts for that
    one PowerShell and changes no setting. The second form needs no flag,
    because a policy is about script files and that pipe never makes one. This
    script itself starts nothing hidden and nothing with that flag.

    What it does, and nothing else:
      1. finds the project: the folder it is in, or GitHub's zip of main
      2. fetches python.org's own Python 3.12 for Windows (about 30 MB, a plain zip),
         checks it against the SHA-256 written below and checks that python.exe
         and pythonw.exe are signed by the Python Software Foundation -- the
         signature Smart App Control lets run -- and unpacks it into
         <program>\runtime. It never touches any other Python on this machine.
      3. hands over to installer.py on that Python, which says every step as it
         goes: the app into <program>\app, the speech engine's packages into
         the private Python, Settings > Apps, the Start menu, the registry key
         games find it by, and Claude Code's hooks.

    Options:
        -Engine pocket|qwen|breeze   which voice engine (default: pocket, on the CPU)
        -NoClaude                    for a game or a program only: nothing of Claude Code's
        -Desktop                     a Desktop shortcut as well as the Start menu one
        -StartWithWindows            open the panel when you log in
        -BigData D:\voices           one folder for the big engine files (Qwen, Breeze)
        -Root <folder>               where the program goes (default %LOCALAPPDATA%\Programs\claude-voice)
        -NoPanel                     do not open the panel at the end
        -WhatIf                      say what it would do, change nothing

    Arguments with the GitHub form:
        & ([scriptblock]::Create((irm https://raw.githubusercontent.com/TraxData313/claude-voice/main/install.ps1))) -Engine qwen

    Running it again updates the program in place and keeps your settings.
    Removing it: Settings > Apps > claude-voice > Uninstall. docs/install.md has the rest.
#>

param(
    [ValidateSet("pocket", "qwen", "breeze")]
    [string]$Engine,
    [string]$Root = (Join-Path $env:LOCALAPPDATA "Programs\claude-voice"),
    [string]$Source,
    [switch]$NoClaude,
    [switch]$Desktop,
    [switch]$StartWithWindows,
    [string]$BigData,
    [switch]$NoPanel,
    [switch]$NoStart,
    [switch]$Quiet,
    [switch]$WhatIf,
    # For a throwaway install a test makes: its own registry names, data folder,
    # Claude Code folder, shortcut folder and port, so it touches nothing real.
    [string]$Product,
    [string]$DataDir,
    [string]$ClaudeDir,
    [string]$ShortcutDir,
    [int]$Port
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"      # Invoke-WebRequest is many times slower drawing a bar

# --- the Python it runs on, pinned -------------------------------------------
# python.org's package for its own install manager: the full Python, tkinter
# included, as a zip. The hash is the one python.org publishes for it
# (https://www.python.org/ftp/python/index-windows.json). runtime.json in the
# project says the same three things, and test_installer.py checks they agree.
$PythonVersion = "3.12.10"
$PythonUrl     = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.zip"
$PythonSha256  = "8649692de846c56a7189d6dae5c322ab20deb1b5908b6f39426b62a36f39415d"
$PythonSigner  = "Python Software Foundation"
$ZipUrl        = "https://github.com/TraxData313/claude-voice/archive/refs/heads/main.zip"

function Say($label, $text, $colour = "Gray") {
    Write-Host ("{0,-14}: {1}" -f $label, $text) -ForegroundColor $colour
}

$Root = [System.IO.Path]::GetFullPath($Root)
# installer.py refuses this too, but only after a Python would have been unpacked into it.
if (Test-Path (Join-Path $Root ".git")) { throw "$Root is a git checkout; the program goes in a folder of its own (-Root)." }
$runtime = Join-Path $Root "runtime"
Write-Host ""
Write-Host "claude-voice -- installing into $Root" -ForegroundColor Cyan
Write-Host ""

# --- 1. the project ----------------------------------------------------------
$work = $null      # piped into iex this shares the caller's scope; never delete their $work
if (-not $Source) {
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "installer.py"))) {
        $Source = $PSScriptRoot
    } elseif ($WhatIf) {
        Say "would fetch" $ZipUrl "Yellow"
    } else {
        $work = Join-Path $env:TEMP ("claude-voice-source-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $work | Out-Null
        Say "project" "downloading $ZipUrl"
        Invoke-WebRequest -Uri $ZipUrl -OutFile (Join-Path $work "source.zip") -UseBasicParsing
        Expand-Archive -Path (Join-Path $work "source.zip") -DestinationPath (Join-Path $work "src")
        $found = Get-ChildItem -Path (Join-Path $work "src") -Filter "installer.py" -Recurse | Select-Object -First 1
        if (-not $found) { throw "the download from GitHub holds no installer.py" }
        $Source = $found.DirectoryName
    }
}
if ($Source) {
    $Source = [System.IO.Path]::GetFullPath($Source)
    if (-not (Test-Path (Join-Path $Source "installer.py"))) { throw "$Source is not a copy of claude-voice (no installer.py in it)" }
    Say "project" $Source "Green"
}

# --- 2. the runtime ----------------------------------------------------------
$marker = Join-Path $runtime "claude-voice-runtime.json"
$haveRuntime = $false
if ((Test-Path (Join-Path $runtime "python.exe")) -and (Test-Path $marker)) {
    try { $haveRuntime = ((Get-Content $marker -Raw | ConvertFrom-Json).sha256 -eq $PythonSha256) } catch { }
}

if ($haveRuntime) {
    Say "python" "$PythonVersion, already in $runtime" "Green"
} elseif ($WhatIf) {
    Say "would fetch" "Python $PythonVersion from python.org into $runtime (about 30 MB, SHA-256 checked)" "Yellow"
} else {
    $cache = Join-Path $env:TEMP "claude-voice-download"
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $zip = Join-Path $cache ([System.IO.Path]::GetFileName($PythonUrl))
    if (-not ((Test-Path $zip) -and ((Get-FileHash $zip -Algorithm SHA256).Hash -eq $PythonSha256))) {
        Say "python" "downloading $PythonVersion from python.org (about 30 MB)"
        Invoke-WebRequest -Uri $PythonUrl -OutFile $zip -UseBasicParsing
    }
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash
    if ($hash -ne $PythonSha256) {
        Remove-Item $zip -Force
        throw "the Python download does not match the pinned SHA-256 (got $hash). Nothing was installed."
    }
    Say "checked" "SHA-256 matches what python.org publishes" "Green"

    # Unpacked beside the live runtime and only then swapped in, so a failure
    # here leaves whatever was working before. The docs (62 MB) are left out.
    $staged = Join-Path $Root "runtime.new"
    if (Test-Path $staged) { Remove-Item $staged -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $staged | Out-Null
    $tar = Join-Path $env:SystemRoot "System32\tar.exe"
    if (Test-Path $tar) {
        & $tar -xf $zip -C $staged --exclude "Doc"
        if ($LASTEXITCODE -ne 0) { throw "tar could not unpack $zip" }
    } else {
        Expand-Archive -Path $zip -DestinationPath $staged
    }
    foreach ($exe in "python.exe", "pythonw.exe") {
        $sig = Get-AuthenticodeSignature (Join-Path $staged $exe)
        if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch [regex]::Escape("CN=$PythonSigner")) {
            Remove-Item $staged -Recurse -Force
            throw "$exe in the download is not signed by the $PythonSigner ($($sig.Status)). Nothing was installed."
        }
    }
    Say "checked" "python.exe and pythonw.exe are signed by the $PythonSigner" "Green"

    if (Test-Path $runtime) {
        # An older Python under a running install: stop that install's processes first.
        $oldInstaller = Join-Path $Root "app\installer.py"
        if ((Test-Path (Join-Path $runtime "python.exe")) -and (Test-Path $oldInstaller)) {
            & (Join-Path $runtime "python.exe") $oldInstaller stop --root $Root
        }
        $old = Join-Path $Root "runtime.old"
        if (Test-Path $old) { Remove-Item $old -Recurse -Force }
        Rename-Item $runtime "runtime.old"
        Remove-Item $old -Recurse -Force -ErrorAction SilentlyContinue
    }
    Rename-Item $staged "runtime"
    [System.IO.File]::WriteAllText($marker,
        (@{ version = $PythonVersion; url = $PythonUrl; sha256 = $PythonSha256 } | ConvertTo-Json),
        (New-Object System.Text.UTF8Encoding $false))
    Say "python" "$PythonVersion in $runtime" "Green"
}

if ($WhatIf -and (-not $haveRuntime -or -not $Source)) {
    Write-Host ""
    Write-Host "-WhatIf: nothing was written. With the Python in place, installer.py would then" -ForegroundColor Yellow
    Write-Host "copy the app, install the engine's packages and register it." -ForegroundColor Yellow
    return
}

# --- 3. the rest, on that Python ---------------------------------------------
$argv = @((Join-Path $Source "installer.py"), "install", "--root", $Root, "--source", $Source)
if ($Engine)           { $argv += @("--engine", $Engine) }
if ($NoClaude)         { $argv += "--no-claude" }
if ($Desktop)          { $argv += "--desktop" }
if ($StartWithWindows) { $argv += "--startup" }
if ($BigData)          { $argv += @("--big-data", $BigData) }
if ($NoPanel)          { $argv += "--no-panel" }
if ($NoStart)          { $argv += "--no-start" }
if ($Quiet)            { $argv += "--quiet" }
if ($WhatIf)           { $argv += "--dry-run" }
if ($Product)          { $argv += @("--product", $Product) }
if ($DataDir)          { $argv += @("--data", $DataDir) }
if ($ClaudeDir)        { $argv += @("--claude-dir", $ClaudeDir) }
if ($ShortcutDir)      { $argv += @("--shortcut-dir", $ShortcutDir) }
if ($Port)             { $argv += @("--port", $Port) }
Write-Host ""
& (Join-Path $runtime "python.exe") @argv
$code = $LASTEXITCODE
# The downloads are spent once app\ and runtime\ hold them. Python's zip is kept
# after a failure, so running this again skips that download.
if ($work) { Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue }
if ($code -eq 0 -and -not $WhatIf) {
    Remove-Item (Join-Path $env:TEMP "claude-voice-download") -Recurse -Force -ErrorAction SilentlyContinue
}
# Its exit code for a caller of the script file; not when piped into iex, where
# exit would close the window the person is reading.
if ($PSCommandPath) { exit $code }
