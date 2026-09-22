# Two engines

There are two ways to make sound here, and they are not a fast one and a good
one. They are a heavy one that reads any alphabet, and a light one that reads
six languages and starts in a fifth of the time.

```powershell
python voice_cli.py engine            # which one is speaking
python voice_cli.py engine pocket     # switch
```

Or the top dropdown in the panel, above the voice.

| | **Qwen** | **Pocket TTS** |
|---|---|---|
| install | Studio, a GPU, and `qwen_engine.py` | `pip install pocket-tts` |
| runs on | GPU | CPU, two cores |
| first audio | 812 ms | **181 ms** |
| throughput | 3.7–4.0× realtime | 4.0–4.5× realtime |
| voices | the ones in `voices\`, cloned here | 21 built in, English |
| Cyrillic | yes — Russian well, Bulgarian accented | **no** |
| licence | Studio's | MIT code, CC-BY-4.0 weights |

The timings are from this machine, taken the same way as the ones in
[engine-notes.md](engine-notes.md).

## Which to use

**Qwen, if you have it working.** It reads Cyrillic, the voices are yours, and
Abby lives there. Nothing about Pocket TTS replaces that.

**Pocket TTS, if you do not.** It is the answer to "I do not have Studio, I do
not have a GPU, and I would like a voice anyway" — which until now this project
had no answer to at all. That it is also quicker to the first word is a bonus
nobody planned.

## What changes when you switch

**The voices change completely.** The two engines share none. Qwen's are folders
under `voices\` holding an embedding this repo made; Pocket's are names the model
fetches a precomputed speaker state for. So switching engine picks a voice for
you — whoever you last used there, or `alba` the first time — and switching back
returns you to whoever you had. Neither choice is lost.

**The portraits are stood in for.** There are two pictures in this repo and
twenty-one voices on the other engine, so a Pocket voice borrows the shipped face
of its own sex: Abby for a female voice, Max for a male one. The dropdown
underneath says who is really speaking. A voice you cloned yourself keeps its
coloured initial, because that one you *could* give a picture to.

**Only one model is ever loaded.** The engine in use is closed before the other
is built, never after, so the two are never resident together. The log writes
down what each swap cost, because that is the difference between it being true
and being believed:

```
switching engine: pocket -> qwen
  unloaded pocket: 830 MB -> 770 MB
