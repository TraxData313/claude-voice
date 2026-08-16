# When she stops, changes voice, or buzzes

Three complaints that sound like one bug and are not. Each has its own cause, its own place
in the pipeline, and its own fix — so the first job is always to tell them apart.

The measurements come from two places. The engine numbers were taken in the sister project
**ImmersiveAI**, which drives the same `qwen3_tts.dll` through its own C ABI rather than
through Studio's Java wrappers, on an RTX 5080 Laptop; nothing there is inferred from
documentation, because there is none. The numbers about *this* tool were re-derived here,
from `logs\speak-server.log`, using their detector.

| What the listener says | Where it actually happens | State |
|---|---|---|
| "She stopped in the middle of the summary" | before the engine — the text was cut | **fixed** — the ceiling is a runaway guard now, and it says when it fires |
| "She buzzes, or babbles on" | inside one generation — a derail | **fixed** — measured, detected and re-synthesised |
| "She changes voice between sentences" | the seam between two generations | **fixed** — an answer is one streamed generation now, so there is no seam |

---

## 1. Stopping mid-summary never reaches the engine

`speech_for` caps a TL;DR at `maxChars` — **600 at the time this was written** — and
`truncate` cuts at the last sentence boundary that fits. Everything after that boundary is
dropped before anything is synthesised.

That is why it does not sound broken. The cut lands on a full stop, the intonation falls,
and the listener hears a sentence that ends properly — it simply was not the last one. A
summary that lost its final "and here is what you have to do next" is indistinguishable
from one that never had it.

Measured on the case that started this note: the spoken part ran to **599 characters**, one
under the ceiling. Two sentences were dropped.

**What changed.** Two things, and the second matters more than the first.

The ceiling is now **4000 characters**, the same as the one on an answer with no TL;DR. It
is a runaway guard rather than an editorial rule: someone who finds an answer long can stop
it with a keypress, and someone who is never told the end was dropped can do nothing at
all. Trading a silent cut for a long read is a trade worth making every time.

And `truncate` now **says so when it fires** — how many characters went missing, into the
server log or the hook's trace depending on who asked. `voice_lib` has no logger of its own,
so it exposes a `notify` that whoever imports it points at their own; the default does
nothing, which keeps the library importable from anywhere.

The writing rule stands anyway, for a different reason: a summary is *heard once, in order*,
and five sentences is about what survives that. It is no longer a guillotine, so overrunning
costs the listener time rather than the ending. See
[writing-for-the-ear.md](writing-for-the-ear.md).

## 2. The voice changing is the seam between chunks

Every chunk is a separate `generate()` call, and two generations from the same speaker
embedding are not the same voice to the ear. Timbre, pace and mouth size drift a little
across the boundary, and the listener hears the speaker being swapped mid-answer.

`chunks()` used to spend boundaries as sparingly as it could — only the first chunk short,
to get speech started — and that was the right shape for the wrong problem. It still left a
600-character summary as roughly three generations and two seams, and in the log **159 of
528 messages were multi-chunk**, up to four pieces. So it was heard on a good third of
everything said, and the longer the answer the worse it got. That is exactly the "on large
sessions" part of the complaint, and no amount of careful chunking fixes it.

**The answer was a call the tool was not making.** The DLL exports
`qwen3_tts_synthesize_with_speaker_embedding_streaming` beside the plain one: *one*
generation that hands its audio over in pieces as they are made, keeping two seconds of what
it has already said as context for the next. The voice is then continuous by construction
rather than by care. The sister project's own note on it is worth quoting, because it is the
same diagnosis arrived at independently — the model "rolls its own prosody afresh each time",
so the same voice came back as a recognisably different person at every seam, in a playtest.

An answer is now one streaming generation, and the Kotlin `generate()` road remains behind
`streaming: false` for a build that lacks the exports.

### What it took, and what it measured

Reaching the streaming export means leaving the JNI wrappers and calling the DLL's own C
ABI, which is why it was not done first. Three things made it safe rather than reckless:

- **The context pointer is already ours.** `nativeInit` hands back the handle that the JNI
  wrappers have been forwarding as `ctx` all along, and we already set it by hand into
  `nativePtr`. Nothing new has to be created, and no second model is loaded.
- **Opening the DLL again gets the same module.** The JVM's own `ensureNativeLoaded()`
  already loaded it in the order ggml and CUDA need; `CDLL` on the same path takes a
  reference to it rather than a second copy.
