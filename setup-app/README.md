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
sets the folder, `--show choose|progress|done|fail --shot page.png` renders a page to
a picture and exits (for docs, and for checking a change without clicking through).

## Build and release

```powershell
.\setup-app\build.ps1        # -> setup-app\dist\ClaudeVoiceSetup.exe
```

Attach `dist\ClaudeVoiceSetup.exe` to the GitHub release. The Immersive AI mod
downloads it from `releases/latest/download/ClaudeVoiceSetup.exe`, so the name must
not change.
