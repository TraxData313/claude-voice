# Installing it

Windows only, and two things have to be installed by hand first. Both are ordinary
installers; Claude does everything after them.

## 1. Python

[python.org/downloads/windows](https://www.python.org/downloads/windows/) — tick
**"Add python.exe to PATH"** in the installer.

Nothing else is needed. This uses the standard library only, so there is nothing to
`pip install`, no virtual environment, no requirements file.

Already have [Miniconda](https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe)
or Anaconda? That works too. `install.ps1` records the full path to whichever `python.exe`
it finds, so a conda install that is not on PATH is fine.

## 2. Qwen-TTS Studio, and one model

From the [Releases page](https://github.com/Danmoreng/qwen-tts-studio/releases). Two builds,
and the difference is only what they carry:

| build | take it if |
|---|---|
| **`windows-cuda-bundled`** (~630 MB) | you are not sure — it brings NVIDIA's CUDA runtime with it |
| `windows-cuda-system` (~255 MB) | you already have the CUDA runtime installed |

Either as `.msi` (installs) or `.zip` (portable). No separate Java is needed — Studio ships
its own, and this project boots that same JVM in process.

**Then launch Studio once** and let the Welcome screen download **`qwen-talker-1.7b-base`**.
That is the one step nobody can do for you, and the size matters:

- **1.7b** produces **2048-dimension** speaker embeddings — what the shipped voices are.
- 0.6b produces 1024, and **will not work** with a voice made by the larger model.

## 3. Hand it to Claude

Paste this into a new Claude Code session:

```
Clone https://github.com/TraxData313/claude-voice here, then follow docs/tour.md
in it: set it up, turn it on, and give me the spoken tour.
```

It clones the repo, checks what you have, installs it, switches it on, and then *tells you
out loud* how to change voice, how to shush it mid-sentence, and how to turn it off. If
either prerequisite is missing it says so plainly rather than pretending it worked.

## Or do it yourself

```powershell
git clone https://github.com/TraxData313/claude-voice
cd claude-voice
.\install.ps1                 # finds Python and Studio, writes config, adds /voice
```

Then, in Claude Code: `/voice on`. The first model load takes 40–60 seconds; after that the
engine stays warm and answers start speaking almost at once.

### Speaking in every project

By default `/voice` is installed for the project you are in. For every project instead:

```powershell
.\install.ps1 -ProjectDir $env:USERPROFILE
```

Do one or the other, not both.

## What the installer actually does

- finds `python.exe` and the Studio folder, and writes their full paths into `config.json`
- copies the `/voice` slash command into `.claude\commands\`
- registers the hooks in `.claude\settings.json` — note that **hooks are read once, at
  session start**, so restart Claude Code afterwards
- puts a **claude-voice shortcut on your Desktop** that opens the panel; `-NoShortcut`
  skips it, and `.\make_shortcut.ps1` puts it back later (`-Remove` takes it away,
  `-StartMenu` adds one there too)
- writes every file as UTF-8 **without a BOM**, because a BOM in `settings.json` stops
  Claude Code reading it at all

Nothing is installed system-wide, nothing is written outside the repo and your `.claude`
folder, and nothing runs at boot. To undo it, delete the folder.

## No administrator rights needed

Nothing here elevates, and nothing wants to. Every file it writes is one you already own —
the repo folder, your `.claude` folder, your Desktop, your temp folder. It reads `Program
Files` only to look for Studio there; it never writes to it. It touches no registry key, adds
no service, and installs nothing machine-wide. So it goes on a locked-down work laptop as
easily as your own, with two provisos:

- **Keep the folder somewhere you own** — `Documents`, not `Program Files`. The settings file
  lives beside the code, so a folder you cannot write to is a voice that cannot remember
  anything.
- **PowerShell may refuse to run a script it did not sign**, which is a policy, not a
  permission. Either of these gets past it without any rights at all:

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  ```

  ```powershell
  Get-Content .\install.ps1 -Raw | Invoke-Expression
  ```

  The second one works even where the first is blocked by group policy, because the rule is
  about running script *files*.

Qwen-TTS Studio is the one part that is not ours: take its **`.zip`** rather than the `.msi`
if you cannot install software, unpack it into your own folder, and point the installer at it
with `-StudioDir`.

## When it does not work

Silence has no error message. **[When it goes quiet →](troubleshooting.md)** is the page for
that, and it starts with the two questions worth asking first.