- **The layouts were not guessed.** They are the sister project's, derived from the
  disassembly of these same JNI wrappers and proven over 200 syntheses, and every offset was
  re-asserted here before a single call was made. That check is worth keeping: a wrong offset
  does not raise, it reads whatever else is there, and ggml's answer to a bad state is
  `abort()`.

Measured here on the first working run:

| | |
|---|---|
| Time to first audio | **812 ms**, against 1967 ms for the first of two chunks |
| Throughput | 3.7–4.0× realtime, so playback is never caught up with |
| Piece cadence | 1.04 s of audio at a time — 13 frames, from the 1.0 s knob |
| A 461-character answer | one generation, **3 playable units**, first ready at 1263 ms |
| Early stop | asked to stop after two pieces, it stopped at 2.08 s and closed cleanly |

**The text alignment is not usable for cutting, and that is worth knowing before trying.**
Each streamed piece reports which byte range of the input it covers, which looks like exactly
the right way to cut playback at a sentence end. It lags badly: on a clip that spoke all 213
bytes, the counter stopped reporting at 140. The audio was complete — 12.00 s streamed
against 12.64 s for the same text blocking, which is ordinary take-to-take variation — so
nothing was lost, but anything trusting that counter would have cut the answer short.

Silence does not lag. There were eight clean gaps in twelve seconds of speech, far more cut
points than an answer needs, and cutting on the quietest window near the end of what has
been gathered puts every seam inside a pause. Measured on a three-unit answer, the last 40 ms
of each unit peaked at 0.018, 0.010 and 0.003 — silence, all three.

**One thing the chunker still does.** `chunks()` targets 480 characters instead of 320 now.
That only matters on the fallback road, since streaming rejoins the pieces and speaks the
lot; it stays because if the fallback is ever needed, fewer seams is better than more, and
the length check makes a bigger piece affordable.

## 3. The buzzing is a derail, and there was no guard at all

Autoregressive TTS misses its end-of-speech token and then generates until it hits its own
ceiling. What comes out is babbling, a held vowel, or a screech. It happens **in the middle
or at the end, never at the beginning** — it is drift, not bad conditioning.

Studio's default `maxAudioTokens` is **4096**, and one audio token is **exactly 1920 samples
= 80 ms**, so the default ceiling is **327.68 seconds**. Observed live in the mod, unprompted,
while something else was being measured:

> 202 characters of Bulgarian → **327.68 s of audio** (5½ minutes), 139 s of GPU.

327.68 is 4096 × 80 ms to the sample. The same text read cleanly in 15.3 s a minute later,
so it is a dice roll and not a property of the text.

**It happens here too, and the log proves it.** Their detector is "far more audio than the
text justifies", and this log carries both halves of that ratio — `n_tokens` from the
prefill graph and `frames/samples` from the vocoder. Across **989 generations**:

| | |
|---|---|
| Audio frames per text token, median | **2.63** |
| Second-worst line | 4.17× |
| **Worst line** | **8.66×** — 29 text tokens → 251 frames, **20.08 s** where about 6 s was honest |

The worst one stands well clear of everything else, which is what a derail looks like next
to ordinary variation. About one generation in a thousand: rare enough never to show up
while testing, common enough to be heard several times in a long day.

**Nothing used to check it.** `synthesize()` returned the samples, `write_wav` wrote them,
the player played the lot — no comparison against the text, no retry, and the clip was then
kept in history like any other, to be replayed on demand forever.

### The guard, and where its numbers come from

`Speaker._say` now measures every clip against its own text and re-synthesises anything that
is not speech. The arithmetic is the mod's, but the thresholds were re-derived here, because
a different voice reads at a different pace and theirs is not ours. Every generation in this
tool's log — **1005 of them** — was replayed against the estimate:

| | |
|---|---|
| Honest speech, against its own estimate | **0.20× to 1.18×**, median 0.68× |
| The one derail in that log | **2.21×**, alone, with nothing between it and 1.18 |
| Ceiling set at 1.8× | clears the slowest honest line by half again, and still caught it |
| False positives across the whole run | **zero**, and stable however the estimate is calibrated |

That gap between 1.18 and 2.21 is the whole reason this works. A derail is not a long
reading; it is a different kind of object.

**The floor had to be moved, and that is worth recording.** Their 0.35× "swallowed" test
flagged **fifteen perfectly good short lines** here — Abby reads a short line far faster
than the estimate allows for, and the estimate's 1.5 s of grace is most of a short line's
budget. The only thing worth catching at that end is the catastrophic shape, not the merely
brisk one, so it is now a tenth of an honest reading and never more than 0.6 s. Zero false
positives, and it still catches the 1920-sample clip.

