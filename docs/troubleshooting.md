# When it goes quiet

Silence has no error message, which is what makes it maddening. Work down this list; it is
ordered by how often each one is actually the culprit.

## 1. Is the engine alive?

```powershell
python voice_cli.py status
```

This answers most of it in one screen: whether the voice is on, whether the engine is
loaded, and whether it is following your sessions.

**The engine does not survive a reboot,** and nothing revives it on its own. After
restarting the machine, `on` once and it stays warm until you shut down or `kill` it.

If it died mid-session — it can, see below — everything goes quiet until something asks it
to speak. `on` or `start` brings it back, and it will catch up on what it missed.

## 2. Read `logs\speak-server.log`

The engine's own account of itself: model loading, every utterance, and any crash. Search
it for `watcher:` to see what it decided to say, and when.

## 3. If you are relying on hooks, read `logs\hook.log` first

One line goes in **per invocation**, before any decision is made. That distinction is the
whole point:

- **Entries, but no sound** — the hook ran. The line says what it decided; the fault is
  downstream, so go back to the engine log.
- **No entries at all** — Claude Code never called it. The config is being ignored.

Three things get a hooks config ignored, and none of them report anything:

- **The interpreter cannot be found.** Hooks run without a shell profile, so anything
  conda-activated or installed as the Microsoft Store alias is invisible to them.
  `install.ps1` writes the absolute path to `python.exe` for exactly this reason.
- **A byte-order mark.** `Out-File -Encoding utf8` writes one in PowerShell 5.1, and three
  invisible bytes at the top of `settings.json` are enough for a strict JSON parser to
  reject the whole file. Check with
  `Get-Content settings.json -Encoding Byte -TotalCount 3` — `239 187 191` is the BOM.
- **A missing field.** `PreToolUse` matches on tool name and wants a `matcher`; `Stop`
  fires once per turn and takes none. One malformed entry can invalidate the entire block,
  so a bad `PreToolUse` silences `Stop` along with it.

**None of this stops the voice working.** The watcher does not use hooks at all. If the
hooks never fire on your setup, ignore them entirely — that is why the watcher exists.

## 4. Hooks are read once, at session start

A session that was already open when you installed them will never fire them, however
correct the config is. That needs a genuinely new session — relaunching the app and landing
back in the same conversation is a *resumed* session and keeps its original config.

The watcher has no such problem, which is another reason to prefer it.

## Other things that have actually happened

**It spoke a message that was not mine.** The watcher follows every session touched in the
last fifteen minutes, so a second Claude Code window talks through the same voice. It says
the session's name when the speaker changes. `sessionLabel: "off"` in `config.json` stops
that.

**It started a line and cut it off.** Something took the floor: `say`, `replay` or `stop`
all interrupt deliberately. Ordinary lines queue and wait.

**The voice changed character between sentences.** Each chunk is a separate generation.
That is inherent, and it is why the chunking crosses as few boundaries as possible — but a
very long message will still cross one or two.

**The engine died and took a line with it.** Renaming or deleting a voice's folder while it
is being spoken will do it: the native layer reads the embedding file on every generation.
Stop it first.

**The panel said git is not on PATH, and git works fine in my terminal.** Both are true. The
panel is started from the Desktop shortcut, so it inherits Explorer's PATH from the registry;
your terminal inherits whatever started it, and Claude Code brings a git of its own for its
children. The updater looks in the usual install folders now and pulls anyway —
[listing it properly](updating.md#when-it-says-git-is-not-on-path) is one line, and worth it.

**Two engines, everything said twice.** `HTTPServer` sets `SO_REUSEADDR`, which on Windows
lets a *second* process bind a port already in use rather than failing. The server refuses
the steal now, but a stray old process is worth ruling out — `status` reports the pid.

## Starting clean

```powershell
python voice_cli.py kill      # unload the model
python voice_cli.py start     # load it again
python voice_cli.py say "testing"
```

If `say` speaks and your answers do not, the fault is in what is being *watched*, not in
the speaking. If `say` is silent too, it is the engine or the audio device.
