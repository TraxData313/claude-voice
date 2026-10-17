---
description: Install claude-voice as a program, so the installed plugin can actually talk
argument-hint: "[-WhatIf] [-Engine pocket|qwen|breeze] [-Desktop] [-StartWithWindows]"
allowed-tools: Bash(powershell:*), Bash(pwsh:*)
disable-model-invocation: true
---

Install the parts a plugin download cannot carry: a private Python, the speech engine, and the
program itself, with a Start menu entry and an Uninstall in Settings → Apps. Run once, after
installing the plugin.

**Before running it, tell the user what it will do and wait for them to agree.** It fetches
python.org's Python (about 30 MB) and Pocket TTS with torch's CPU build (a few hundred MB) into
`%LOCALAPPDATA%\Programs\claude-voice`, and touches no other Python on the machine.

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/install.ps1" $ARGUMENTS
```

The flag is there because Windows ships with script files switched off; it lasts for that one
PowerShell and changes no setting. `-WhatIf` prints what it would do and writes nothing — offer
that first to anyone who hesitates.

## What to say while it runs

A few minutes, nearly all of it download. No administrator rights at any point, and it works
with Smart App Control on: the Python is signed by the Python Software Foundation and the
engine's packages are pinned to builds Smart App Control allows. Pocket TTS, the default, runs on
the CPU; Abby speaks, Cyrillic does not.

`-Engine qwen` needs an NVIDIA card and Smart App Control off (its DLLs are unsigned and refused;
the engine then falls back to Pocket). `-Engine breeze` is the one that laughs: about 11 GB and a
16 GB card.

## Afterwards

The user ends up with a plain `/voice` command as well as this plugin's `/claude-voice:voice`.
That is expected: the install writes its own. Both drive the same engine. The hooks are read when
a session starts, so they restart Claude Code, and then:

- `/voice on` starts the engine.
- `/voice panel` opens the window showing what is being said (also **Abby for Claude** in the
  Start menu).
- `/voice list` shows the voices; `set <name>` picks one.

If the install fails, read what it printed rather than running it again — the last lines say
what stopped it, and `%LOCALAPPDATA%\claude-voice\setup.log` has the rest. **[When it goes quiet
→](https://github.com/TraxData313/claude-voice/blob/main/docs/troubleshooting.md)**
