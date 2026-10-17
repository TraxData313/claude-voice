# Getting the newer one

```
/voice update            # is there a newer one, and what is in it
/voice update --apply    # take it, and put the engine back
```

Or the button at the bottom of the panel. What `--apply` does depends on what this copy is:

| | |
|---|---|
| **An install** (the usual) | fetches GitHub's zip of `main`, and hands it to *that* release's `installer.py`, which stages the new `app\` beside the running one, swaps the two, re-registers it and restarts the engine if it was running. Your settings, logs and voices in `%LOCALAPPDATA%\claude-voice` are never touched, the panel stays open, and an update that fails leaves what was there before |
| **A git checkout** | a careful `git pull`, then `kill` and `start` — see [below](#a-git-checkout) |

A release that needs a different Python than the one installed says so and stops: run
`install.ps1` again, which fetches the new one and keeps everything else.

An install made before 2.0 — by `setup.ps1` or `ClaudeVoiceSetup.exe`, straight into
`%LOCALAPPDATA%\Programs\claude-voice` — cannot update itself into the new layout. Run the
one-line install from [install.md](install.md) once; it moves that copy over and keeps its
settings.

## From the panel, without typing anything

The bottom row of the panel does the whole of it:

| | |
|---|---|
| **check now** → **update to 1.4.2** → **reopen panel** | one button at three stages: it asks, it takes what it found, and then it stands aside |
| **what's new** | appears beside the button when there is something to read — the changelog, *before* you decide |
| *up to date, 2 days ago* | how the last look went, when there is nothing else to say |
| **v1.0.0** | in the far corner, and clickable — it turns the link colour when there is something to take |

The weekly look is a tick in Settings (the three bars, top left), **auto-check for updates**. Ticking it
also looks straight away, so you see one happen rather than waiting a week to find out that
it works. Untick it and nothing in this program ever contacts anything.

If the update is refused — local changes in a checkout's way, usually — the reason appears in
that same row, and the full account goes to `logs\panel.log`.

The panel never checks anything by itself. It reads the answer of whatever check last ran.

## What restarts, and what you restart

Nothing has a *restart button* to go hunting for. There are three things that could need
putting back, and only one of them is ever your problem:

| | |
|---|---|
| **the engine** | **automatic.** The update stops it and starts it again itself, and waits for the model to load — 40–60 seconds, during which the panel says the engine is down. This is the whole reason `--apply` exists |
| **the panel window** | **one click.** It is the process it was started as, so it cannot become the new code by itself. After a successful update its button says `reopen panel`; pressing it saves the window's position, closes it, and opens the new one in the same place |
| **Claude Code** | **only sometimes.** Hooks and the `/voice` command are read when a session starts, so a release that changes them needs a restart of Claude Code — and it will say so, in the panel and on the command line, rather than leaving you to guess. An install's update rewrites them itself; a checkout needs `hooks install` |

From the command line it is the same, minus the window: `update --apply` restarts the engine
and tells you if anything else is outstanding.

## The step people miss

**The engine loaded the code when it started, and goes on running that copy.** It holds a
model in memory and is meant to live for days, so it does not watch its own source. Put new
code under a running engine and nothing whatsoever changes about how it speaks.

So a code update is always: new code, then `kill`, then `start`. That is exactly what
`--apply` does, and why it exists.

Settings are the other way round — `config.json` is re-read whenever it changes, so a change
to the voice, the volume or `maxChars` takes effect with nothing restarted.

## What version am I on?

```
/voice version
```

`version.json` in the program's `app\` folder (or the checkout) is the truth, `/voice status`
shows it on the `updates` line, and the panel prints it in the bottom right corner. **Quote it in a bug report** —
before this existed, the answer to "which version?" was "whatever was on `main` that day".

## The check, and what it sends

**It is off.** Nothing here contacts anything until you say so, in one of two ways:

| | |
|---|---|
| `/voice update` | looks **now**. Typing it is the asking, so this works whatever the setting says |
| `/voice update on` | allows one look a week, without being asked again |

`update on` is the only thing that makes this project use the network on its own.

What goes out, when it goes out at all, is a single GET of one public file:

```
https://raw.githubusercontent.com/TraxData313/claude-voice/main/version.json
```

- **No query string, no identifier, no version, no operating system, no counter.** The
  request cannot tell one machine from another. The `User-Agent` is the literal string
  `claude-voice (update check)` and carries nothing else — deliberately, since it is the one
  place a version or a platform would normally be written.
- **Nothing is sent back.** Not what you said, not what was spoken, not which voice, not how
  often. There is no server on the other end of this; it is a file on a CDN.
- **GitHub sees what any web request shows it**: your IP address and the time. That is the
  whole of the cost, and it is why this is off by default rather than merely documented.
- **Every contact is written down** in `logs\update.log`, one line, with the time and what
  came of it. If that file is empty, nothing has ever been contacted — the promise is
  auditable rather than just stated.

The answer is cached in `logs\update-check.json`, and the weekly look is made **in a detached
background process** while you are running some other voice command. Nothing ever waits on
the network: offline, `status` is as instant as it is online, and a failed check is a line in
a log rather than an error in your way.

## Turning it off again

`/voice update off`, or set `"updateCheck": false` in `config.json`. `"updateCheckDays": 0` also means never.

## A git checkout

A clone updates by pulling, and `--apply` does that carefully: `--ff-only`, refusing a dirty
tree or a branch with nowhere to pull from, checking that `HEAD` actually moved before
claiming anything, then `kill` and `start`. It never installs anything into the clone.

- **Local changes to tracked files** stop it. It lists them and pulls nothing. Commit or
  `git stash` first. Untracked files — a voice folder you added, your own notes — are never in
  the way.
- **A branch tracking nothing**, or a pull that will not fast-forward, stops it. Both mean this
  copy has a history of its own, and untangling that by hand is the only safe answer.
- **It does not rewrite the hooks.** When a release changes them (`needsSetup`, or a changed
  `templates\` or install script), it says to run `python voice_cli.py hooks install` and
  restart Claude Code.

Without git, or for a copy that is not a clone: download
<https://github.com/TraxData313/claude-voice/archive/refs/heads/main.zip>, copy it over the
folder, run `python voice_cli.py hooks install`, then `kill` and `start`. Nothing of yours is in
that zip.

To stop running from the clone and use an install instead: `python voice_cli.py kill`, close the
panel, then `powershell -ExecutionPolicy Bypass -File .\install.ps1` from the clone. It carries
the clone's settings over and takes the hooks and the registration with it.
**[A developer's checkout →](install.md#a-developers-checkout)**

### When it says git is not on PATH

The panel's button can say that while the very same pull works in your terminal, and both are
telling the truth. **A PATH belongs to a process.** The panel is started from the Desktop
shortcut, so it inherits Explorer's — built from the registry at login. A terminal inherits
whatever started it, and Claude Code hands its own children a PATH with a git of its own
prepended. Git for Windows lists itself only if you ticked the box during install, so a
machine can use git all day and have nothing about git in the registry.

The updater no longer takes PATH's word for it. With no `git` on PATH it looks in
`%LOCALAPPDATA%\Programs\Git\cmd`, `%ProgramFiles%\Git\cmd`, the 32-bit folder and GitHub
Desktop's bundled copy, and prints which one it used — so this now sorts itself out, and the
line naming the folder is the explanation of why the button used to fail.

Listing it properly is still worth doing, because every other tool on that machine shares the
blind spot. This is the check, and the fix:

```powershell
# what the panel sees. Nothing back means git is not on the registry PATH
@([Environment]::GetEnvironmentVariable('Path','User'),
  [Environment]::GetEnvironmentVariable('Path','Machine')) -join ';' -split ';' |
  Where-Object { $_ -and (Test-Path -LiteralPath ($_.TrimEnd('\') + '\git.exe')) }

# put it there -- your own hive, no admin. Then reopen the panel
[Environment]::SetEnvironmentVariable('Path',
  [Environment]::GetEnvironmentVariable('Path','User').TrimEnd(';') +
  ';' + "$env:LOCALAPPDATA\Programs\Git\cmd", 'User')
```

Only processes started afterwards see it, and the shortcut counts as one once Explorer
notices; signing out and in settles it if it does not.

**Not `setx PATH "%PATH%;..."`,** which is the advice you will find everywhere and is wrong
twice over: `setx` truncates at 1024 characters, and `%PATH%` there is the machine and user
paths already joined — so it copies every machine entry into your user variable and you now
have two divergent copies of the same list.

## Releasing one (for whoever maintains this)

A release is four files, one commit, and one annotated tag:

1. **`version.json`** — bump `version`, set `date`, write a `headline` that will be *read
   aloud*, and two or three `notes`. Set `needsSetup` to `true` if the release touches the
   hooks, `templates\` or the install scripts — a checkout's pull does not reinstall those. Not
   for `speaking-notes.md`: sessions read it through their hook, so new wording reaches them
   with nothing reinstalled.
2. **`CHANGELOG.md`** — a new `## <version> — <date>` section at the top. `--apply` prints
   this section to whoever just took the update, so write it for them rather than for git.
3. **`.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`** — set both plugin
   versions to the same number.
4. **Tag the release commit as `v<version>`** after the checks pass, then push the branch
   and tag together.

Versions are plain numbers compared piece by piece (`1.10.0` beats `1.9.9`). A suffix like
`-beta` is ignored, so do not rely on one to mean anything.

There is no separate GitHub Release in this scheme. The annotated tag is the plugin release
marker, and `version.json` remains what the update checker reads. The one GitHub Release that
matters is v1.16.0: it must stay "Latest" with its `ClaudeVoiceSetup.exe`, because Immersive AI
3.4 to 3.6 download it from there ([setup-app/README.md](../setup-app/README.md)).
