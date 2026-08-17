---
description: Fetch the speech engine and model, so the installed plugin can actually talk
argument-hint: "[--whatif] [-Build system] [-ProjectDir <path>]"
allowed-tools: Bash(powershell:*), Bash(pwsh:*)
disable-model-invocation: true
---

Install the parts a plugin download cannot carry: the speech engine, the model, and
Python if this machine has none. Run once, after installing the plugin.

**Before running it, tell the user what it will do and wait for them to agree.** This
fetches about 3 GB. Nobody should discover that from their connection.

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/setup.ps1" $ARGUMENTS
```

`--whatif` maps to the script's own `-WhatIf`: it prints every step it would take and
writes nothing. Offer that first to anyone who hesitates.

## What to say while it runs

Ten minutes, nearly all of it download — 0.66 GB of engine and 2.4 GB of model, both
resuming where they stopped if the connection drops. No administrator rights at any
point. It needs **Windows and an NVIDIA card**: both engine builds are CUDA builds and
there is no CPU one, so on other hardware this fails at model load rather than at the
door. If the user is on AMD, Intel graphics or a Mac, say so before it downloads
anything rather than after.

## Afterwards

The user ends up with a second, unnamespaced `/voice` command as well as this plugin's
`/claude-voice:voice`. That is expected, not a fault: `setup.ps1` performs the full
install, and the plugin's own copy of the command is separate. Both drive the same
engine, and neither interferes with the other. Say so if they ask, rather than
suggesting something went wrong.

Then hand over:

- `/claude-voice:voice on` starts the engine. The first model load takes 40-60 seconds.
- `/claude-voice:voice panel` opens the window showing what is being said.
- `/claude-voice:voice list` shows the voices; `set <name>` picks one.

## Check the git line while you are here

`setup.ps1` prints one. If it says git is installed but not on the PATH the panel inherits, or
that there is none at all, that concerns `/voice update` and nothing else — the panel runs
from a Desktop shortcut and does not have your PATH, so git working in your shell does not
mean the update button will. What to do about each answer, and what to ask before touching
anyone's PATH, is **[step 6 of the
tour](https://github.com/TraxData313/claude-voice/blob/main/docs/tour.md)**.

If setup fails, read what it printed rather than running it again — it skips whatever
already succeeded, so a second run is cheap, but a failure that repeats has a cause
worth naming. **[When it goes quiet →](https://github.com/TraxData313/claude-voice/blob/main/docs/troubleshooting.md)**
