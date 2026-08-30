<#
    From nothing to a voice, in one command.

    Python if it is missing, then Qwen-TTS Studio, then the model, then
    install.ps1, then the panel with the engine warming up in it. Everything it
    already finds, it skips -- so running it twice is cheap and safe.

        .\setup.ps1                              # the whole thing
        .\setup.ps1 -ProjectDir $env:USERPROFILE # speak in every project, not just this one
        .\setup.ps1 -Build system                # smaller Studio, if you have CUDA already
        .\setup.ps1 -NoPanel                     # do not open the window at the end
        .\setup.ps1 -NoNote                      # leave ~\.claude\CLAUDE.md alone
        .\setup.ps1 -UpdateChecks                # let it look for a new version weekly
        .\setup.ps1 -WhatIf                      # say what it would do

    No administrator rights, and none are asked for. Python installs per-user;
    Studio is unpacked rather than installed -- msiexec /a lays a package out in
    a folder without touching the registry or Program Files, and that folder is
    all Studio has ever been. If there is no .msi to unpack it takes the .zip
    from the release page instead.

    Downloads resume. Two and a half gigabytes over a work VPN does not always
    arrive on the first attempt, and starting again from zero is a poor answer
    to that.
#>

param(
    [string]$ProjectDir,
    [string]$StudioDir = (Join-Path $env:LOCALAPPDATA "Programs\qwen-tts-studio"),
    [string]$ModelDir  = (Join-Path $env:USERPROFILE ".qwen-tts-studio\models"),
    [string]$PythonExe,
    # Qwen-TTS Studio's version, not this project's -- claude-voice's own is in
    # version.json and is never passed in.
    [string]$Version   = "0.2.9",
    [ValidateSet("bundled", "system")]
    [string]$Build     = "bundled",
    [string]$PythonVersion = "3.13.9",
    [switch]$NoShortcut,
    [switch]$NoNote,
    [switch]$NoPanel,
    [switch]$UpdateChecks,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

# There is no script file when this is piped into Invoke-Expression, so
# $PSScriptRoot is empty -- and that pipe is the documented way past an
# execution policy that refuses to run script files at all. Stand where the
# user is standing instead.
$repo = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }

function Say($msg, $colour = "Gray") { Write-Host $msg -ForegroundColor $colour }

# Studio is the folder holding app\ and runtime\; this file is the proof it is
# unpacked rather than half-copied.
function Test-Studio($dir) {
    return $dir -and (Test-Path (Join-Path $dir "runtime\bin\server\jvm.dll"))
}

# Move a Studio tree to where it belongs, contents first rather than the folder
# itself. The destination is often already there and half full: the ImmersiveAI
# mod for Bannerlord fetches this same engine and unpacks only its DLLs into
# %LOCALAPPDATA%\Programs\qwen-tts-studio. Move-Item onto an existing directory
# puts the source *inside* it, which yields qwen-tts-studio\qwen-tts-studio and
# a Test-Studio that keeps saying no.
function Move-StudioInto($src, $dst) {
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Get-ChildItem -LiteralPath $src -Force | ForEach-Object {
        $target = Join-Path $dst $_.Name
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Move-Item -LiteralPath $_.FullName -Destination $target
    }
    Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue
}