engine ready (qwen), 1187 MB resident
```

**What a swap does not do is give the memory back.** Measured across two
switches the process went 830 → 1187 → 1555 → 1431 MB, and never returned to
where it started. Two reasons, both outside this code: Python and torch hand
freed pages back to their own allocators rather than to Windows, and Qwen's JVM
and CUDA context are created once per process and live as long as it does — the
model weights go, the runtime around them stays. So a session that has used Qwen
keeps a floor under it. If you want the memory actually returned, restart the
engine (`voice kill`, then `voice on`) rather than expecting a swap to do it.

**It loads on the next thing said, not when you pick it.** Loading the other
model costs seconds, and spending them when a message arrives puts the wait where
somebody is already waiting. Until then `voice_cli.py status` says which is still
loaded.

## Cyrillic, and why there is a spoken warning

Pocket TTS does not fail on Cyrillic. It runs away. Measured here, *"Сега ще
проверя как звучи това на български."* — about three seconds of speech — came
back as **11.4 seconds of audio**. Not an accent, not gibberish: eleven seconds
of babbling, with whatever English was around it buried.

So Cyrillic is taken out before synthesis, on that engine only, and you are told:

> There are 162 Cyrillic characters in this that I can't speak out, so I'll skip
> them, just so you know.

and if there was nothing else in the line:

> That line is all Cyrillic, and Pocket TTS can't read it at all. Switch back to
> the other engine and I'll say it properly.

Whole words go, not single letters — cutting the Cyrillic out of a mixed word
leaves a stump the model reads as some other word, which is worse to hear than
the word being gone. The counts and the line itself are `SKIPPED_SOME` and
friends in `voice_lib.py`, and they are three strings to edit.

None of this touches Qwen, which reads those languages. See
[languages.md](languages.md) for what it makes of them.

## Voices

Twenty-one English voices ship with the model — ten female, eleven male. They
are named, not numbered, and the dropdown says how each one reads:

> Alba (m, reading) · Eve (f, conversation) · Stuart Bell (m, reading)

**None of that is in the package.** `pocket_tts` carries a dict of names mapped
to wav files and nothing else — no gender field, no style field, no metadata
anywhere. So the table in `pocket_engine.py` was transcribed from Kyutai's own
listing, and the obvious shortcut is a trap: reading the sex off the name gets
`alba` backwards. He is a man.

**Style is the more useful column, and the one you cannot guess at all.** The
LibriVox readers narrate; most of the VCTK speakers talk; three of those VCTK
voices narrate anyway and nothing in the name says which. It earns its place in
the dropdown because this tool reads summaries aloud, and a narrator and a
talker are genuinely different to listen to for that.

Three voices — `cosette`, `marius`, `javert` — carry no style, because they
were not in the listing. They print as `Cosette (f)` rather than with a guess
in the brackets.

## The five foreign voices, and the model that comes with them

> Estelle (French, f) · Giovanni (Italian, m) · Lola (Spanish, f) · Rafael
> (Brazilian Portuguese, m) · Juergen (German, m)

They sit at the end of the same list and are picked the same way. **The voice
decides which model is loaded**, not a setting — pick Estelle and the French
model comes down on its own, because a speaker state belongs to exactly one of
the six and handing hers to the English model gives nonsense rather than an
error.

That costs a few seconds the first time a language is used in a session, and
one download the first time ever. Two things worth knowing:

- **They read English in their own accent**, which is the charm rather than a
  fault — the same shape as Abby reading Russian in [languages.md](languages.md).
- **French is a 24-layer model**, because that is the only French there is.
  Bigger, better, slower. The others take the small model where one exists, and
  which is which is read off the installed package rather than hardcoded, so a
  later release that adds a small French one is picked up with no edit here.

A voice carried across with `make_pocket_voice.py` sorts above all of them, and
takes its style from a `"Style"` key in its own `voice.json` — free text, so
Abby's reads `the original`. The `"PocketLanguage"` key beside it records which
model she was baked against, and the script writes it.

`pocketLanguage` in the config no longer hides anything. It only says which
language sorts to the top of the list, and which model to bake a new clone
against.

## Carrying a voice across

```powershell
python make_pocket_voice.py abby
```

**Cloning needs a Hugging Face account; the built-in voices do not.** They are
different weights, and Kyutai gate the ones that can clone. Two steps, once:
accept the terms at [huggingface.co/kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts),
then `hf auth login`. Until then the twenty-one voices work perfectly and
cloning stops with a message saying exactly this — the reference render is kept,
so the retry afterwards costs seconds.

**The original recordings are not needed.** That is the part worth knowing
before you start looking for them. The Qwen engine is itself a source of clean
audio for every voice it already has, so the script renders a reference passage
in that voice, clones Pocket TTS from the render, and bakes the result to
`voices\<sex>\<id>\pocket.safetensors` beside the embedding that made it. The
catalogue picks it up on its own — a cloned voice sorts to the top of the list,
ahead of the twenty-one built in.

A render is a better prompt than a recording, not a worse one. The model
reproduces the *quality* of its sample as faithfully as the voice, and a render
has no room tone, no breath and no microphone in it.

It is a clone of a clone, so something shifts. The script writes
`pocket-reference.wav` and `pocket-after.wav` in the voice's own folder — the
same words, before and after — because the only question left is one an ear
answers.

`--from some-recording.wav` clones from a file instead, which is the road for a
voice the other engine does not have. `--text` changes what gets read: the
default is deliberately broad rather than long — every vowel, the awkward
clusters, numbers, a question — because a cloning prompt is the only evidence
the model gets. It is also in character, since the prompt carries delivery as
well as timbre and a bored sample makes a bored clone.

Read [voices.md](voices.md) before pointing it at anybody who has not agreed
to it. The rules there apply to a state baked from a voice exactly as they apply
to the recording.

## Using one of these voices in another project

Nothing here is specific to this tool, and that is worth saying plainly, because
the interesting part of Pocket TTS for anybody else is that it needs no GPU and
no server. Three lines:

```python
from pocket_tts import TTSModel
model = TTSModel.load_model(language="english")
state = model.get_state_for_audio_prompt("abby/pocket.safetensors")
audio = model.generate_audio(state, "Whatever you want said.")     # 24 kHz mono float
```

`generate_audio_stream` is the same call handing pieces over as they are made —
about 80 ms each, first one inside 200 ms. That is the one to use if anything is
waiting to hear it.

Four things this project learned the expensive way, which carry over:

- **Bake the voice once.** `get_state_for_audio_prompt` on a wav costs a couple
  of seconds; on a `.safetensors` it is a load. `export_model_state` writes one.
- **The language is a property of the voice, not of the app.** Six models, and a
  state belongs to exactly one. Mixing them gives nonsense rather than an error,
  which is the hardest kind of bug to notice.
- **Count what comes back.** The model answers a prompt it cannot read by
  babbling, not by failing — 11.4 seconds of audio for a three-second sentence,
  measured. Compare the audio's length against what the text should take and
  throw away anything wildly over. `voice_lib.expected_seconds` and
  `audio_verdict` are that check, and they have no dependencies worth speaking
  of.
- **Cut playback on silence, not on the clock.** If you are streaming into a
  player that takes a file at a time, put the seams inside pauses;
  `voice_lib.quiet_span` does it in about forty lines.

**Cloning needs the gated weights; using an already baked one does not.**
`get_state_for_audio_prompt` checks for a safetensors source at the very top and
imports it directly, before it ever asks whether this build can clone — so a
voice baked once travels to machines that could never have made it. That is why
`pocket.safetensors` is committed: it is the difference between Abby being
everybody's and being this laptop's.

For a game, the thing to weigh is that this runs on two CPU cores while the GPU
is busy drawing, which is the opposite of the usual problem.

## Adding a third engine

The contract is small enough to write out. An engine module needs:

| | |
|---|---|
| `SAMPLE_RATE` | module level, and 24000 unless you resample here |
| `Engine(...)` | plus `load_models()` |
| `synthesize_streaming(text, on_piece, **kwargs)` | `on_piece(samples, None)`; returning False stops it |
| `synthesize(text, **kwargs)` | mono float samples |
| `close()` | |

Then `build_engine` in `speak_server.py` gains a branch, `voice_lib.ENGINES`
gains a name, and `voice_lib.resolve` decides what goes in `kwargs` for it —
those kwargs are handed through without being read, so whatever a new engine
needs to identify a voice is its own business.

The catalogue is the only other question: voices on disk, a table in the module,
or both, as `catalog()` does for Pocket.

## Why the pieces are so much smaller

Pocket hands back about 80 ms of audio at a time, against Qwen's second. That
suits the player rather than straining it: playback is cut on silence, and more
pieces means more places to cut, so the seams land deeper inside the pauses. See
[how-it-works.md](how-it-works.md) for what the buffering does with them.
