# When it goes quiet

Silence has no error message, which is what makes it maddening. Work down this list; it is
ordered by how often each one is actually the culprit.

Where things are: an installed copy keeps its logs in `%LOCALAPPDATA%\claude-voice\logs\`, a
git checkout in its own `logs\`. In a terminal, an installed copy runs on its own Python:

```powershell
& "$env:LOCALAPPDATA\Programs\claude-voice\runtime\python.exe" "$env:LOCALAPPDATA\Programs\claude-voice\app\voice_cli.py" status
```

`/voice status` in Claude Code is the same thing, and shorter.

## 1. Is the engine alive?

`status` answers most of it in one screen: whether the voice is on, whether the engine is
loaded and on which port, whether Claude Code's hooks are wired and when one last ran, and
whether games can find it.

**The engine does not survive a reboot.** With the voice on, the next prompt you type into
Claude Code starts it again, by way of the `UserPromptSubmit` hook; `on` does the same by hand,
and so does the panel's switch. An answer that arrived while it was down is said once it is up,
if it is less than five minutes old.

## 2. Read `speak-server.log`

The engine's own account of itself: model loading, every utterance, and any crash. Search it
for `sessions:` to see what it decided to say for each hook call, and why it said nothing.

## 3. Did the hook run? Read `hook.log`

One line goes in **per call**, before any decision is made. That distinction is the whole
point:

- **Entries, but no sound** — the hook ran. The line says what came back; the fault is
  downstream, so go back to the engine log.
- **No entries at all** — Claude Code never called it.

`status` says on its *hooks* line which events are wired, when one last ran, and whether the
command is written in a way that can run at all. `/voice hooks install` writes them again.
Things that get hooks ignored or failing, none of which says anything out loud:

- **The session started before the hooks existed.** Hooks are read when a session starts.
  Recent Claude Code picks a change up at the open session's next prompt; older versions need
  Claude Code restarted. A resumed conversation runs the hooks as they are now.
- **Backslashes in the command.** Claude Code runs a hook's command through bash on Windows,
  and bash reads `C:\Users\...\python.exe` as `C:Users...python.exe`: *command not found*, on
  every call. The installer writes forward slashes; `status` calls anything else *the old
  kind*.
- **A byte-order mark.** Three invisible bytes at the top of `settings.json` are enough for a
  strict JSON parser to reject the whole file. Check with
  `Get-Content settings.json -Encoding Byte -TotalCount 3` — `239 187 191` is the BOM.
- **A malformed entry.** One bad entry can invalidate the entire hooks block, so a broken
  `PreToolUse` silences `Stop` along with it.

## 4. It speaks the answers but not the lines in between

That is the default since 2.0. No hook Claude Code runs carries the short lines written
between tool calls, so only the end of each turn is heard, plus a prompt waiting on you.
`/voice narrate transcript on`, then `/voice hooks install`, reads them off the session's
transcript as each tool starts. **[Why →](how-it-works.md#what-a-hook-does-not-carry)**

## 5. "Part of this app has been blocked"

That Windows Security notice is **Smart App Control**. Windows 11 turns it on for a clean
install, and it refuses any program or DLL that is neither signed by somebody Microsoft knows
nor known to Microsoft's cloud. It is per file, and it says which in Event Viewer →
Applications and Services Logs → Microsoft → Windows → CodeIntegrity → Operational.

What claude-voice does about it:

- **Qwen is refused up front.** Qwen-TTS Studio's `qwen3_tts.dll` is unsigned. With Smart App
  Control on, the engine does not try to load it — trying is what raises the notice — but
  switches to Pocket (Breeze where there is no Pocket), writes that into the config, and the
  panel says *"Windows Smart App Control blocked the Qwen engine; switched to Pocket TTS."* A
  refusal by some other policy is remembered, so it is seen once rather than at every start.
- **Everything else is a signed Python running plain `.py` files.** The program is
  python.org's own Python, signed by the Python Software Foundation and checked against its
  SHA-256 at install, and the engine's packages are pinned to builds seen to load with Smart
  App Control on.

**Why there is no `.exe`.** An installer or a program built here would be unsigned, and an
unsigned `.exe` never runs under Smart App Control. Signing one takes a code-signing
certificate paid for every year, which this project will not have. A signed runtime running
our code gets the same result without one. **[More →](install.md#smart-app-control)**

If you see the notice anyway, the CodeIntegrity log names the file. A notice that names
`python.exe` or a `.pyd` under `claude-voice\runtime` means a package build the pins did not
cover — worth reporting, with that file name.

## Other things that have actually happened

**The engine is not on port 8765.** Another program held it. The engine names that program in
its log, takes the next free port, and writes it where the panel, the hooks and games look;
the panel's status line says so. It moves back once 8765 is free. A port that every try finds
taken is set by hand with `"port"` in `config.json`.

**Claude Code refused every prompt: "A hook blocked your prompt … can't open file …
speak_hook.py".** The folder was moved, renamed or deleted while its hooks were still in
Claude Code's settings, and an install older than 1.15.1 wrote a hook that blocks when its
file is gone. Put the folder back, or open `%USERPROFILE%\.claude\settings.json` in Notepad and
delete the entries that mention `speak_hook.py`. Installing again writes the newer line, which
steps aside instead.

**She said "there are 162 Cyrillic characters I can't speak out".** She cannot, on Pocket TTS
— six Latin-script languages and nothing else. It is said out loud rather than dropped
quietly because that model answers Cyrillic by babbling, not by failing. Qwen reads it, where
Smart App Control allows it. **[Three engines →](engines.md)**

**Cloning stopped with a Hugging Face message.** The built-in voices and voice cloning are
different weights, and Kyutai gate the ones that clone. Accept the terms on their page and
`hf auth login`. The reference render is kept, so the retry costs seconds.

**Picking a voice paused for half a minute the first time.** Each of Pocket's six languages is
its own model, and the voice decides which one loads. First use of a language downloads it;
after that it is a load.

**It spoke a message that was not mine.** Every Claude Code session on the machine talks
through the same voice. It says the project's name when the speaker changes;
`sessionLabel: "off"` in `config.json` stops that, and unticking a session in the panel
silences it.

**It started a line and cut it off.** Something took the floor: `say`, `replay` or `stop` all
interrupt deliberately. Ordinary lines queue and wait.

**She breaks up mid-sentence when the machine is busy.** The player takes one file at a time
and the next one was not finished in time — a buffer underrun, not the voice failing.
`speak-server.log` says `playback ran dry mid-message` every time it happens. `playback auto`
is the default and works the wait out per message; `playback whole` makes the entire line
before saying any of it. `playback report` says what lead this machine has actually needed.
**[The measurements →](how-it-works.md#when-the-first-word-is-played)**

**The engine died and took a line with it.** Renaming or deleting a voice's folder while it is
being spoken will do it: the native layer reads the embedding file on every generation.

**Two engines, everything said twice.** `HTTPServer` sets `SO_REUSEADDR`, which on Windows
lets a *second* process bind a port already in use. The engine refuses that now, and leaves
when the port is held by an engine of its own; a stray old process is still worth ruling out
— `status` reports the port.

## Starting clean

```
/voice kill      # unload the model
/voice start     # load it again
/voice say testing
```

If `say` speaks and your answers do not, the fault is in the hooks (step 3), not in the
speaking. If `say` is silent too, it is the engine or the audio device.
