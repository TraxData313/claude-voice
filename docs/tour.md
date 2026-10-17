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
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

One command, and it does the lot. The folder you cloned is only the **source**: it installs
claude-voice as a program into `%LOCALAPPDATA%\Programs\claude-voice`, on a private copy of
python.org's Python that it fetches and checks (SHA-256 and signature), installs Pocket TTS into
that Python, lists it in Settings → Apps with an Uninstall, puts **Abby for Claude** in the Start
menu, installs the hooks and `/voice`, starts the engine and opens the panel. No administrator
rights, and it works with Smart App Control on. Running it again updates in place.

The flag is there because Windows ships with script files switched off; it applies to that one
PowerShell and changes no setting. Say so if they ask.

Two things about the wait, because they will be watching a quiet terminal:

- **It is a few hundred MB** on a cold machine — Python, then torch's CPU build — and Pocket's
  voice model comes down the first time the engine loads. Tell them before you start.
- **With an NVIDIA card and Smart App Control off**, `-Engine qwen` gets Qwen (Studio and a
  2.4 GB model; Cyrillic works). With Smart App Control on, Qwen's DLLs are refused and the
  engine falls back to Pocket — so on such a machine, do not offer it. `-Engine breeze` is the one
  that laughs (11 GB, a 16 GB card).

## 2. Check it really is up

```powershell
& "$env:LOCALAPPDATA\Programs\claude-voice\runtime\python.exe" "$env:LOCALAPPDATA\Programs\claude-voice\app\voice_cli.py" status
```

The installer's last lines already said `engine : ready on port 8765` or why not; this is
confirmation. **Do not tell them it is speaking until you have seen it ready** — you cannot hear
it, and it is silent when it fails. The hooks are read when a session starts, so **this session
may not be wired up yet**. Ask them to restart Claude Code and come back to this conversation
(`claude --continue`, or reopen it in the app): a resumed session runs the new hooks, and the
tour carries on from step 3. Until then nothing you write is heard.

## 3. Start writing to be heard — from your very next message

The SessionStart hook hands these rules to every *future* session. **You are not a future
session** — this one started before the hooks existed — so nothing has told you any of this, and the first thing the user will hear
is whatever you write next. Read [writing for the ear](writing-for-the-ear.md) now, and
apply it from here on:

- **End every answer longer than a few lines with a `## TL;DR`.** That section alone is
  spoken; everything above it stays on screen. An answer with no `## TL;DR` is read out
  **in full** — so an answer full of paths, flags and file names with no summary is a wall
  of technical noise read at someone who cannot see it. This is the single mistake that
  makes people turn it off in the first five minutes.
- **Keep the summary self-contained**: what changed, whether it works, what to do next, in
  plain sentences, five at most, with no paths or flags in it.
- **What is heard is the end of each turn**, and a prompt that is waiting on them. The short
  lines between tool calls are silent unless they turn on `narrate transcript on`; give them a
  verb anyway ("Now I'm writing the fix", not "Now the fix itself"), in case they do.

## 4. Give the tour — out loud, four things

They are listening now, so this is the demonstration. Only the end of each of your turns is
heard, so give the tour as short turns, one point each, ending on its own `## TL;DR`. Cover
exactly these, in this order:

1. **That it is working, and who is talking.** Name the current voice. The panel is open in
   front of them — say so, and that it shows what is being said and has the volume slider.
2. **How to change voice** — `/voice set max`, and that any unambiguous substring works.
   Switch once so they hear the difference, then switch back if they prefer.
3. **How to quiet it** — `/voice stop` cuts off the line being spoken and the voice stays
   on; `/voice off` stops it speaking at all.
4. **Where everything else is** — `/voice help` lists every command.

Then stop. Do not read the whole command list aloud; that is what `help` is for.

## 5. Ask them once about update checks

Nothing tells them when a newer version exists. There is a check for that, and **it is off**, because the whole promise of this project is
that it runs on their machine and tells nobody about it. Turning it on is theirs to decide,
not yours, so **ask, in one sentence, and take the answer**:

> "There's a version check — once a week it asks GitHub whether there's a newer
> claude-voice. It's off right now, and it's the only thing here that would ever use the
> network. Want it on?"

- **Yes** → `/voice update on`
- **No, or no clear answer** → leave it. Say that `/voice update` looks whenever they ask,
  and move on.

Do not sell it, do not ask twice, and do not turn it on because it seems helpful. Somebody
who chose an offline voice for a reason has already answered this.

## 6. Updates need no git

An installed copy updates itself: `/voice update --apply`, or the panel's button, takes the
newest release from GitHub, swaps it in and keeps their settings. Nothing to check here.

## 7. Worth mentioning once, then leaving alone

- The voice is **one setting shared by every session**. Changing it anywhere changes it
  everywhere, and it survives a reboot.
- The **engine does not** survive a reboot. With the voice left on, the first prompt they
  type afterwards starts it again; `on` or the button in the panel does it by hand.
- The panel can be closed and reopened from **Abby for Claude** in the Start menu.
- The **version is in the panel's bottom right corner**, and `/voice update --apply` is how a
  newer one arrives — it swaps the program and restarts the engine, keeping their settings.
- If it ever goes quiet, `status` says whether the engine died, and `on` revives it. So does
  their next prompt, while the voice is on.

## If something is wrong

`%LOCALAPPDATA%\claude-voice\logs\speak-server.log` is the engine's account of itself. Silence with no error is covered
in [troubleshooting](troubleshooting.md) — work down it in order rather than guessing, and
tell them plainly what you found rather than reassuring them it is fine.
