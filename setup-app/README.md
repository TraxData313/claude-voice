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
