# Getting the newer one

This was installed by cloning a repo, so updating it is a `git pull` — plus one step that
is easy to miss and makes the pull look like it did nothing.

```powershell
python voice_cli.py update            # is there a newer one, and what is in it
python voice_cli.py update --apply    # take it, and put the engine back
```

Or `/voice update` inside Claude Code. Both are the same tool.

## From the panel, without typing anything

The bottom row of the panel does the whole of it:

| | |
|---|---|
| ☐ **auto-check** | the weekly look. Ticking it also looks **now**, so you see one happen rather than waiting a week to find out it works |
| **check now** → **update to 1.4.2** | one button at two stages: it asks, and then it takes what it found |
| **what's new** | appears beside the button when there is something to read — the changelog, *before* you decide |
| *up to date, 2 days ago* | how the last look went, when there is nothing else to say |
| **v1.0.0** | in the far corner, and clickable |

Pressing the update button pulls and restarts the engine, and then says `updated to 1.4.2 —
reopen` in that same row: the panel is running the code it was started with, so the window
itself has to be closed and opened again to become the new one. If the pull is refused — local
changes in the way, usually — the reason appears there too, and the full account goes to
`logs\panel.log`.

The panel never checks anything by itself. It reads the answer of whatever check last ran.

## The step people miss

**The engine loaded the code when it started, and goes on running that copy.** It holds a
2 GB model in memory and is meant to live for days, so it does not watch its own source. Pull
new code under a running engine and nothing whatsoever changes about how it speaks.

So a code update is always: pull, then `kill`, then `start`. That is exactly what `--apply`
does, and why it exists.

Settings are the other way round — `config.json` is re-read on every sweep, so a change to
the voice, the volume or `maxChars` takes effect with nothing restarted.

## What version am I on?

```powershell
python voice_cli.py version
```

`version.json` in the repo folder is the truth, `/voice status` shows it on the `updates`
line, and the panel prints it in the bottom right corner. **Quote it in a bug report** —
before this existed, the answer to "which version?" was "whatever was on `main` that day".

## The check, and what it sends

**It is off.** Nothing here contacts anything until you say so, in one of two ways:

| | |
|---|---|
| `/voice update` | looks **now**. Typing it is the asking, so this works whatever the setting says |
| `/voice update on` | allows one look a week, without being asked again |

`update on` is the only thing that makes this project use the network on its own. The
installer can set it at install time with `-UpdateChecks`, and says which way it went either
way.

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

```powershell
python voice_cli.py update off
```

Or set `"updateCheck": false` in `config.json`. `"updateCheckDays": 0` also means never.

## If you have no git

`--apply` needs git and a clone. Without either, it says so and stops rather than guessing:

1. Download <https://github.com/TraxData313/claude-voice/archive/refs/heads/main.zip>
2. Unpack it and copy the contents over your folder, replacing what is there.
3. Run `setup.ps1` again, then `voice_cli.py kill` and `start`.

Nothing of yours is in that zip, so this does not touch `config.json`, your logs, or any
voice you made yourself.

## What `--apply` will not do

It is deliberately timid, because the alternative is losing someone's work:

- **Local changes to tracked files** stop it. It lists them and pulls nothing. Commit or
  `git stash` first. Untracked files — a voice folder you added, your own notes — are not in
  the way and are never mentioned.
- **A branch tracking nothing**, or a pull that will not fast-forward, stops it. Both mean
  this copy has a history of its own, and untangling that by hand is the only safe answer.
- **It will not re-run the installer.** A `git pull` updates the repo, but the `/voice`
  command, the hooks and the note in `~\.claude\CLAUDE.md` were *copied out* of it at setup
  time. When a release changes one of those, `--apply` says to run `setup.ps1` again — and
  then to restart Claude Code, because hooks and slash commands are read once, at session
  start.

## Releasing one (for whoever maintains this)

A release is two files and one commit:

1. **`version.json`** — bump `version`, set `date`, write a `headline` that will be *read
   aloud*, and two or three `notes`. Set `needsSetup` to `true` if the release touches
   `commands\`, `speaking-notes.md`, or either installer.
2. **`CHANGELOG.md`** — a new `## <version> — <date>` section at the top. `--apply` prints
   this section to whoever just took the update, so write it for them rather than for git.

Versions are plain numbers compared piece by piece (`1.10.0` beats `1.9.9`). A suffix like
`-beta` is ignored, so do not rely on one to mean anything.

There are no tags and no GitHub Releases in this scheme, on purpose: one file that the tool
itself reads is one thing to keep true, and a tag nobody parses is another thing to forget.
