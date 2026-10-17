# ClaudeVoiceSetup.exe — retired

This was the one-window installer: an unsigned .NET exe that downloaded the repo zip and ran
`setup.ps1` through `powershell -ExecutionPolicy Bypass` with no window. **It is no longer built
or released**, for two reasons that are really one:

- **Smart App Control refuses it.** On a clean Windows 11 install SAC is on, and it blocks any
  executable without a signature it knows. This one has none, so on those machines it never ran.
- **What it did looks like what malware does**: a fresh exe running hidden PowerShell with the
  execution policy bypassed, then installing into whatever Python it found.

The install today is `install.ps1` → python.org's signed Python → `installer.py`
([docs/install.md](../docs/install.md)); the Immersive AI mod does the same steps in C#. A signed,
packaged installer (an Inno Setup build with a signing step) is step 7b, for when there is a
certificate; it will share the same install layout and registry key.

The source stays here for reference until then. Old copies of the exe in players' hands fetch
their own release's code and still work where nothing blocks them; `setup.ps1` hands over to
`install.ps1` for any that fall back to `main`.

## What must stay published for old Immersive AI versions

Immersive AI 3.4 to 3.6 download
`releases/latest/download/ClaudeVoiceSetup.exe` and run it with `--quiet`. That exe (1.16.0)
fetches `archive/refs/tags/v1.16.0.zip` and installs 1.16 in the old layout. So, until nobody
plays those versions:

- **The GitHub Release v1.16.0 stays "Latest", with its `ClaudeVoiceSetup.exe`.** Releases here
  are tags only (docs/updating.md). If a GitHub Release is ever made again, attach that same exe
  to it, or untick "Set as the latest release".
- **The tag `v1.16.0` stays.** Without it the exe falls back to `main.zip` and lays 2.0's code
  into whatever folder it picked.

The same exe, run by those mods to *add an engine* to a 2.0 install, would copy 1.16 over
`app\` (the folder where.json names). It is stopped by `app\.gitignore`, which the installer
writes read-only: it is the exe's first copy, and the error it raises is one it does not catch.
See `guard_against_old_setup` in installer.py.
