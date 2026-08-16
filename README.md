<p align="center">
  <img src="docs/banner.svg" alt="claude-voice — Claude Code, out loud. Locally." width="820">
</p>

<p align="center">
  <em>Your terminal grew a voice. It runs on your own machine and tells nobody about it.</em>
</p>

<p align="center">
  <a href="https://traxdata313.github.io/claude-voice/"><strong>▶&nbsp; Demo — hear the voices</strong></a>
  <br>
  <sub>Abby and Max, 5 seconds each</sub>
</p>

---

Claude Code reads its answers aloud, in a voice you choose, using a local Qwen-TTS model.
No cloud, no API key, no account, no audio leaving the machine. Unplug the network and it
carries on talking.

It speaks **two** things, and the difference is the whole design:

- **The short lines said while it works** — "now I'm checking whether that exists" — go out
  as they happen, so you can follow along without watching the screen.
- **The summary at the end.** A long answer is read as its `## TL;DR` and not in full,
  because an answer stuffed with paths and flags is written for eyes that skip around. The
  detail stays on screen where you can look at it properly.

Short answers are read whole. Nothing is repeated, nothing talks over anything else.

<p align="center">
  <img src="docs/panel.png" width="390"
       alt="The claude-voice panel: Abby's portrait beside the line being spoken, stop, play and skip, a queue, a clickable history, and a tick per session">
</p>

<p align="center">
  <em><strong>Abby</strong>, mid-sentence. The panel shows what is being said and which
  conversation it came from, what is waiting behind it, and everything just said —
  <br>click any line to hear it again. <a href="#the-panel">More about it below ↓</a></em>
</p>

## Try it in five minutes

Windows only. Two things have to be installed by hand first — both are ordinary installers,
and Claude does the rest.

