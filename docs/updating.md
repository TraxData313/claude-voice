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
| **check now** → **update to 1.4.2** → **reopen panel** | one button at three stages: it asks, it takes what it found, and then it stands aside |
| **what's new** | appears beside the button when there is something to read — the changelog, *before* you decide |
| *up to date, 2 days ago* | how the last look went, when there is nothing else to say |
| **v1.0.0** | in the far corner, and clickable — it turns the link colour when there is something to take |

The weekly look is a tick behind the settings cog, **auto-check for updates**. Ticking it
also looks straight away, so you see one happen rather than waiting a week to find out that
it works. Untick it and nothing in this program ever contacts anything.

If the pull is refused — local changes in the way, usually — the reason appears in that same
row, and the full account goes to `logs\panel.log`.

The panel never checks anything by itself. It reads the answer of whatever check last ran.

## What restarts, and what you restart

Nothing has a *restart button* to go hunting for. There are three things that could need
putting back, and only one of them is ever your problem:

| | |
|---|---|
| **the engine** | **automatic.** The update stops it and starts it again itself, and waits for the model to load — 40–60 seconds, during which the panel says the engine is down. This is the whole reason `--apply` exists |
| **the panel window** | **one click.** It is the process it was started as, so it cannot become the new code by itself. After a successful update its button says `reopen panel`; pressing it saves the window's position, closes it, and opens the new one in the same place |
| **Claude Code** | **only sometimes.** Hooks and the `/voice` command are read once, at session start, so a release that changes them needs a restart of Claude Code — and it will say so, in the panel and on the command line, rather than leaving you to guess |

From the command line it is the same, minus the window: `update --apply` restarts the engine
and tells you if anything else is outstanding.

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

## When it says git is not on PATH

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
blind spot. `setup.ps1` reports it, and this is the same check and the fix:

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

## If you have no git

`--apply` needs git and a clone. Without either, it says so and stops rather than guessing:

1. Download <https://github.com/TraxData313/claude-voice/archive/refs/heads/main.zip>
2. Unpack it and copy the contents over your folder, replacing what is there.
3. Run `setup.ps1` again, then `voice_cli.py kill` and `start`.

Nothing of yours is in that zip, so this does not touch `config.json`, your logs, or any
voice you made yourself.

## Why this is a `git pull` and not an installer

Because **the way a thing is installed sets the way it is updated**, and this one is
installed by cloning a repo. A checkout that updates by pulling is the normal shape for a
tool distributed this way — it is how oh-my-zsh, nvm, pyenv and most editor plugin managers
update themselves, and it is what the folder on disk already is.

An installer-style updater — download a zip, swap the files — would be the wrong tool twice
over. It would have to reimplement what git already does correctly (know what changed, refuse
to trample your edits, be interruptible), and it would throw away the two things that come
free with a checkout: **you can see exactly what changed**, and **you can go back** with
`git checkout <old-sha>` if a release turns out badly.

What *would* be sloppy is a bare `git pull` fired at a button. That can trample local edits,
stop half-way in a merge conflict, or succeed while the running program carries on being the
old one. So this one is `--ff-only`, refuses a dirty tree, refuses a branch with nowhere to
pull from, checks that `HEAD` actually moved before claiming anything, and restarts what has
to be restarted. The zip route above exists for copies that are not checkouts at all.

## What `--apply` will not do

It is deliberately timid, because the alternative is losing someone's work:

- **Local changes to tracked files** stop it. It lists them and pulls nothing. Commit or
  `git stash` first. Untracked files — a voice folder you added, your own notes — are not in
  the way and are never mentioned.
- **A branch tracking nothing**, or a pull that will not fast-forward, stops it. Both mean
  this copy has a history of its own, and untangling that by hand is the only safe answer.
- **It will not re-run the installer.** A `git pull` updates the repo, but the `/voice`
  command and the hooks were *copied out* of it at setup time. When a release changes one of
  those, `--apply` says to run `setup.ps1` again — and then to restart Claude Code, because
  hooks and slash commands are read once, at session start.
- **Except the note, which now reinstalls itself.** `~\.claude\CLAUDE.md` is rewritten from
  `speaking-notes.md` by `--apply`, and again on every engine start, so a release that
  changes how Claude is told to write reaches you by restarting Claude Code and nothing
  else. It was the worst one to leave to a hand-run installer: a session quietly following
  last month's rules looks exactly like a session.
  **[What that block says →](writing-for-the-ear.md)**

## Releasing one (for whoever maintains this)

A release is two files and one commit:

1. **`version.json`** — bump `version`, set `date`, write a `headline` that will be *read
   aloud*, and two or three `notes`. Set `needsSetup` to `true` if the release touches
   `commands\` or either installer. Not for `speaking-notes.md` any more — the note puts
   itself back after a pull, so a release that only changes the wording needs no installer.
2. **`CHANGELOG.md`** — a new `## <version> — <date>` section at the top. `--apply` prints
   this section to whoever just took the update, so write it for them rather than for git.

Versions are plain numbers compared piece by piece (`1.10.0` beats `1.9.9`). A suffix like
`-beta` is ignored, so do not rely on one to mean anything.

There are no tags and no GitHub Releases in this scheme, on purpose: one file that the tool
itself reads is one thing to keep true, and a tag nobody parses is another thing to forget.
