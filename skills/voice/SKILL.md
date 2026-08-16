---
description: Turn Claude's spoken voice on/off, or pick which voice it uses
argument-hint: "help | on | off | status | panel | list | set <voice> | volume <0-100> | repeat | repeat-all | say <text> | stop | kill | version | update"
allowed-tools: Bash(python:*)
disable-model-invocation: true
---

Run the voice control CLI with the user's arguments and report what it says.

```bash
python "${CLAUDE_PLUGIN_ROOT}/voice_cli.py" $ARGUMENTS
```

If that fails because `python` is not found, the interpreter is installed but not on
this shell's PATH — a common case, since a hook or a tool call gets no shell profile.
Find it (`where python`, or `%USERPROFILE%\miniconda3\python.exe`) and use the full
path rather than reporting the tool as broken.

If it fails because the engine or the model is missing, this copy has the plugin but has
not been set up yet — a plugin install carries the command, not the 3 GB of engine and
model. Tell the user to run **`/claude-voice:setup`**, which does exactly that and
nothing else. Say it plainly and once; do not retry the CLI, and do not start the
download on their behalf without being asked.

Notes for interpreting the result:

- With no arguments this prints status. `help` prints every command, grouped.
- `on` starts the engine host if it is cold; the first model load takes 40-60s
  and the command waits for it.
- `list` shows every available voice; `set <id>` accepts any unambiguous
  substring, so `set sib` works.
- `panel` opens a small always-on-top window and returns at once; it shows what
  is playing, what is queued, and lets the user mute a session, change voice or
  drag the volume without typing. Closing it changes nothing else.
- `volume <0-100>` is this app's own slider in the Windows mixer, so it takes
  effect in the middle of a sentence rather than at the next one.
- `replay` says the last answer's summary again; `replay-all` says the whole
  message with no length cap. Both take a number to reach further back, and
  `stop` cuts either off.
- `version` prints what this copy is and contacts nobody. `update` is the one
  command here that uses the network: it asks GitHub whether a newer version
  exists, and `update --apply` pulls it and restarts the engine. `update
  on|off` governs only the weekly unprompted look, which is off by default.
  Never turn that on for the user without being asked to.
- Report the CLI's output plainly. Do not re-run the command to "verify" --
  state is written synchronously.
