# Installing it

Windows only, and one step. Paste this into a new Claude Code session:

```
Clone https://github.com/TraxData313/claude-voice here, then follow docs/tour.md
in it: set it up, turn it on, and give me the spoken tour.
```

It clones the repo, checks you have Python and installs it if you have not, fetches the
speech engine and the model, wires up the `/voice` command, opens the panel, switches it on,
and then tells you out loud how to drive it. If something is genuinely wrong it says so
plainly rather than pretending it worked.

Budget about ten minutes, nearly all of it download: **~660 MB** of engine and **~2.4 GB** of
model. Both resume if the connection drops, so a second run after a dropped VPN picks up
where it stopped rather than starting again.

**No administrator rights are needed at any point** — [why that is true, and what to do on a
locked-down laptop](#no-administrator-rights-needed).

## What your machine needs

| | |
|---|---|
| **Windows** | the engine is a Windows build; there is no macOS or Linux one |
| **An NVIDIA card** | both Studio builds below are CUDA builds. There is no CPU build to fall back to, and nothing here checks before it starts downloading — so on other hardware this fails at model load rather than at the door |
| **~4 GB of video memory** | **an estimate, not a measurement.** The talker is 1.94 GB and the tokenizer 0.27 GB, and both are loaded onto the card with room to work in. Only a 16 GB card has actually been run. If you try a smaller one, please [say how it went](https://github.com/TraxData313/claude-voice/issues) — it is the one number here nobody has established |
| **~3 GB of disk** | 0.81 GB of unpacked engine, 2.2 GB of models |
| **Python** | fetched for you if you have none. Standard library only |

While it runs the engine holds about 350 MB of ordinary memory, and that figure does not
grow with use — it was measured flat across a thousand generations. The first model load
takes 40–60 seconds; afterwards she stays warm and the first words arrive in about a second.

## Or install it as a plugin

Claude Code can carry the `/voice` command itself:

```
/plugin marketplace add TraxData313/claude-voice
/plugin install claude-voice@claude-voice
/claude-voice:setup
```

The first two install the command as `/claude-voice:voice`. The third fetches the engine and
the model — a plugin install is not the place to pull down 3 GB, so that is a separate,
deliberate step which tells you what it is about to do and takes `--whatif` if you would
rather see first.

Afterwards `/plugin update` keeps the command current. Your `config.json` and `logs\` sit
alongside the plugin and are untracked, so an update leaves them alone — tested, not assumed.

You will end up with a plain `/voice` as well as `/claude-voice:voice`, because setup performs
the full install and the plugin carries its own copy of the command. Both drive the same
engine; neither interferes with the other.

## Or do it yourself

One command does everything the paragraph above describes:

```powershell
git clone https://github.com/TraxData313/claude-voice
cd claude-voice
.\setup.ps1
```

It skips whatever you already have, so running it again after a failure is cheap. `-WhatIf`
says what it would do without writing anything.

| | |
|---|---|
| `-ProjectDir $env:USERPROFILE` | speak in **every** project, not just this one |
| `-Build system` | the smaller Studio (268 MB), if you already have the CUDA runtime |
| `-StudioDir` · `-ModelDir` | put the engine or the models somewhere else |
| `-PythonExe` | use a particular interpreter rather than the one it finds |
| `-NoPanel` · `-NoShortcut` | do not open the window · do not touch the Desktop |

Afterwards, in Claude Code: `/voice on`. The first model load takes 40–60 seconds; after
that the engine stays warm and answers start speaking almost at once.

Installing for one project and for every project both write a `/voice` command. Do one or
the other, not both.

## Python

`setup.ps1` looks for a real `python.exe` — on PATH, under `%LOCALAPPDATA%\Programs\Python`,
or a conda install in the usual places — and accepts any of them. The Microsoft Store stub
does not count: it is a launcher for the Store, not an interpreter, and it will not run from
a hook.

If there is none, it installs
[Python](https://www.python.org/downloads/windows/) **per-user**: under `%LOCALAPPDATA%`,
with `PrependPath` writing to your own registry hive, and no elevation prompt. Prefer to do
it yourself? Tick **"Add python.exe to PATH"** in the installer, or use
[Miniconda](https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe).

Either way there is nothing to `pip install` — this uses the standard library only. No
virtual environment, no requirements file.

## What gets fetched

Two things, and `setup.ps1` skips whichever you already have.

**Qwen-TTS Studio**, from its [Releases
page](https://github.com/Danmoreng/qwen-tts-studio/releases). Two builds, and the difference
is only what they carry:

| build | take it if |
|---|---|
| **`windows-cuda-bundled`** (~663 MB, the default) | you are not sure — it brings NVIDIA's CUDA runtime with it |
| `windows-cuda-system` (~268 MB) | you already have the CUDA runtime installed |

No separate Java is needed — Studio ships its own, and this project boots that same JVM in
process. If you already downloaded either package by hand, leave it in `Downloads`: it is
found and unpacked rather than fetched a second time.

**The model**, from
[Serveurperso/Qwen3-TTS-GGUF](https://huggingface.co/Serveurperso/Qwen3-TTS-GGUF) — the same
repo Studio's own Welcome screen pulls from. Two files, both needed: the *talker* turns text
into audio tokens, the *tokenizer* turns those back into sound.

The size of the talker is not a preference:

- **1.7b** produces **2048-dimension** speaker embeddings — what the shipped voices are.
- 0.6b produces 1024, and **will not work** with a voice made by the larger model.

An *unpacked* Studio already sitting in `Downloads` is moved out rather than used where
it stands: Windows Storage Sense empties that folder after 30 days and has taken one
before now. A `.zip` or `.msi` may stay there — losing it costs a download, not the install.

Studio lands in `%LOCALAPPDATA%\Programs\qwen-tts-studio`, the models in
`~\.qwen-tts-studio\models`. You never have to open Studio's own window — it is welcome to
be opened, it is a perfectly good GUI, but nothing here needs it.

## What the installer actually does

`setup.ps1` hands off to `install.ps1` once the pieces are in place, and that part:

- finds `python.exe` and the Studio folder, and writes their full paths into `config.json`
- copies the `/voice` slash command into `.claude\commands\`
- registers the hooks in `.claude\settings.json` — note that **hooks are read once, at
  session start**, so restart Claude Code afterwards
- writes [the note about writing to be heard](../speaking-notes.md) into
  `~\.claude\CLAUDE.md`, between `<!-- claude-voice -->` markers, so every future session
  knows to end long answers with a `## TL;DR` and that only that part is spoken. Anything
  else already in that file is left alone; `-NoNote` skips it.
  **[Why this is the difference between bearable and not →](writing-for-the-ear.md)**
- puts a **claude-voice shortcut on your Desktop** that opens the panel; `-NoShortcut`
  skips it, and `.\make_shortcut.ps1` puts it back later (`-Remove` takes it away,
  `-StartMenu` adds one there too)
- writes every file as UTF-8 **without a BOM**, because a BOM in `settings.json` stops
  Claude Code reading it at all

Nothing is installed system-wide, nothing runs at boot, and nothing is written outside the
repo, your `.claude` folder, your Desktop and your home folder. To undo it, delete the repo
folder, `~\.qwen-tts-studio`, `%LOCALAPPDATA%\Programs\qwen-tts-studio`, and the
`<!-- claude-voice -->` block from `~\.claude\CLAUDE.md`.

## No administrator rights needed

Nothing here elevates, and nothing wants to. Every file it writes is one you already own —
the repo folder, your `.claude` folder, your Desktop, your home folder, your temp folder. It
touches no registry key outside your own hive, adds no service, and installs nothing
machine-wide. So it goes on a locked-down work laptop as easily as your own.

The one part that *looks* like it needs rights is Studio, because it is shipped as an
installer. It does not:

- **The `.zip` build is the same files without the installer**, and that is what `setup.ps1`
  takes by default. Unpack, point at it, done.
- **An `.msi` can be unpacked without being installed.** `msiexec /a package.msi /qn
  TARGETDIR=<folder>` is an *administrative install*, which despite the name needs no
  administrator: it lays the package's files out in a folder and stops. No elevation, no
  registry, no entry in Add/Remove Programs. So if you already have the `.msi` sitting in
  `Downloads` because you tried to install it and Windows refused, `setup.ps1` finds it and
  does exactly this — the download is not wasted.

Two provisos, and neither is about permissions:

- **Keep the folder somewhere you own** — `Documents`, not `Program Files`. The settings
  file lives beside the code, so a folder you cannot write to is a voice that cannot
  remember anything.
- **PowerShell may refuse to run a script it did not sign**, which is a policy, not a
  permission. Either of these gets past it without any rights at all:

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\setup.ps1
  ```

  ```powershell
  Get-Content .\setup.ps1 -Raw | Invoke-Expression
  ```

  The second one works even where the first is blocked by group policy, because the rule is
  about running script *files*.

## When it does not work

Silence has no error message. **[When it goes quiet →](troubleshooting.md)** is the page for
that, and it starts with the two questions worth asking first.