The rest, taken as-is because it is arithmetic and not taste:

- **Honest duration** = 1.5 s of grace at the ends + `chars / 13`. Measured speech ran
  13–17 characters a second; the low end is the safe one, because being generous costs
  nothing and being mean truncates a real sentence.
- **Never call a short line a derail.** The ceiling has a floor of 3.2 s under it, or
  "Right, that is done." gets called a runaway on a leisurely reading.
- **Discard and retry once**, because a derail is a dice roll and not a property of the
  text — the same words read cleanly a minute later over in the mod. At better than 4×
  realtime a re-synthesis costs about a second per four seconds of speech, and the listener
  never learns it happened.
- **Twice is not luck.** Then it keeps whichever attempt came nearest an honest reading and
  cuts the tail off at the ceiling. A derail *drifts*, so every word before it is good; what
  the listener gets is a sentence that ends early rather than one that dissolves into noise.
  This is the only path on which a suspect clip reaches the history, and by then it is the
  clean head of one.

The same detector works from the other end: **far *less* audio than the text justifies is
the same failure.** The ICL road on a base model mostly returns success with 1920 samples —
eight hundredths of a second — and anything that only checks "did it return" sails straight
past it. We default to `embedding` and should keep doing so; ICL is the better clone on
paper and is the broken one in practice on a base talker.

### Two ceilings now, and the second trusts nothing

Going to the C ABI for streaming brought the real knob with it. `maxAudioTokens` sits at
offset `0x00` of the params block and is honoured to the sample — ask for 256 tokens and
20.48 s is what comes back, from any text — so it is set per utterance from that utterance's
own honest length. Kotlin's `generate()` could never reach it: seven arguments, none of them
a params block.

The second ceiling depends on nothing at all. Every streamed piece is counted as it arrives,
and returning zero from the callback is the engine's own documented way of being asked to
stop early. Proven live: told to stop after two pieces, it stopped at 2.08 s and closed
cleanly. The first ceiling depends on a field still meaning what it means; this one depends
on arithmetic we do ourselves, and it is what catches the day a model arrives with a
different token rate.

Stopping is safe for the same reason the after-the-fact truncation is: a derail *drifts*, so
everything handed over before it is good speech. On the streaming road the listener hears the
answer stop early rather than dissolve — and, because playback has already started, hears the
good part while the rest is still being decided about.

---

## What a long run leaves behind

The engine is meant to stay up for days, so everything it accumulates is bounded — but only
two of the three were bounded on purpose, and the third had been growing since the tool was
written.

**Spoken audio: a ring of 40 utterances** (`historyKeep`), oldest dropped and its wavs
deleted as a 41st arrives, and the whole folder emptied at every start. That is what the
panel's clickable history is reading, so it is the same 40 either way. Raise it if you want
to scroll back further:

```powershell
python voice_cli.py history 60
```

Worth knowing that the summary ceiling going from 600 to 4000 characters made each utterance
up to six times bigger, so 40 of them is now a few hundred megabytes at the pathological end
rather than tens. It is in the temp folder and it is emptied at every start, so this is a
number to know rather than a thing to worry about.

**The server log: capped at 8 MB now, keeping one previous.** This one was the leak. The
engine writes about a kilobyte per spoken line — timings, memory, the prefill graph — and it
was opened in append mode and never rolled, so it grew for as long as the tool had been
installed. It rolls at a start rather than mid-run, which is the simple place to do it:
nothing holds the handle yet, and *read the log first* still finds the whole of the session
being debugged.

**Resident memory does not grow.** Worth stating because it is the thing people assume: RSS
sat at 391 MB across the whole of that thousand-generation log, start to end, with the VRAM
held by the model and freed on release.

## Things that cost real time to find

**A failed allocation aborts the process.** ggml's failure mode is `GGML_ASSERT` → `abort()`,
which no `try`/`except` sees. That is survivable here only because the engine already lives
in its own process; it is the whole reason the mod refuses to run it in-process at all.

**The engine is not re-entrant.** One request at a time. Already true here — the engine
thread is the only caller — and it must stay true.

**"It returned successfully" is not "it worked".** Both real failure modes return success:
the derail returns minutes of noise, the broken ICL road returns 0.08 s of nothing. Length
against text is the only honest check either way.

**Measure more than once.** The first call after a model load ran at 2.26× realtime against
about 3.0× steady state. A single timing is a warm-up, not a throughput.
