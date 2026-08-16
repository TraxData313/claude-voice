---
description: Turn Claude's spoken voice on/off, or pick which voice it uses
argument-hint: "on | off | status | list [filter] | set <voice-id> | replay | replay-all | say <text> | stop | kill"
allowed-tools: Bash(__PYTHON__:*)
---

Run the voice control CLI with the user's arguments and report what it says.

```bash
__PYTHON__ "__REPO__/voice_cli.py" $ARGUMENTS
```

Notes for interpreting the result:

- With no arguments this prints status.
- `on` starts the engine host if it is cold; the first model load takes 40-60s
  and the command waits for it.
- `list` shows every available voice; `set <id>` accepts any unambiguous
  substring, so `set sib` works.
- `replay` says the last answer's summary again; `replay-all` says the whole
  message with no length cap. Both take a number to reach further back, and
  `stop` cuts either off.
- Report the CLI's output plainly. Do not re-run the command to "verify" --
  state is written synchronously.
