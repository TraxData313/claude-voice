---
description: Turn Claude's spoken voice on/off, or pick which voice it uses
argument-hint: "help | on | off | status | panel | list | set <voice> | volume <0-100> | repeat | repeat-all | say <text> | stop | kill"
allowed-tools: Bash(__PYTHON__:*)
---

Run the voice control CLI with the user's arguments and report what it says.

```bash
__PYTHON__ "__REPO__/voice_cli.py" $ARGUMENTS
```

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
- Report the CLI's output plainly. Do not re-run the command to "verify" --
  state is written synchronously.