# Invoke-WebRequest buffers the whole response in memory before writing a byte,
# which is survivable for a jar and not for a 2 GB model. Stream it, and send a
# Range header when a part-file is already on disk so an interrupted download
# picks up where it stopped.
function Get-File($url, $dest, $label) {
    $part = "$dest.part"
    $from = if (Test-Path $part) { (Get-Item $part).Length } else { 0 }

    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    $req = [System.Net.HttpWebRequest]::Create($url)
    $req.UserAgent = "claude-voice/setup.ps1"
    if ($from -gt 0) { $req.AddRange($from) }

    try {
        $res = $req.GetResponse()
    } catch [System.Net.WebException] {
        # 416 means the part-file is already the whole file: the server has
        # nothing past that offset left to send.
        if ($from -gt 0 -and $_.Exception.Response -and
            $_.Exception.Response.StatusCode -eq 416) {
            Move-Item $part $dest -Force
            Say "$label : already complete" "Green"
            return
        }
        throw
    }

    $total = $res.ContentLength + $from
    if ($from -gt 0) { Say "$label : resuming at $([math]::Round($from/1MB)) of $([math]::Round($total/1MB)) MB" }
    else             { Say "$label : $([math]::Round($total/1MB)) MB" }

    $in = $res.GetResponseStream()
    $out = [System.IO.File]::Open($part, "Append", "Write")
    $buf = New-Object byte[] (1MB)
    $done = $from
    $tick = [Diagnostics.Stopwatch]::StartNew()
    try {
        while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
            $out.Write($buf, 0, $n)
            $done += $n
            if ($tick.Elapsed.TotalSeconds -ge 2) {
                $tick.Restart()
                Write-Progress -Activity $label `
                               -Status "$([math]::Round($done/1MB)) of $([math]::Round($total/1MB)) MB" `
                               -PercentComplete ([math]::Min(100, 100 * $done / $total))
            }
        }
    } finally {
        $out.Close(); $in.Close(); $res.Close()
        Write-Progress -Activity $label -Completed
    }

    if ($done -ne $total) { throw "$label : got $done bytes, expected $total" }
    Move-Item $part $dest -Force
    Say "$label : done" "Green"
}

# --- python ---------------------------------------------------------------
# Not the word "python" -- the absolute path. Hooks are spawned without a shell
# profile, so anything conda-activated or aliased is invisible to them, and the
# Microsoft Store stub on PATH is a stub: it launches the Store, not Python.
function Find-Python {
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $candidates += @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:LOCALAPPDATA\miniconda3\python.exe",
        "$env:LOCALAPPDATA\anaconda3\python.exe",
        "$env:USERPROFILE\miniconda3\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe",
        "$env:ProgramFiles\Python3*\python.exe"
    ) | ForEach-Object { (Get-Item $_ -ErrorAction SilentlyContinue).FullName } | Sort-Object -Descending

    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c) -and $c -notmatch 'WindowsApps') { return $c }
    }
    return $null
}

if (-not $PythonExe) { $PythonExe = Find-Python }

