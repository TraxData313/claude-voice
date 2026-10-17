# Installing it

Windows only. One line in PowerShell:

```powershell
powershell -c "irm https://raw.githubusercontent.com/TraxData313/claude-voice/main/install.ps1 | iex"
```

Or from a copy of the project (a clone, or GitHub's zip):

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

It says each step as it takes it, and at the end claude-voice is a program like any other: in
the Start menu as **Abby for Claude**, in **Settings → Apps** as **claude-voice** with a real
Uninstall, and wired into Claude Code. Restart Claude Code, then `/voice on`.

**No administrator rights at any point**, and it works with **Smart App Control** on — [why
both are true](#smart-app-control).

**As a Claude Code plugin** instead: `/plugin marketplace add TraxData313/claude-voice`, then
`/plugin install claude-voice@claude-voice`, then `/claude-voice:setup` for the engine.

## Why the command line names an execution policy

Windows ships with running script *files* switched off, and refuses one downloaded from the
internet even where they are on. `-ExecutionPolicy Bypass` on the command line is Microsoft's
documented way to run one script you chose: it lasts for that one PowerShell and changes no
setting. The first form needs no flag at all, because a policy is about script files and the
pipe never makes one — the same form uv, Scoop and others use.

Nothing claude-voice starts afterwards uses either. Read `install.ps1` first if you like; it is
short and says what it does.

## What it does

1. **Finds the project** — the folder `install.ps1` is in, or GitHub's zip of `main`.
2. **Fetches Python** — python.org's own Python 3.12.10 for Windows, as a plain zip (about
   30 MB), checks it against the SHA-256 written into the script, checks that `python.exe` and
   `pythonw.exe` are signed by the **Python Software Foundation**, and unpacks it into the program
   folder. This Python is claude-voice's alone: it never touches one you already have, and
   nothing is installed into yours.
3. **Hands over to `installer.py`** on that Python, which:
   - copies the app in, compiles it to prove every file parses, and only then swaps it into place
   - installs the engine's packages into the private Python, at the exact versions in
     `requirements-pocket.txt`, from PyPI
   - writes your settings into a data folder of their own
   - lists it in **Settings → Apps**, with an uninstaller that runs on the same signed Python
   - puts **Abby for Claude** in the Start menu (`-Desktop` adds one there too)
   - writes the registry key games find it by ([discovery.md](discovery.md))
   - installs Claude Code's hooks into `~\.claude\settings.json`, and the `/voice` command
   - starts the engine and opens the panel

| Option | |
|---|---|
| `-Engine pocket` | the default: Pocket TTS on the CPU, a few hundred MB, no graphics card |
| `-Engine qwen` | Qwen on an NVIDIA card: Studio and the 2.2 GB model. **Smart App Control refuses Studio's DLLs** — the engine says so and falls back to Pocket |
| `-Engine breeze` | the engine that laughs: about 11 GB, a 16 GB NVIDIA card. [Breeze →](engines.md#breeze-tts-2-the-one-that-laughs) |
| `-NoClaude` | for a game or a program only: nothing of Claude Code's is touched |
| `-Desktop` · `-StartWithWindows` | a Desktop shortcut · open the panel at login (a Run value, removed with the rest) |
| `-BigData D:\voices` | one folder for the big engine files |
| `-Root <folder>` | another program folder |
| `-WhatIf` | say what it would do, change nothing |

With the piped form, arguments go like this:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/TraxData313/claude-voice/main/install.ps1))) -Engine qwen
```

Running it again updates the program in place and keeps your settings.

**What Claude Code then says aloud:** each finished answer (its `## TL;DR`), and a prompt or a
question that is waiting on you. The short lines written between tool calls are not in any hook
Claude Code runs, so they stay silent unless you turn on `/voice narrate transcript on` and run
`/voice hooks install` again. **[Why →](how-it-works.md#the-hooks)**

Nothing is written into your `~\.claude\CLAUDE.md`. The rules for writing to be heard reach each
session through its `SessionStart` hook instead, and an install made before 2.0 has its old
`<!-- claude-voice -->` block cut out of that file — only between the markers.

## Where everything goes

```text
%LOCALAPPDATA%\Programs\claude-voice\      the program
    runtime\        python.org's Python, private; the engine's packages live in here
    app\            the code, replaced whole by an update
    install.json    what was installed, and everything it put outside this folder
%LOCALAPPDATA%\claude-voice\               yours, never touched by an update
    config.json, logs\, voices\ (the ones you make), where.json
```

Outside those two: the Start menu shortcut, the Settings → Apps entry and the
`HKCU\Software\claude-voice` key, Claude Code's hooks and `/voice`, and — only when asked — a Run
value. Pocket's voice model is fetched by the engine the first time it loads, into the shared
Hugging Face cache.

## Smart App Control

Windows 11 turns Smart App Control on for a clean install. It runs a program or loads a DLL only
when it is signed by somebody Microsoft knows, or when Microsoft's cloud knows that exact file.
An `.exe` built here would be neither: signing one takes a paid code-signing certificate renewed
every year, and this project is not going to have one. So there is no `.exe`, and there will not
be — the program is python.org's signed Python running claude-voice's own code:

- **The Python is signed** by the Python Software Foundation. `python.exe`, `pythonw.exe` and
  every file with them run.
- **The engine's packages are pinned** to builds seen to load under Smart App Control. That is
  per file: on 2026-10-17 it refused the Python 3.13 build of one Pocket dependency
  (sentencepiece) while allowing the 3.12 build of the same version — which is why the Python is
  3.12, and why the versions are exact.
- **Qwen's engine is the exception**: Qwen-TTS Studio's DLLs are unsigned and refused. The engine
  checks before it loads anything, says so, and switches to Pocket.

If Smart App Control does refuse something, `install` says so at the end ("could not load its
voice"), and Event Viewer → Applications and Services → Microsoft → Windows → CodeIntegrity names
the file.

## Updating

`voice_cli.py update --apply` (or the panel's button) takes the newest release from GitHub,
stages it beside the running copy, swaps the two, re-registers it and restarts the engine. Your
data folder is not touched. A release that needs a different Python says so and asks you to run
`install.ps1` again. **[Updating →](updating.md)**

## Removing it

**Settings → Apps → claude-voice → Uninstall.** It lists what it will take and asks, then asks
separately about your own settings and voices — kept unless you say otherwise. It removes the
hooks (only its own: another copy's stay), the `/voice` command if it is its own, the shortcuts,
the Run value, both registry keys, where.json, anything it fetched for an engine, and the program
folder.

```powershell
& "$env:LOCALAPPDATA\Programs\claude-voice\runtime\python.exe" "$env:LOCALAPPDATA\Programs\claude-voice\app\installer.py" uninstall --dry-run
```

The uninstaller runs on the Python it is deleting. Windows will not delete a running program's
files, but it will rename their folder, so the runtime is moved into `%TEMP%` in one step and
everything that lets go is deleted; a few MB of in-use files wait there until the next install
sweeps them, or Windows clears its temp folder.

## A developer's checkout

A git clone is a **source**, never an install: nothing installs into it, rewrites it or removes
it, and `install.ps1` run from one copies it into the program folder. To run straight from the
clone instead:

```powershell
python voice_cli.py hooks install     # Claude Code's hooks point at this folder
python voice_cli.py register          # games find it (Kind = dev)
```

Installing afterwards takes both over — the hooks and the registration then name the installed
copy, and the clone's settings are carried into the data folder on that first install. `register`
in the clone takes the key back.

## Older installs

An install made before 2.0 (code straight in `%LOCALAPPDATA%\Programs\claude-voice`, packages in
your own Python) is moved over by running `install.ps1` once: its settings go to the data folder,
what it had fetched (Studio, a model, Breeze) is remembered for the uninstaller, and its files
are replaced by the new layout. The Python it may have installed for you is left alone — remove
it from Settings → Apps if nothing else uses it. `setup.ps1` and `uninstall.ps1` still exist and
hand over to the new ones.

## From a game

The Immersive AI mod installs it for its players the same way, without PowerShell: it fetches
the project and python.org's Python, checks the hash and the signature, and runs `installer.py
--quiet --no-claude`, drawing the progress inside the game from `setup-status.json`
([api.md](api.md#for-a-game)). The old `ClaudeVoiceSetup.exe` is retired: Smart App Control
refuses it.

## When it does not work

Silence has no error message. **[When it goes quiet →](troubleshooting.md)** starts with the two
questions worth asking first. The install's own log is `%LOCALAPPDATA%\claude-voice\setup.log`.
