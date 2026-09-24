# ClaudeVoiceSetup.exe

The install for somebody who has never opened a terminal: a window with Abby on the
left, three engines to choose from, one button, and her saying hello at the end.

It is a **window around `setup.ps1`**, not a second installer. It fetches this repo
(the `main` branch zip, or a folder with `--source`), then runs

```
powershell -ExecutionPolicy Bypass -File setup.ps1 -Engine <qwen|pocket|breeze> -NoPanel [-NoClaude]
```

and turns that script's lines into a headline and a progress bar. Everything that
knows how to install anything stays in the script, so the two roads cannot drift,
and "Try again" works because the script resumes where it stopped.

| | |
|---|---|
| **Why .NET Framework 4.8** | it ships with every Windows 10 and 11, so the exe runs as downloaded — about 400 KB with Abby in it, no runtime to fetch |
| **Why the manifest** | an exe named *setup* is assumed to be an installer and Windows asks for admin rights unless the exe says `asInvoker`. Nothing here needs them |
| **What it reads about the machine** | `nvidia-smi` for the card, its memory, compute capability and CUDA version — the same lines `breeze_setup.py` checks, so the two agree |
| **`--for "Immersive AI"`** | who sent the user here; the pages speak to that, and the Claude Code box starts unticked |
| **`-NoClaude`** | unless the user ticks the Claude Code box: no hooks, no `/voice`, no note in `~\.claude\CLAUDE.md`, and — on a fresh install only — the transcript watcher off. An existing install's Claude Code settings are never touched |

Other flags: `--engine qwen` pre-picks one (only if the machine can run it), `--dir`
sets the folder, `--data D:\claude-voice` puts the big files (Studio, the Qwen model,
Breeze) in a folder of their own, `--show choose|progress|done|fail --shot page.png`
renders a page to a picture and exits (for docs, and for checking a change without
clicking through).

**`--quiet`** is the game's road: no window at all, everything decided by the flags,
and every step written to `%LOCALAPPDATA%\claude-voice\setup-status.json` for the
caller to draw — four steps (the app, the engine, the model, the first start), what is
downloading, how much, how fast, how long is left. A `setup-cancel` file beside it
stops the install; exit codes are 0 installed, 1 failed, 2 another install is already
running, 3 stopped. The window writes the same file, so a game can follow a
double-click install too. Breeze's two long downloads print no numbers of their own:
its model is measured from the growing folder, and PyTorch says how long it has been.

**The code it installs is its own release's.** `build.ps1` stamps the exe with
`version.json`'s version, and it fetches that tag's zip — falling back to `main` only
when the tag is not there (a build made outside a release).

## Build and release

```powershell
.\setup-app\build.ps1        # -> setup-app\dist\ClaudeVoiceSetup.exe
```

Attach `dist\ClaudeVoiceSetup.exe` to the GitHub release. The Immersive AI mod
downloads it from `releases/latest/download/ClaudeVoiceSetup.exe`, so the name must
not change.
