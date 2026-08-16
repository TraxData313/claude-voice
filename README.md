# claude-voice

Claude Code reads its answers aloud, locally, in a voice you choose.

No cloud, no API key, no audio leaving the machine. A local Qwen-TTS model does the
speaking, and a `Stop` hook feeds it each answer as it finishes.

It speaks two things. The short lines said **while it works** ("let me check whether that
exists") go out as they happen, so you can follow along without watching. When a long
answer finishes, it reads the **TL;DR** and not the whole thing — an answer full of paths
and flags is written for eyes that skip around, so the ear gets the summary and the
detail stays on screen where you can look at it properly. Short answers are read in full,
because there is nothing to summarise.

## What you need

- **Windows.** Playback uses `winsound` and the engine bridge loads a Windows DLL.
- **[Qwen-TTS Studio](https://github.com/QwenLM)** installed, with a talker model
  downloaded through it. `qwen-talker-1.7b-base` is the one to get: it produces the
  2048-dimension embeddings this expects. The 0.6b model gives 1024 and will not match
  voices made with the larger one.
- **Python 3.9+** — standard library only. Nothing to `pip install`.
- **Claude Code.**

Studio has no CLI, so this drives its engine directly: it boots Studio's own bundled JVM
in-process and calls the JNI methods behind the GUI. See `qwen_engine.py` if you want the
details, including the two constants that must be exactly right.

## Install

```powershell
git clone <this-repo> claude-voice
cd claude-voice
.\install.ps1
```

That finds Python and Studio, writes `config.json`, installs the `Stop` hook into
`.claude\settings.json`, and adds the `/voice` command. Use `-WhatIf` to see what it
would do, `-ProjectDir C:\code\my-project` to make Claude speak in a different project,
and `-StudioDir` / `-PythonExe` if either is somewhere unusual.

**Then restart Claude Code** — hooks are only read at session start.

To speak in **every** project rather than one, install into your user settings instead:

```powershell
.\install.ps1 -ProjectDir $env:USERPROFILE
```

Do one or the other, not both. Hooks defined in both places all fire, so you get two
processes racing for every event.

```
/voice on
```

The first model load takes 40–60 seconds; after that the engine stays warm and answers
begin speaking almost immediately.

## Using it

```powershell
python voice_cli.py on          # or off / toggle / status
python voice_cli.py list        # every voice available
python voice_cli.py set sibylla
python voice_cli.py say "trying a line"
python voice_cli.py replay      # say the last answer again; replay 3 for the third back
python voice_cli.py stop        # shut up mid-sentence
python voice_cli.py kill        # unload the model and free the memory
python voice_cli.py narrate off # only speak finished answers, not the running commentary
python voice_cli.py max 900     # read a bit more of each answer
```

`/voice <args>` inside Claude Code is the same thing — note the space, `/voice on` rather
than `/voice_on`.

**There is no play button next to each message.** Claude Code has no API for adding
controls to the transcript, so `replay` is how you hear something a second time. A new
answer always interrupts whatever is still being spoken; `stop` cuts it off entirely.

## Voices

Two voices ship with this repo: **Sibylla** and **Max**, both the author's own. See
[VOICES.md](VOICES.md) for what may go in that folder and what may not — the short version
is that a speaker embedding *is* a clone of a real person's voice, and shipping one you
did not record is not yours to give away.

Adding your own is one command. Use a clean 20–40 second mono clip of one person talking:

```powershell
python voice_cli.py clone C:\path\to\sample.wav --name "Ada"
python voice_cli.py set ada
```

Voices you may keep but not publish can live outside the repo entirely — point
`extraVoicesDirs` in `config.json` at any folder with the same
`<sex>\<culture>\<id>` layout, and they show up in `list` marked *local only*.

## Writing answers that get spoken well

Only long answers need a summary. A one-line reply, or a line of narration before a
command, is already the right size for an ear and gets read as it is. Put this in your
project's `CLAUDE.md` — or in `~\.claude\CLAUDE.md` to cover every project at once:

> Answers are read aloud. Keep the short lines said between commands to a sentence or two
> of plain speech — they are spoken as written. End **substantial** answers with a
> `## TL;DR` section written for the ear: no file names, paths, commands or line numbers,
> plain sentences in the order *what changed, does it work, what do I do next*, five
> sentences at most. Short answers need no TL;DR at all.

| hook | fires | speaks |
|---|---|---|
| `PreToolUse` | before each command, mid-turn | the narration line above it |
| `Stop` | when a turn ends | the TL;DR, or the whole answer if there is none |

Both dedupe against what was said recently, so a line spoken during the work is not
repeated in the summary.

## When nothing is spoken

Read `logs\hook.log` **first**. One line goes in per hook invocation, so an empty file
means Claude Code never ran the hook at all — a different problem from the hook running
and failing. `logs\speak-server.log` has the engine's side, including model load errors.

Two causes account for almost all silence, and the log tells them apart.

**Entries in `hook.log`, but nothing spoken** — the hook ran. The line says what it
decided; check `speak-server.log` for the engine's side.

**No entries at all** — Claude Code never called it. Either the session predates the
config (hooks load at session start, so restart), or the hooks block was rejected. Two
things get it rejected:

- *The interpreter cannot be found.* Hooks run without a shell profile, so anything
  conda-activated or Store-aliased is invisible; `install.ps1` writes the absolute path
  for this reason.
- *An entry is missing the field its event expects.* `PreToolUse` matches on tool name and
  wants a `matcher`; `Stop` fires once per turn and takes none. One malformed entry can
  invalidate the whole block, so a bad `PreToolUse` will silence `Stop` along with it.
- *The file starts with a byte-order mark.* `Out-File -Encoding utf8` writes one in
  PowerShell 5.1, and three invisible bytes at the top of `settings.json` are enough for a
  strict JSON parser to reject the entire file. Nothing reports this; the hooks simply
  never run. Check with `Get-Content settings.json -Encoding Byte -TotalCount 3` — `239
  187 191` is the BOM. `install.ps1` writes without one.

If the log stays empty after a restart with both of those correct, the hook may not be
loading from the project at all — try installing it into your user settings instead:

```powershell
.\install.ps1 -ProjectDir $env:USERPROFILE
```

## How it fits together

| file | what it does |
|---|---|
| `speak_server.py` | holds one warm engine behind a localhost HTTP API |
| `speak_hook.py` | the `Stop` hook: last answer → TL;DR → the server |
| `voice_cli.py` | the switch, the picker, and `clone` |
| `voice_lib.py` | config, voice catalogue, markdown→speech |
| `qwen_engine.py` | the JNI bridge into Studio's engine |

Three things in here are less obvious than they look, and all three cost real time to
find:

- **A JNIEnv pointer belongs to the thread that made the JVM.** The engine therefore
  lives on one dedicated thread and HTTP handlers only enqueue work.
- **`SO_REUSEADDR` means something else on Windows.** `HTTPServer` sets it, and Windows
  then lets a *second* process bind a port already in use rather than failing — two
  engines, two model loads, every sentence spoken twice.
- **The embedding extractor and the ICL encoder cannot share a process.** Loading the ICL
  encoder tears the talker model down underneath the engine.

## Licence

Code is MIT. Voices are not code — see [VOICES.md](VOICES.md).