**1. Python** — [python.org/downloads/windows](https://www.python.org/downloads/windows/).
Tick **"Add python.exe to PATH"** in the installer. Nothing else is needed: this uses the
standard library only, so there is nothing to `pip install`.
*(Already have [Miniconda](https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe)
or Anaconda? That works too — the installer records the full path to your `python.exe`,
so a conda install that is not on PATH is fine.)*

**2. Qwen-TTS Studio** — from its
[Releases page](https://github.com/Danmoreng/qwen-tts-studio/releases). Take the
`windows-cuda-bundled` build unless you already have NVIDIA's CUDA runtime; either the MSI
or the portable ZIP. **Then launch it once** and let the Welcome screen download
**`qwen-talker-1.7b-base`**. That step is the one thing nobody can do for you, and the size
matters: it produces 2048-dimension embeddings, while the 0.6b model gives 1024 and will
not work with voices made by the larger one. No separate Java needed — Studio ships its own.

**3. Then paste this into a new Claude Code session.** That is the whole of the rest.

```
Clone https://github.com/TraxData313/claude-voice here, then follow docs/tour.md
in it: set it up, turn it on, and give me the spoken tour.
```

It clones the repo, checks what you have, installs it, switches it on — and then *tells you
out loud* how to change voice, how to shush it mid-sentence, how to turn it off, and where
the rest of the commands live. If either of the two above is missing, it will say so plainly
rather than pretending it worked.

<details>
<summary>Or do it yourself</summary>

```powershell
git clone https://github.com/TraxData313/claude-voice
cd claude-voice
.\install.ps1                 # finds Python and Studio, writes config, adds /voice
```

Then, in Claude Code:

```
/voice on
```

First load takes 40–60 seconds; after that the engine stays warm and answers start
speaking almost at once.
</details>

> **Speaking in every project** rather than just this one: install into your user settings
> with `.\install.ps1 -ProjectDir $env:USERPROFILE`. Do one or the other, not both.

## The controls

`/voice <thing>` in Claude Code, or `python voice_cli.py <thing>` in a terminal — the same
tool either way. Mind the space: `/voice on`, never `/voice_on`.

| | |
|---|---|
| `on` / `off` | the switch. `off` leaves the engine warm, so `on` is instant |
| `set abby` | change voice. Any unambiguous substring: `set ab` works |
| `repeat` | say the last answer again. `repeat-all` for the whole thing, not the summary |
| `stop` | cut off what is playing. The voice stays on (`break` works too) |
| `list` | every voice available |
| `panel` | a small window: what is playing, what is queued, and the switches |
| `help` | every command, grouped |

**[Full command list →](docs/commands.md)** — generated from the tool itself, so it cannot
go stale.

### The panel

`/voice panel` opens the window pictured at the top of this page. It floats over
everything — a tick box turns that off — and shows what the voice is actually doing:

- the line being spoken, headed by the project and conversation it came from, with the
  speaker's portrait and name beside it. **Click the portrait to swap voice**
- **turn off / turn on** — the master switch, the same one `/voice off` throws
- **stop** — silence, and drop everything waiting. **skip** — abandon this line only and
  go straight on to the next one
- what is queued behind it
- everything said recently — **click a line to hear it again**, played from the audio it
  was heard as, so there is nothing to re-synthesise and no wait
- a tick per session, named *project · conversation*: untick one and that conversation
  stops being read aloud
- the voice as a dropdown, a portrait size (pick one or type a number), and tick boxes
  for **dark** and **on top**

It holds nothing of its own. It asks the engine what is happening twice a second and turns
every click into a request, so closing it changes nothing, and opening it before the engine
is running gives you a button to start one.

## The voices

Two ship with it, both the author's own — original characters, borrowed from nobody:

| | | |
|---|---|---|
| <img src="docs/art/abby.jpg" width="300" alt="Abby"> | **Abby** | Cute, slightly nerdy, young and warm. Calm, never in a hurry — the voice for careful work, tricky debugging, and being talked through something gently. |
| <img src="docs/art/max.jpg" width="300" alt="Max"> | **Max** | Brave and driving, a trainer's energy. Punchy lines, counts off what is done, pushes for one more — the voice for grinding through a long list. |

▶ **[Hear them both](https://traxdata313.github.io/claude-voice/)** — GitHub will not play
audio inside a README, so the two samples live on a page of their own.

> *With thanks to **Genndy Tartakovsky**, whose* Samurai Jack *is why they look the way they
> do — the flat shapes, the spare linework, those painted backgrounds. The characters are
> ours; the debt is to the style, and to the spirit of the thing.*

Switch any time with `/voice set abby` or `/voice set max`. The manner changes tone, never
substance: neither voice will cheer a result that has not been checked.

Adding your own is one command and a clean 20–40 second clip of one person talking:

```powershell
python voice_cli.py clone C:\path\to\sample.wav --name "Ada"
python voice_cli.py set ada
```

A speaker embedding **is** a clone of a real person's voice. Whose you may publish, and
whose you may only keep, is worth two minutes of your time before you add one:
**[the rules →](docs/voices.md)**

Voices you may keep but not share never need to enter the repo at all — point
`extraVoicesDirs` at any folder and they show up marked *local only*.

## Where the detail lives

| | |
|---|---|
| **[Setup and tour →](docs/tour.md)** | what Claude follows when you ask it to set this up for you |
| **[The panel →](docs/panel-plan.md)** | the floating control window: how it was built, and what changed on the way |
| **[How it works →](docs/how-it-works.md)** | the engine, the watcher, and the JNI bridge into an app with no CLI |
| **[Writing for the ear →](docs/writing-for-the-ear.md)** | the TL;DR contract, and why captions sound wrong aloud |
| **[When it goes quiet →](docs/troubleshooting.md)** | silence has no error message. Start here |
| **[Commands →](docs/commands.md)** | all of them, grouped |
| **[Voices →](docs/voices.md)** | what may go in that folder, and what may not |

## Three things that cost real time to find

Kept here because they will not be obvious to the next person either.

**Hooks are not dependable, so this does not need them.** A hook runs only if the client
chooses to run it, and nothing from outside can tell whether it did — which looks exactly
like the voice being broken. Claude Code writes every turn to a transcript on disk
regardless, so that is what gets followed. No hooks, no restart, every session at once.

**A JNIEnv pointer belongs to the thread that made the JVM.** The engine lives on one
dedicated thread and everything else only enqueues. Calling `generate()` from a request
thread is undefined behaviour, not a race you get away with.

**`SO_REUSEADDR` means something else on Windows.** `HTTPServer` sets it, and Windows then
lets a *second* process bind a port already in use rather than failing — two engines, two
model loads, every sentence spoken twice.

<!-- ─────────────────────────────────────────────────────────────────────────
     AUTHOR'S SECTION — copied verbatim from github.com/TraxData313/ImmersiveAI
     Hands off. Only TraxData313 changes the wording below.
     ───────────────────────────────────────────────────────────────────────── -->

## Freely given

- **Public domain** — no license, no strings, no permission to ask ([The Unlicense](LICENSE)).
  Use it, share it, change it, sell it. *"Freely you have received; freely give."* (The bundled
  Harmony library keeps its own MIT notice in `lib\`.)
- **Want to help?** Give feedback and report bugs.
- **No donations** — this is a hobby, done for fun and out of good will; I want to keep money
  out of it. *"For the love of money is the root of all evil."*
- If you still insist on thanking me somehow — visit [my GitHub acc](https://github.com/TraxData313) and read my top pinned

<!-- ── end author's section ── -->

Voices are not code: a speaker embedding is a clone of someone's actual voice, so what may
be added to `voices/` has its own rules — see **[docs/voices.md](docs/voices.md)**.
