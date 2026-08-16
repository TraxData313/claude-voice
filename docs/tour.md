# Setting it up, and giving the tour

**This page is addressed to Claude.** A user has pasted the line from the README asking you
to set this up and show them around. Work through it in order and keep it brisk — the point
is that they hear it working within a few minutes, not that they read a manual.

## 0. You may have only just arrived

If they pasted the one-liner from the README, you cloned this repo a moment ago and are
reading this out of it. Everything below runs **from the repo folder**, so `cd` into it
first. If you are already there, carry on.

Windows only. If this is not Windows, say so now — none of the rest will work.

## 1. Set it up

```powershell
.\setup.ps1
```

One command, and it does the lot: finds Python (or installs it per-user, no admin), fetches
Qwen-TTS Studio and the model, writes the config, adds `/voice`, puts an icon on the
Desktop, opens the panel, and loads the model. It skips anything already there, so it is
safe to run again.

Two things about the wait, because they will be watching a quiet terminal:

- **It is roughly 3 GB of download** on a cold machine — ~660 MB of engine, ~2.4 GB of
  model. **Tell them that before you start it**, and that it resumes if it drops. Ten
  minutes is normal; an hour on a bad connection is not a hang.
- **PowerShell may refuse to run it** — an execution policy, not a permission. Retry with
  `powershell -ExecutionPolicy Bypass -File .\setup.ps1`, and if group policy blocks even
  that, `Get-Content .\setup.ps1 -Raw | Invoke-Expression` works, because the rule is about
  running script *files*.

Useful flags, if the situation calls for one: `-ProjectDir $env:USERPROFILE` to speak in
every project rather than this one, `-Build system` if they already have the CUDA runtime
and want the 268 MB Studio instead of 663, `-PythonExe` or `-StudioDir` if your own checks
found either somewhere unusual.

It also installs hooks, which **may never fire** depending on the client. That is expected
and does not matter — the watcher does not use them.

## 2. Check it really is up

```powershell
python voice_cli.py status
```

`setup.ps1` has already turned it on, so this is confirmation, not action. You want
`engine: ready` and *following sessions itself*. **Do not tell them it is speaking until you
have seen that** — you cannot hear it, and it is silent when it fails.

If the engine is not ready, `python voice_cli.py on` loads it; cold, that takes 40–60
seconds and the command waits.

## 3. Start writing to be heard — from your very next message

The installer has just written the rules into `~\.claude\CLAUDE.md`, which every *future*
session loads at startup. **You are not a future session.** That file was read before it
existed for you, so nothing has told you any of this, and the first thing the user will hear
is whatever you write next. Read [writing for the ear](writing-for-the-ear.md) now, and
apply it from here on:

- **End every answer longer than a few lines with a `## TL;DR`.** That section alone is
  spoken; everything above it stays on screen. An answer with no `## TL;DR` is read out
  **in full** — so an answer full of paths, flags and file names with no summary is a wall
  of technical noise read at someone who cannot see it. This is the single mistake that
  makes people turn it off in the first five minutes.
- **Keep the summary self-contained**: what changed, whether it works, what to do next, in
  plain sentences, five at most, with no paths or flags in it.
- **Give the short lines between your tool calls a verb.** They are spoken exactly as
  written: "Now I'm writing the fix", not "Now the fix itself".

## 4. Give the tour — out loud, four things

They are listening now, so this is the demonstration. Keep every line to a sentence or two
of plain speech; those lines *are* the tour. Cover exactly these, in this order:

1. **That it is working, and who is talking.** Name the current voice. The panel is open in
   front of them — say so, and that it shows what is being said and has the volume slider.
2. **How to change voice** — `/voice set max`, and that any unambiguous substring works.
   Switch once so they hear the difference, then switch back if they prefer.
3. **How to quiet it** — `/voice stop` cuts off the line being spoken and the voice stays
   on; `/voice off` stops it speaking at all.
4. **Where everything else is** — `/voice help` lists every command.

Then stop. Do not read the whole command list aloud; that is what `help` is for.

## 5. Ask them once about update checks

They installed this by cloning a repo, so nothing tells them when a newer version exists.
There is a check for that, and **it is off**, because the whole promise of this project is
that it runs on their machine and tells nobody about it. Turning it on is theirs to decide,
not yours, so **ask, in one sentence, and take the answer**:

> "There's a version check — once a week it asks GitHub whether there's a newer
> claude-voice. It's off right now, and it's the only thing here that would ever use the
> network. Want it on?"

- **Yes** → `python voice_cli.py update on`
- **No, or no clear answer** → leave it. Say that `/voice update` looks whenever they ask,
  and move on.

Do not sell it, do not ask twice, and do not turn it on because it seems helpful. Somebody
who chose an offline voice for a reason has already answered this.

## 6. Worth mentioning once, then leaving alone

- The voice is **one setting shared by every session**. Changing it anywhere changes it
  everywhere, and it survives a reboot.
- The **engine does not** survive a reboot. After restarting the machine they say `on` once,
  or press the button in the panel.
- The panel can be closed and reopened from the **Abby for Claude** icon on the Desktop.
- The **version is in the panel's bottom right corner**, and `/voice update` is how a newer
  one arrives — it pulls and restarts the engine, which is the step that is easy to miss.
- If it ever goes quiet, `status` says whether the engine died, and `on` revives it.

## If something is wrong

`logs\speak-server.log` is the engine's account of itself. Silence with no error is covered
in [troubleshooting](troubleshooting.md) — work down it in order rather than guessing, and
tell them plainly what you found rather than reassuring them it is fine.
