# How a game finds claude-voice

A program that wants to speak through claude-voice has to find it while it is asleep: you cannot
ask a server that is not running where it lives. This page is the contract for that — what
claude-voice writes, where, when, and the order a program should look in. The Immersive AI mod
follows it; anything else can too.

## Why there is a contract at all

Until October 2026 the only record was `where.json`, and only a running engine wrote it. So a game
installed after claude-voice did not find it until somebody had started the engine by hand; a
folder that was moved was "not installed" until it ran again; and an install from before 1.14.0
was invisible for good. Worse, the game's quiet installer found Toni's git clone through that note
and ran the setup inside it — packages into his own Python, and its record of itself rewritten.

The registry key fixes the first three: it is written when claude-voice is installed, not when it
runs. `Kind` fixes the last one: a developer's checkout says what it is, and a game leaves it alone.

## The registry key

`HKCU\Software\claude-voice` — per user, no administrator needed, readable by any program the
user runs.

| Value | Type | What it says |
|---|---|---|
| `Contract` | DWORD | `1`. Goes up only when a value below changes meaning. |
| `InstallDir` | string | The folder claude-voice's code is in (`voice_cli.py` is there). For an install that is `<program>\app`; for a checkout, the checkout. |
| `Kind` | string | `installed` (its setup put it there) or `dev` (somebody's own checkout). |
| `Version` | string | Its version, from `version.json`. |
| `Launcher` | string | The exact command line that starts the engine with no window. The executable comes first and is always quoted, so split it off at the closing quote. For an install, its private, signed Python: `"<program>\runtime\pythonw.exe" "<InstallDir>\voice_cli.py" start`; a packaged build will name its own exe. |
| `Port` | DWORD | The port the engine actually listens on (as of its last start). |
| `PortHome` | DWORD | The port it wants. It differs from `Port` when something else held that one and the engine moved up. |
| `Updated` | string | When any of this was last written, local time. For people, not programs. |

**Who writes it.**

- **The installer** (`installer.py`, which `install.ps1` and the Immersive AI mod run) writes it
  whole through `voice_cli.py register --kind installed`, with `Kind = installed`, and takes it
  over from any other copy — the newest install is the one games should find. It says so when the
  key named a checkout before.
- **`voice_cli.py register`** writes it whole for the copy it is run from. This is how a git
  checkout opts in (`Kind = dev` by default there); nothing registers a checkout unasked.
- **The engine**, on every start, rewrites `Port`, `PortHome`, `Version` and `Updated` — but only
  when the key already names its own folder. A second copy started from somewhere else never
  takes the key over; it only writes `where.json`.

**Who removes it.** The uninstaller (Settings → Apps), when it names the copy being removed; and
`voice_cli.py unregister` (another copy's key only with `--force`).

## where.json

`%LOCALAPPDATA%\claude-voice\where.json`, written by every engine start, whatever copy it is and
whether or not it is registered. Kept for programs that predate the key, and as the second place
to look:

```json
{ "root": "...", "python": "...\\python.exe", "pythonw": "...\\pythonw.exe",
  "port": 8766, "portHome": 8765, "version": "1.16.0" }
```

`port` is the one it actually got. A start that could not bind at all writes `error` instead of
listening — treat that note's port as unknown.

## The order to look in

1. **The registry key.** Use it if `InstallDir\voice_cli.py` exists (or, for a packaged build,
   the executable in `Launcher` does).
2. **`where.json`.** Same check on `root`.
3. **Settings → Apps** — `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\claude-voice`,
   `InstallLocation` — which setups before the key also wrote for non-git installs.
4. **The usual folders**: `%LOCALAPPDATA%\Programs\claude-voice`, `Documents\claude-voice`,
   `%USERPROFILE%\claude-voice`.

Since 2.0, 3 and the first folder of 4 name the *program* folder: when it holds `install.json` and
`app\voice_cli.py`, the app is `app\` and its Python is `runtime\pythonw.exe`. A pre-2.0 install
has `voice_cli.py` at the root.

The first that still holds the app wins.

**Starting it.** Run `Launcher`. Without one (an older install, or a copy found by folder), run
`voice_cli.py start` in that folder with where.json's `pythonw` (or `python`), and failing that a
Python Windows has registered (PEP 514, `Software\Python\PythonCore\<version>\InstallPath`).
**Never bare `python`**: a game started from Steam inherits the PATH from before any Python was
installed since, and `python` then meets the Microsoft Store stub, or nothing.

**Which port.** `Port` from the key (or `port` from where.json, when it is the same copy), then
the program's own setting, then 8765. `/health` answers with `port` too, and only claude-voice's
`/health` has `ready` in it — anything else on that port is somebody else's program.

**A developer's copy** — `Kind = dev`, or a folder with `.git` in it — is found, woken and spoken
through like any other. A game must not install into it, run its setup, or uninstall it. Since 2.0
nothing installs *into* any folder it finds — `installer.py` copies into a program folder of its
own and refuses a checkout as its target — and `setup.ps1 -Quiet` (what older setup programs run)
still exits with code 3 in one.

**Taking it off.** Run the `QuietUninstallString` of the Settings → Apps entry — for a 2.0
install, its own pythonw running `installer.py uninstall --quiet`, which keeps the player's
settings and voices. An older install's entry runs PowerShell with its execution policy bypassed;
send the player to Settings → Apps for that one rather than starting it.

## Commands

```text
python voice_cli.py register                 # this copy; Kind from .git (dev) or not (installed)
python voice_cli.py register --kind dev      # say it outright
python voice_cli.py unregister               # take it away, if it names this copy
python voice_cli.py status                   # "games find it: dev, port 8765"
```
