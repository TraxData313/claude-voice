# Setting it up, and giving the tour

**This page is addressed to Claude.** A user has pasted the line from the README asking you
to set this up and show them around. Work through it in order and keep it brisk — the point
is that they hear it working within a few minutes, not that they read a manual.

## 1. Check the prerequisites before installing

- **Python** — you need the absolute path to `python.exe`, not the word `python`. Anything
  conda-activated or the Microsoft Store alias will not resolve when spawned without a
  shell profile.
- **Qwen-TTS Studio** — look in `Downloads`, `Program Files`, `%LOCALAPPDATA%\Programs`. It
  is the folder containing `app\` and `runtime\`.
- **A talker model** — `~\.qwen-tts-studio\models` should hold a `*talker*.gguf`.

If Studio or the model is missing, **stop and say so plainly**. They have to launch Studio
once and let the Welcome screen fetch `qwen-talker-1.7b-base` — this cannot be done for
them, and the 0.6b model is the wrong size. Point them at the README quote box and wait.

## 2. Install

```powershell
.\install.ps1
```

Add `-StudioDir` or `-PythonExe` if your checks found either somewhere unusual, and
`-ProjectDir $env:USERPROFILE` if they want it in every project rather than this one.

The installer writes the config and adds the `/voice` command. It also installs hooks,
which **may never fire** depending on the client — that is expected and does not matter,
because the watcher does not use them.

## 3. Turn it on

```powershell
python voice_cli.py on
```

Cold, this takes 40–60 seconds and the command waits. Then confirm with `status` that the
engine is `ready` and that it is *following sessions itself*. Do not tell them it is
speaking until you have seen that — you cannot hear it, and it is silent when it fails.

## 4. Give the tour — out loud, four things

They are listening now, so this is the demonstration. Keep every line to a sentence or two
of plain speech; those lines *are* the tour. Cover exactly these, in this order:

1. **That it is working, and who is talking.** Name the current voice.
2. **How to change voice** — `/voice set max`, and that any unambiguous substring works.
   Switch once so they hear the difference, then switch back if they prefer.
3. **How to quiet it** — `/voice stop` cuts off the line being spoken and the voice stays
   on; `/voice off` stops it speaking at all.
4. **Where everything else is** — `/voice help` lists every command.

Then stop. Do not read the whole command list aloud; that is what `help` is for.

## 5. Worth mentioning once, then leaving alone

- The voice is **one setting shared by every session**. Changing it anywhere changes it
  everywhere, and it survives a reboot.
- The **engine does not** survive a reboot. After restarting the machine they say `on` once.
- If it ever goes quiet, `status` says whether the engine died, and `on` revives it.

## If something is wrong

`logs\speak-server.log` is the engine's account of itself. Silence with no error is covered
in [troubleshooting](troubleshooting.md) — work down it in order rather than guessing, and
tell them plainly what you found rather than reassuring them it is fine.
