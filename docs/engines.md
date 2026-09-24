# Three engines

There are three ways to make sound here, and they are not a fast one and a good
one. They are a heavy one that reads any alphabet, a light one that reads six
languages and starts in a fifth of the time, and an optional one that laughs.

```powershell
python voice_cli.py engine            # which one is speaking
python voice_cli.py engine pocket     # switch
python voice_cli.py install breeze    # check this machine for the third, and offer it
```

Or the top dropdown in the panel, above the voice.

| | **Qwen** | **Pocket TTS** | **Breeze TTS 2** |
|---|---|---|---|
| install | Studio, a GPU, and `qwen_engine.py` | `pip install pocket-tts`, or `setup.ps1 -Engine pocket` from scratch | a separate download of about 11 GB, [only when asked](#breeze-tts-2-the-one-that-laughs) |
| runs on | GPU, 3.3 GB | CPU, two cores | GPU, 13 GB free to start and 9 GB after — a 16 GB card |
| first audio | 812 ms | **181 ms** | **180–370 ms** |
| throughput | 3.7–4.0× realtime | 4.0–4.5× realtime | 1.6–2.1× realtime |
| voices | the ones in `voices\`, cloned here | 21 built in, English | the ones in `voices\` with a clip and its words — Abby |
| Cyrillic | yes — Russian well, Bulgarian accented | **no** | **no** — English and Chinese |
| laughs, sighs, whispers | no | no | **yes** |
| licence | Studio's | MIT code, CC-BY-4.0 weights | Apache code, **non-commercial** weights |

The timings are from this machine, taken the same way as the ones in
[engine-notes.md](engine-notes.md).

## Which to use

**Qwen, if you have it working.** It reads Cyrillic, the voices are yours, and
Abby lives there. Nothing about Pocket TTS replaces that.

**Pocket TTS, if you do not.** It is the answer to "I do not have Studio, I do
not have a GPU, and I would like a voice anyway" — which until now this project
had no answer to at all. That it is also quicker to the first word is a bonus
nobody planned.

**Breeze TTS 2, if you want her to act.** It laughs where `(laugh)` is written,
sighs, whispers when told to, and a sad line is sad — none of which the other
two do. It costs a big download, a graphics card with room to spare, and
speaking at half Qwen's pace, which is still twice as fast as she talks.

## What changes when you switch

**The voices change completely.** Qwen and Pocket share none. Qwen's are folders
under `voices\` holding an embedding this repo made; Pocket's are names the model
fetches a precomputed speaker state for. Breeze's are the same folders as Qwen's,
but only those holding a clip it can learn from. So switching engine picks a voice
for you — whoever you last used there, or `alba` or Abby the first time — and
switching back returns you to whoever you had. No choice is lost.

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

**Breeze is the exception, and gives back all of it.** It runs as a process of
its own, and leaving it ends that process. Measured on 2026-09-24, the card went
from 11.2 GB in use to 1.7 GB the moment Breeze was swapped for Qwen — what the
desktop was holding before either was loaded. Going the other way, Qwen's
runtime left 0.24 GB behind on the card while Breeze started.

**It loads on the next thing said, not when you pick it.** Loading the other
model costs seconds, and spending them when a message arrives puts the wait where
somebody is already waiting. Until then `voice_cli.py status` says which is still
loaded. Breeze costs the most: about half a minute, and two the very first time,
while it compiles.

## Telling Qwen how to say it

Qwen takes a second string beside the words: not more to read, but how to read
it. `POST /speak` carries it as `instruction`, and `/state` answers
`instruction: true` when the configured engine would use one, so a caller can
ask before it offers the feature to anybody.

```
{"text": "I did not expect you back so soon.", "instruction": "sound daring and brave"}
```

It had been sitting in the parameter block the whole time — `0x28`, mapped from
Studio's own ABI and passed as null since the day it was mapped. It is read. The
engine says so itself, in the line it prints while building the prefill:

```
build_prefill_graph: n_tokens=27, n_instruct=0,  has_speaker=yes   <- no instruction
build_prefill_graph: n_tokens=27, n_instruct=15, has_speaker=yes   <- one sent
```

And it does something. The same sentence, same voice, four runs each:

| | mean audio |
|---|---|
| nothing | 4.24 s |
| *speak very slowly and sadly* | 5.74 s |
| *speak quickly and excitedly* | 3.88 s |

`probe_instruction.py` is that measurement, if it needs making again. It loads
the talker and spends a minute of GPU, so run it on purpose.

**A whole instruction, not one adjective**, and this is the part people get wrong
first. The same line, the same voice, three runs each:

| instruction | mean audio |
|---|---|
| none | 1.41 s |
| `sad` | 1.68 s — inside the sampler's own spread |
| `Speak slowly and sadly, quiet and downcast, with long pauses.` | 1.95 s |

One word is not enough to steer it. A directive with a verb and two or three things
about the delivery is. The instruction is tokenized into the prefill and competes
there with a speaker embedding that is pinned hard, so it needs some weight to
carry — `n_instruct=6` did nothing audible, `n_instruct=19` did.

**Pocket has no such field**, and it is never sent one — a keyword an engine did
not declare would raise on the sentence rather than be ignored, and that failure
arrives as silence. The decision is made once, in `Speaker._kwargs`, against the
engine that is actually loaded. Breeze takes the same field and performs it far
more strongly; see below.

## Breeze TTS 2: the one that laughs

BreezeBlue's 3B model, open since 2026-08-25, and the top open model on the
Artificial Analysis cloned-voice arena when it was chosen. It clones a voice from
a clip and its exact words, as the other two clone theirs, and then does what
neither of them can: it performs a sound written into the text — `(laugh)`,
`(sigh)`, `(cough)`, `(clears throat)` — and follows a mood given beside the
words. Toni heard Abby laugh, whisper and be very sad on it on 2026-09-24; the
takes, the timings and his verdict are in [laughing.md](laughing.md), and how a
program asks for all of it is [api.md](api.md). A Claude session is told it may
write a laugh while Breeze is the engine speaking, and told again when it no
longer is: [writing for the ear](writing-for-the-ear.md#a-mood-and-a-laugh-when-the-engine-has-them).

**Never downloaded by surprise.** It is about eleven gigabytes and it needs a
particular kind of graphics card, so an update brings the code for it and
nothing else. Picking Breeze in the panel before it is installed opens a window
instead of switching: it asks the card what it has, sets that beside what Breeze
needs, says where it would go and what it costs, and only its button downloads
anything. `voice_cli.py install breeze` does the same in a terminal, and
`--check` stops after the check.

| it needs | why |
|---|---|
| an NVIDIA card, 16 GB for full speed | the fast stages peak at 13.1 GB while starting, on top of the desktop's share. 12 GB runs it without them, at about half realtime |
| RTX 30 series or later | it runs in bf16 throughout |
| a driver that runs CUDA 12.8 | the PyTorch build it is tested with |
| about 20 GB free on one drive | 13 GB when done, and the downloads unpacking on the way |

**It lives in a folder of its own**, on whichever drive has the room: a Python
environment, BreezeBlue's code at a pinned commit, and the weights at a pinned
revision. Everything the install downloads, caches included, stays inside that
folder, and deleting it undoes the whole of it — the dropdown then offers the
installer again. `breezePython`, `breezeDir` and `breezeModel` in the config
point at the three pieces, which is also how an existing copy is adopted.

**And it runs as a process of its own.** It needs CUDA torch 2.9.1, transformers
4.57.3 and qwen-tts, which this tool's Python has no business growing — the
answer the Kyutai section below arrived at, for the same reason. So Breeze's
own streaming server runs out of Breeze's own environment, and
`breeze_engine.py` is an HTTP client for it that needs nothing but the standard
library. A Windows job object ties the server's life to the engine's, so
`voice kill` or a crash cannot leave nine gigabytes held by nobody. What it
says goes to `logs\breeze-server.log`.

**Its voices are clips.** Breeze keeps no state for a voice; it learns her from
the clip on every line. So a voice is a folder holding `breeze-reference.wav` and
`breeze-reference.txt`, the exact words said in it — and for Breeze the clip is
the artefact, committed where Pocket's working render is not. Abby's is the
24-second Qwen render `make_pocket_voice.py` made for Pocket, which now keeps
its words beside every render, so a voice carried across to Pocket also speaks
on Breeze on the machine that carried it. Max has no clip yet.

**What it costs**, on the laptop it was measured on:

- the first line after switching waits for it to start — about half a minute,
  two minutes the very first time, while it compiles;
- a line sent straight after a skip starts about two seconds late: Breeze's
  server takes one request at a time and lets go of the one hung up on only
  when it has been cleared away, so the engine waits out its "busy";
- about half Qwen's throughput, which is still twice as fast as she speaks;
- English and Chinese only, so Cyrillic is dropped with a spoken note, as on
  Pocket;
- non-commercial weights: fine at home, and a question to settle before her
  voice goes into anything shipped.

The three fast stages are what make it quicker than speech, and a card that
cannot hold them gets it without: `breezeFast: false`, set by the installer
when the fast start fails. Every number here is in [laughing.md](laughing.md).

## Cyrillic, and why there is a spoken warning

Pocket TTS does not fail on Cyrillic. It runs away. Measured here, *"Сега ще
проверя как звучи това на български."* — about three seconds of speech — came
back as **11.4 seconds of audio**. Not an accent, not gibberish: eleven seconds
of babbling, with whatever English was around it buried.

So Cyrillic is taken out before synthesis, on that engine only, and you are told:

> There are 162 Cyrillic characters in this that I can't speak out, so I'll skip
> them, just so you know.

and if there was nothing else in the line:

> That line is all Cyrillic, and Pocket TTS can't read it at all. Switch to Qwen
> and I'll say it properly.

Breeze gets the same guard and the same two lines with its own name in them. It
reads English and Chinese by its own model card. What it does with Cyrillic left
in was not measured; the guard is there on the strength of the card.

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

## Adding another engine

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
needs to identify a voice is its own business. `voice_lib.ENGINE_CAN` says
whether it takes a mood and which sounds it makes, and the server, the panel
and the API all read that one table; `voice_lib.engine_ready` says whether it
is installed.

The catalogue is the only other question: voices on disk, a table in the module,
or both, as `catalog()` does for Pocket.

An engine that needs a Python of its own is `breeze_engine.py`'s shape: start
the model's own server as a subprocess in a job object, talk to it over
localhost, and let `close()` end the process. It is the one kind of engine whose
memory really does come back on a swap.

## Kyutai TTS 1.6B, and why it is not one of them

It was looked at properly on 2026-09-22 and turned down, so here is the finding
rather than the search. It is the big sibling of Pocket TTS from the same lab —
1.6B parameters, GPU, and genuinely better — and the reason to want it is
emotion. Those facts are all true. It still does not fit here, for one reason
that is not going to change by waiting.

**You cannot put a voice of your own into it.** Kyutai never released the model
that turns audio into a voice embedding, on purpose: the model card says they
"prefered to restrict the voice cloning ability to the use of pre-computed voice
embeddings". You get their repository of about 1,450 voices and no way to add a
1,451st. This is not the Pocket TTS situation, where the gate opens if you accept
the terms — the file that does the work, `mimi_voice.safetensors`, is referenced
by their own script and answers 404 to everybody.
[moshi#404](https://github.com/kyutai-labs/moshi/issues/404) is somebody trying
all three routes, including feeding audio through the ordinary Mimi encoder,
which produces embeddings with the wrong statistics and garbled speech. It was
still open and unanswered when this was written.

So Abby cannot live there, and a voice that cannot carry across is most of what
this project wants an engine for.

**Emotion is a voice, not a parameter — here.** Worth writing down because the
obvious guess is wrong and it is the thing people ask. Note that this is a fact
about Kyutai and not about TTS: Qwen next door does take a steering string, and
[Telling Qwen how to say it](#telling-qwen-how-to-say-it) is the measurement.
In Kyutai there is no steering string, no style argument and no `*laughs*`
markup — the reference script takes a repo, a voice, and a device. The mood is baked into the embedding, which you can read
straight off the default:

```
--voice expresso/ex03-ex01_happy_001_channel1_334s.wav
```

The `ears/` collection carries that furthest: one speaker in 23 emotions —
adoration, amazement, amusement, anger, confusion, contentment, cuteness, desire,
disappointment, disgust, distress, embarrassment, ecstasy, fear, guilt, interest,
neutral, pain, pride, realization, relief, sadness, serenity — as p003 (f) and
p031 (m). Changing mood means changing the selected voice. Beyond that the
delivery follows the text, since punctuation drives the pacing, but nothing
commands it.

**The licences are not all the same**, which matters if a voice is going into
something you ship. `expresso` and `ears` — precisely the two emotional
collections — are **CC-NC, non-commercial**. `voice-donations` and `voice-zero`
are CC0, `vctk` and `cml-tts/fr` are CC-BY-4.0.

**It would also not share this Python.** `moshi` pins `torch<2.10`,
`numpy<2.3`, `safetensors<0.8` and `huggingface-hub<1.0`, and a machine set up
for Pocket TTS is above all four — hub by a whole major version. The floors do
overlap, so one environment *can* satisfy both, but only by moving four packages
backwards underneath the engine that already works. The answer if it is ever
revisited is a separate virtual environment talking to `moshi.server` over a
socket, not an in-process import: `close()` becomes killing a subprocess, which
the engine-swapping here already expects — and which is exactly how Breeze runs
now, so `breeze_engine.py` is the pattern to copy.

**What would change the answer:** Kyutai releasing the voice embedding model.
Nothing else on this list is a blocker on its own.

## OpenAudio S1 Mini, and why Breeze got the place instead

It laughs, which Qwen never has — tried on 2026-09-24 with a clone of Abby. It is
also slower to the first word, 1.4 s against 812 ms, because it does not stream,
and `(whispering)` did nothing. Breeze was tried the same afternoon with the same
eight lines: it streams, it whispers, and its sad is very sad. The measurements
for both, and the three things that break S1 on Windows, are in
[laughing.md](laughing.md), which is where the next engine tried for this goes
too.

## Why the pieces are so much smaller

Pocket hands back about 80 ms of audio at a time, against Qwen's second. That
suits the player rather than straining it: playback is cut on silence, and more
pieces means more places to cut, so the seams land deeper inside the pauses. See
[how-it-works.md](how-it-works.md) for what the buffering does with them.