if ($PythonExe) {
    Say "python        : $PythonExe" "Green"
} elseif ($WhatIf) {
    Say "would install : Python $PythonVersion, per-user" "Yellow"
} else {
    # Per-user, so no elevation prompt: InstallAllUsers=0 puts it under
    # %LOCALAPPDATA%, and PrependPath writes to HKCU. /passive keeps a progress
    # bar on screen, because a silent five-minute pause reads as a hang.
    Say "python        : not found -- installing $PythonVersion, per-user (no admin)" "Yellow"
    $exe = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
    if (-not (Test-Path $exe)) {
        Get-File "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe" `
                 $exe "python $PythonVersion"
    }
    $p = Start-Process $exe -Wait -PassThru -ArgumentList @(
        "/passive", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=0", "Include_test=0")
    if ($p.ExitCode -ne 0) { throw "the Python installer exited with $($p.ExitCode)" }

    # PATH changed in the registry, not in this process. Ask the filesystem.
    $tag = "Python" + ($PythonVersion -split '\.')[0] + ($PythonVersion -split '\.')[1]
    $PythonExe = Join-Path $env:LOCALAPPDATA "Programs\Python\$tag\python.exe"
    if (-not (Test-Path $PythonExe)) { $PythonExe = Find-Python }
    if (-not $PythonExe) { throw "installed Python but cannot find python.exe. Pass -PythonExe." }
    Say "python        : $PythonExe" "Green"
}

# --- git ------------------------------------------------------------------
# Nothing here needs git to install, and none is fetched if there is none. The
# only thing that wants it is `update --apply`, which pulls -- so this reports
# and changes nothing. Putting a folder on somebody's PATH is not a speech
# installer's decision to make.
#
# Tested against the *registry* PATH, not this process's, because the registry
# one is what the panel gets: it starts from the Desktop shortcut and inherits
# Explorer's environment. Claude Code hands its own children a PATH with a git of
# its own prepended, so `Get-Command git` in that terminal answers yes on
# machines where the panel answers no. Both true; only one of them is the panel's.
$registryPath = @([Environment]::GetEnvironmentVariable('Path', 'User'),
                  [Environment]::GetEnvironmentVariable('Path', 'Machine')) -join ';'
$gitListed = @($registryPath -split ';' | Where-Object {
    $_ -and (Test-Path -LiteralPath ($_.TrimEnd('\') + '\git.exe') -ErrorAction SilentlyContinue)
}) | Select-Object -First 1

if ($gitListed) {
    Say "git           : $($gitListed.TrimEnd('\'))\git.exe" "Green"
} else {
    $gitFound = @("$env:LOCALAPPDATA\Programs\Git\cmd\git.exe",
                  "$env:ProgramFiles\Git\cmd\git.exe",
                  "${env:ProgramFiles(x86)}\Git\cmd\git.exe") |
        Where-Object { Test-Path -LiteralPath $_ -ErrorAction SilentlyContinue } |
        Select-Object -First 1
    if ($gitFound) {
        Say "git           : $gitFound" "Green"
        Say "                not on the PATH the panel inherits. Updates work anyway --" "Yellow"
        Say "                the updater looks in that folder itself. To list it properly," "Yellow"
        Say "                which is your PATH and so your call:" "Yellow"
        Say ("                [Environment]::SetEnvironmentVariable('Path', " +
             "[Environment]::GetEnvironmentVariable('Path','User').TrimEnd(';') + " +
             "';" + (Split-Path $gitFound) + "', 'User')") "DarkGray"
    } else {
        Say "git           : none -- everything works except 'update --apply', which pulls" "Yellow"
    }
}

# --- Qwen-TTS Studio ------------------------------------------------------
if (Test-Studio $StudioDir) {
    Say "studio        : already at $StudioDir" "Green"
} else {
    # Somewhere else already? install.ps1 looks in these, so finding one here
    # means there is nothing to fetch.
    $found = @(
        "$env:USERPROFILE\Downloads\qwen-tts-studio",
        "$env:LOCALAPPDATA\Programs\qwen-tts-studio",
        "$env:ProgramFiles\qwen-tts-studio",
        "C:\qwen-tts-studio"
    ) | Where-Object { Test-Studio $_ } | Select-Object -First 1

    if ($found) {
        # Downloads is where a browser drops a file, not where a program lives.
        # Storage Sense deletes anything there left untouched for 30 days, and it
        # is on by default -- it took an 831 MB Studio once, and the engine then
        # failed with a folder-not-found that looks nothing like "Windows tidied
        # up". Every other location is adopted where it stands; this one is moved.
        if ($found.StartsWith("$env:USERPROFILE\Downloads\", [StringComparison]::OrdinalIgnoreCase)) {
            if ($WhatIf) {
                Say "would move    : $found -> $StudioDir" "Yellow"
            } else {
                Move-StudioInto $found $StudioDir
                Say "studio        : moved out of Downloads -> $StudioDir" "Green"
            }
        } else {
            $StudioDir = $found
            Say "studio        : found at $StudioDir" "Green"
        }
    } else {
        # A package already in Downloads beats a second 663 MB off the internet.
        # Someone who tried the .msi and was refused by Windows has one.
        $local = Get-ChildItem "$env:USERPROFILE\Downloads" -Filter "qwen-tts-studio*" -File -ErrorAction SilentlyContinue |
                 Where-Object { $_.Extension -in ".msi", ".zip" } |
                 Sort-Object Length -Descending | Select-Object -First 1

        $name = "qwen-tts-studio-$Version-windows-cuda-$Build.zip"
        if ($WhatIf) {
            if ($local) { Say "would unpack  : $($local.FullName) -> $StudioDir" "Yellow" }
            else        { Say "would fetch   : $name -> $StudioDir" "Yellow" }
        } else {
            # Two temp folders, deliberately: the package is kept so a failed
            # run resumes rather than re-downloads, the unpacked tree is wiped
            # so a failed run cannot leave half a Studio behind to be found.
            $pkgDir  = Join-Path $env:TEMP "qwen-studio-pkg"
            $staging = Join-Path $env:TEMP "qwen-studio-unpack"
            if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $staging | Out-Null

            if ($local) {
                Say "studio        : unpacking $($local.Name) from Downloads"
            } else {
                $pkg = Join-Path $pkgDir $name
                if (-not (Test-Path $pkg)) {
                    Get-File "https://github.com/Danmoreng/qwen-tts-studio/releases/download/v$Version/$name" `
                             $pkg "studio ($Build)"
                }
                $local = Get-Item $pkg
            }

            if ($local.Extension -eq ".msi") {
                # /a is an administrative install, which despite the name needs
                # no administrator: it lays the package's files out under
                # TARGETDIR and stops. No elevation, no registry, no entry in
                # Add/Remove Programs.
                $log = Join-Path $env:TEMP "qwen-studio-unpack.log"
                $p = Start-Process msiexec.exe -Wait -PassThru -NoNewWindow -ArgumentList @(
                    "/a", "`"$($local.FullName)`"", "/qn", "TARGETDIR=`"$staging`"", "/l*v", "`"$log`"")
                if ($p.ExitCode -ne 0) { throw "msiexec /a failed ($($p.ExitCode)). See $log" }
            } else {
                Add-Type -AssemblyName System.IO.Compression.FileSystem
                [System.IO.Compression.ZipFile]::ExtractToDirectory($local.FullName, $staging)
            }

            # Both packages wrap everything in one folder, but they need not:
            # take whichever level actually holds the JVM.
            $root = if (Test-Studio $staging) { $staging }
                    else { (Get-ChildItem $staging -Directory |
                            Where-Object { Test-Studio $_.FullName } |
                            Select-Object -First 1).FullName }
            if (-not $root) { throw "unpacked $($local.Name) but found no runtime\bin\server\jvm.dll under $staging" }

            Move-StudioInto $root $StudioDir
            Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
            Say "studio        : $StudioDir" "Green"
        }
    }
}

