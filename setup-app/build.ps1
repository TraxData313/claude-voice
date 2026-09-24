<#
    Builds ClaudeVoiceSetup.exe into setup-app\dist\.

        .\setup-app\build.ps1

    The exe is what a release carries as an asset, so that
    https://github.com/TraxData313/claude-voice/releases/latest/download/ClaudeVoiceSetup.exe
    always answers with the newest one. It is never committed: dist\ is ignored.
#>
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
dotnet build (Join-Path $here "ClaudeVoiceSetup.csproj") -c Release --nologo -v quiet
if ($LASTEXITCODE -ne 0) { throw "build failed" }
$dist = Join-Path $here "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
Copy-Item (Join-Path $here "bin\Release\net48\ClaudeVoiceSetup.exe") $dist -Force
$exe = Get-Item (Join-Path $dist "ClaudeVoiceSetup.exe")
"built $($exe.FullName)  ($([math]::Round($exe.Length / 1KB)) KB)"
