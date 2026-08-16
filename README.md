<p align="center">
  <img src="docs/banner.svg" alt="claude-voice — Claude Code, out loud. Locally." width="820">
</p>

<p align="center">
  <em>Your terminal grew a voice. It runs on your own machine and tells nobody about it.</em>
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

## Try it in five minutes

You need **Windows**, **Python 3.9+** (standard library only — nothing to `pip install`),
and **[Qwen-TTS Studio](https://github.com/Danmoreng/qwen-tts-studio)** with a talker model.

```powershell
git clone https://github.com/TraxData313/claude-voice
cd claude-voice
.\install.ps1                 # finds Python and Studio, writes config, adds /voice
```

Then, in Claude Code:

```
/voice on
```

That is it. First load takes 40–60 seconds; after that the engine stays warm and answers
start speaking almost at once.

> **Getting Studio ready.** Take a build from its
> [Releases page](https://github.com/Danmoreng/qwen-tts-studio/releases) — the
> `windows-cuda-bundled` one unless you already have NVIDIA's CUDA runtime. Launch it once
> and let the Welcome screen fetch **`qwen-talker-1.7b-base`**. The size is not a free
> choice: it produces 2048-dimension embeddings, and the 0.6b model gives 1024 and will not
> work with voices made by the larger one. No separate Java needed — Studio ships its own,
> and that is the one this drives.
>
> To speak in **every** project rather than one, install into your user settings:
> `.\install.ps1 -ProjectDir $env:USERPROFILE`. Do one or the other, not both.

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
| `help` | all seventeen commands, grouped |

**[Full command list →](docs/commands.md)** — generated from the tool itself, so it cannot
go stale.

## The voices

Two ship with it: **Abby**, cute and slightly nerdy, and **Max**, brave and driving. Both
belong to the author. Adding your own is one command and a clean 20–40 second clip of one
person talking:

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