# --- the models -----------------------------------------------------------
# Studio's own Welcome screen pulls these from the same repo. Two files, and
# both are needed: the talker turns text into audio tokens, the tokenizer turns
# those back into sound.
#
# 1.7b, not 0.6b -- the size decides the shape of a speaker embedding, and the
# voices in voices\ are 2048-dimension, which only the larger model produces.
$hf = "https://huggingface.co/Serveurperso/Qwen3-TTS-GGUF/resolve/main"
foreach ($m in @("qwen-talker-1.7b-base-Q8_0.gguf", "qwen-tokenizer-12hz-Q8_0.gguf")) {
    $dest = Join-Path $ModelDir $m
    if (Test-Path $dest)  { Say "model         : have $m" "Green" }
    elseif ($WhatIf)      { Say "would fetch   : $m -> $ModelDir" "Yellow" }
    else                  { Get-File "$hf/$m" $dest $m }
}

# --- wire it up -----------------------------------------------------------
Say ""
$opts = @{ PythonExe = $PythonExe; StudioDir = $StudioDir }
if ($ProjectDir) { $opts.ProjectDir = $ProjectDir }
if ($NoShortcut)   { $opts.NoShortcut = $true }
if ($NoNote)       { $opts.NoNote = $true }
if ($UpdateChecks) { $opts.UpdateChecks = $true }
if ($WhatIf)       { $opts.WhatIf = $true }
& (Join-Path $repo "install.ps1") @opts

if ($WhatIf) { return }   # install.ps1 has already said so

# --- open it --------------------------------------------------------------
# The panel first, then the model: loading takes the better part of a minute,
# and that minute is much better spent watching a window say so than watching a
# prompt not come back.
#
# Nothing stops a second panel being opened, and a stray duplicate is the one
# way running this twice is not free -- so look before opening one.
Say ""
if (-not $NoPanel) {
    $open = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
              Where-Object { $_.CommandLine -like "*panel.py*" })
    if ($open) { Say "panel         : already open" "Green" }
    else       { & $PythonExe (Join-Path $repo "voice_cli.py") panel }
}
& $PythonExe (Join-Path $repo "voice_cli.py") on

Say ""
Say "Restart Claude Code -- hooks are read at session start. Then: /voice on" "Green"
