# How it works

Four moving parts, none of them clever on their own.

```
Claude Code  ──writes──▶  session transcript (.jsonl on disk)
                                   │
                            watcher thread  ──▶  strip markdown, pick what to say
                                   │
                            engine thread   ──▶  Qwen talker model  ──▶  wav
                                   │
                            player thread   ──▶  winsound
```

## The engine

`speak_server.py` is a long-running process holding one loaded talker model. Loading takes
the better part of a minute cold, which is fine once and unbearable per sentence — hence a
resident process rather than a command run per line. It listens on `127.0.0.1:8765` and
nothing else: not reachable from your network, let alone the internet.

Qwen-TTS Studio has no CLI. Its `--dump-speaker-embedding` fallback wants a `tts-cli`
binary that is not shipped, and everything else is buttons. So `qwen_engine.py` starts
Studio's **own bundled JVM** in-process through `ctypes` → `JNI_CreateJavaVM`, instantiates
`com.qwen.tts.studio.engine.QwenEngine` off the app's jars, and calls the JNI methods
sitting behind the interface. Verified equivalent to the GUI: re-extracting a voice this
way produced an `embedding.json` byte-identical to the one Studio wrote.

Two constants in there are exact, not approximate:

- **JNI function-table slots.** `CallLongMethodA` is 54, not 51 — 51 is `CallIntMethodA`,
  which silently truncates the 64-bit engine handle to 32 bits and then crashes deep
  inside `qwen3_tts.dll` at model load.
- **`nativePtr` must be set by hand** after `nativeInit`. The Kotlin wrappers such as
  `generate()` read the handle off that field, and normally only `loadDetailed()` sets it;
  leaving it zero passes a null context into native code.

Also: the embedding extractor and the ICL prompt encoder **cannot share a process**.
Loading the ICL encoder tears the talker model down underneath the engine. That is why
`clone --icl-only` is a separate run rather than a flag.

## The watcher

Claude Code writes every turn to a JSONL transcript under `~\.claude\projects\` as it
happens. A thread tails those files and speaks what is new.

This is the part that makes it dependable. Hooks are the tidy way to do this, and they are
installed too — but a hook only runs if the client chooses to run it, and **nothing from
outside can tell whether it did**. That is indistinguishable from the voice being broken,
and cost days here before the watcher existed. Transcripts need no cooperation from
anybody: no hooks, no restart, and it follows every session at once.

Details that matter:

- It starts at the **end** of a file it has never seen, so switching it on does not recite
  a whole conversation at you.
- It **remembers its position** across restarts, so an engine that dies does not swallow
  everything written while it was gone — with a freshness window, because catching up
  after a crash is wanted and reading out last night's backlog is not.
- It **names the session** when the speaker changes, reading the title you see on screen.
  Only on a change; announcing every line would be worse than the confusion it fixes.
- It **stays quiet on runs nobody is sitting in front of**. `claude -p`, an SDK caller, an
  errand one of your own programs sends off: those write a transcript exactly like a
  session you are watching, in the project folder you are working in, and the watcher
  needs no hook to find them — so keeping your hooks out of such a run does *not* keep it
  quiet. Their reports are addressed to the program that asked, not to the room, and
  hearing one read out in full is how this was found. Every user entry carries the
  `entrypoint` that wrote it, and a headless run says `sdk-cli` where a window says `cli`
  or `claude-desktop`. `/voice headless on` if you would rather hear yours come home.

## Choosing what to say

`voice_lib.speech_for()` holds the whole contract, and it is one rule: **a message with a
`## TL;DR` is spoken as that summary alone; a message without one is read in full.**

Before anything is spoken, the markdown is stripped of what only makes sense to the eye —
fenced code, tables, links, URLs, headings, bullet markers — and paths in backticks are
dropped rather than spelled out. `snake_case` becomes words. Typography a terminal shows
happily and a speech model cannot pronounce (`—`, `×`, `≥`, emoji, box drawing) is either
translated or removed.

## Speaking and playing

Synthesis and playback are separate threads, so speech starts on the first phrase instead
of the last. **How fast synthesis runs against how fast speech is heard decides whether
that works**, and it is not a constant: measured between 4× realtime and 0.85× on the same
machine, depending on what else it was doing. Below 1× the player catches up, and then the
gap is heard. See *When the first word is played*.

An answer is **one generation**, streamed. That matters because two takes of the same voice
are not identical — the model rolls its prosody afresh for each, so a reply cut into
sentences came back as a recognisably different person at every seam. Streaming hands the
audio over in pieces as they are made, keeping two seconds of what it has already said as
context, so the voice is continuous by construction and the first word arrives sooner than
a whole first sentence could be synthesised.

Playback still has to be cut somewhere, since the player takes one file at a time. The cuts
are placed **in the gaps between words**, found by looking for the quietest moment near the
end of what has been gathered — never at a fixed length, which is what would be heard as a
stutter. If there is no gap to cut on, it gathers more and asks again.

The pieces **double** in size — 2.5 seconds, then 5, then 10, up to 12 — because each one
has to buy the time to make the next. See *Things that cost real time to find* for what
happens when they do not.

### Pausing, which winsound cannot do

The player is `winsound`, which plays a **file** from its beginning and has no notion of a
position — so for a long time the panel had a stop button rather than a pause one, and
said so in a comment. What makes a pause possible anyway is that every piece is already on
disk as plain mono 16-bit PCM, and the player already knows when it started one:

- **Between pieces it is free.** The player pulls from a queue, and pausing is not pulling.
  Nothing is lost and nothing is written.
- **Inside a piece it is a purge and a slice.** The cancel check runs every 50ms, so the
  place is known to a twentieth of a second. The rest of the piece is copied to a new wav
  — a seek and a copy, nothing decoded — and that is what plays on the way back.

Pieces run up to twelve seconds, which is why the second half matters: pausing only at a
seam would mean pressing the button and being talked at for another ten.

A resume starts **0.2s before** where the pause landed, because a pause almost never falls
on a word boundary and coming back on the exact sample comes back mid-syllable.

Two things follow from pause being a hold rather than a stop. The **queue goes on filling**
while it is held — the engine keeps its model, the watcher keeps queueing, and synthesis
stops on its own after two pieces because the play queue is capped at two and blocks. And
**"quiet now" is not "I never want to hear that"**: emptying the queue is ⏭ skip all,
sitting next to the pause button, which is the honest place for it.

**Nothing barges past a held line.** A finished answer normally takes the floor — the
newest is the one worth hearing while an older one is still being read out. That reasoning
runs out when she is held: nobody is hearing either, so the cut buys no time, and it costs
the sentence you stopped half way through on purpose. It costs more than that, in fact —
taking the floor calls `cancel()`, which empties the queue as well, so one answer arriving
took the held line and everything waiting behind it. The one thing that still interrupts a
hold is clicking a line in the **history**, which is you asking for that line, now.

### The play/pause key

The key on a keyboard's media row, on a headphone cable, on the button on a pair of
earbuds: all of them send `VK_MEDIA_PLAY_PAUSE`, and the engine answers it.

`RegisterHotKey` takes a key from **every** program on the machine for as long as it is
held, and play/pause is not ours to keep — it belongs to whatever is playing. So it is
taken while she is speaking, holding a queue, or paused, and handed straight back the
moment she is idle. Pressing it with nothing to say does what it always did, and nothing
on this side needs to know what that was.

Two things worth knowing. The register and the message loop must be the **same thread** —
Windows delivers `WM_HOTKEY` to the thread that asked and to no other, so a hotkey
registered by a thread that never reads its own queue is a key taken from the machine and
answered by nobody. And a keyboard whose media keys are handled by **its own driver**
never lets the key reach Windows at all; there is nothing on this side to be done about
that, and the panel button is the same action either way. `/voice mediakey off` gives the
key up for good.

### When the first word is played

None of the above helps if the next piece is simply not finished when the last one ends.
That gap is heard mid-sentence, and it is a buffer underrun — the same thing a video does
when the network dips. On an idle machine synthesis runs several times realtime and it
never comes up; put a heavy Windows job beside it — or Claude Code itself — and it falls
*below* realtime, at which point it is losing ground for as long as the message runs.

That last part decides what a lead has to be, and it is worth being exact about.

### Why `auto` is arithmetic and not a guess

At a rate *R* the audio arrives *R* times as fast as it is heard. So *E* seconds of speech
take *E*/*R* to make and *E* to say. Start at once and the difference, ***E*(1/*R* − 1)**,
comes out as gaps in the middle. Wait exactly that long first and there are none — and the
message finishes at the same moment either way. **The wait is not a cost. It is the same
silence, moved to the front where it is one pause instead of ten.**

Which is the case against `whole` on a slow machine, and it is not small. `whole` waits
*E*/*R* — that difference *plus the entire message over again*. On a four-minute answer
measured here: 50 seconds against 3½ minutes, for the same result.

### The half of it that cost a day

That is not the whole sum, and the half that is missing is the expensive one.
**The player takes whole files.** It cannot begin a piece until every sample of it is
written, so at each seam it is waiting for a *whole piece* to be made, not for the next
second of one. Leave that out and the lead pays for exactly one seam and no more — which
is what the first version of this did. It stalled once in the middle of nearly every
message while the arithmetic insisted it was fine, and the engine log was right where the
report was wrong.

Requiring no stall at the last seam gives the lead that is actually needed:

> **L ≥ *E*(1 − *R*) + *R* × PIECE**

So `auto` keeps two numbers from recent messages — the rate, at a low percentile because
it must survive the bad message rather than the typical one, and how much of
`expected_seconds()` turned out to be real, since that overstates by about a third — and
banks that, times 1.5. A machine that keeps up gets 2.5s and notices nothing.

Swept against 42 real messages from a machine at 0.6–0.9×, scored walk-forward so each
message is judged using only the ones before it, and against a **simulation of the player**
rather than against the sum:

| | covered | total waiting |
|---|---|---|
| `instant` | 8/42 | none, and 34 audible gaps |
| `buffered` | 38/42 | 630s |
| `whole` | 42/42 | 907s |
| `auto` | 39/42 | **412s** |

The three `auto` misses are the first three messages a brand-new machine ever says, before
there is anything to learn from — and only ever once, since the last twenty are read back
off the trace at startup.

### What a seam actually costs

The sums above cost a seam at `TAIL` — the fifth of a second the player waits past the end
of a clip — and on that basis six-second pieces beat twelve-second ones outright. Shipped
it; the ear disagreed within the hour.

A seam is not a fifth of a second and, more to the point, **it is not merely an
interruption.** The cut is placed where the audio is quiet, and an ordinary gap between two
words is quiet — so the pause lands mid-phrase rather than at a full stop. Worse, the
silence *between* two messages is deliberate and about the same length (`gapSeconds`, so a
new line is audibly a new line). The two are not tellable apart. A seam therefore does not
just break a sentence: it makes you lose your place in it and wonder whether you missed the
start of something else. Waiting at the front costs attention once; a seam costs it every
time, and costs more.

So `PIECE` is back to 12 seconds, and `auto` now decides **out loud** between a lead and
banking the lot: starting early buys back (*E* − *L*)/*R* of waiting and costs one seam per
piece, and a seam is priced at `SEAM_COST`, 8 seconds. Which gives, at 0.81×:

| the message | what happens | you wait | `whole` would be |
|---|---|---|---|
| 6s of speech | whole, no seams | 7s | 7s |
| 17s | whole, no seams | 22s | 22s |
| 49s | 24s lead | 29s | 60s |
| 155s | 54s lead | 67s | 191s |

Short answers — most of them — arrive in one piece with no join in them anywhere. Only a
genuinely long one is broken up, and only because the alternative is three minutes of
silence before it starts.

`SEAM_COST` is the one number here that is a judgement rather than a measurement. The
player times every seam now (`seam of 0.62s between pieces` in the log, and `seam` in
`/health`), so it can stop being one.

So `playback` decides how much audio is banked before the first word — the lead that a
stall is spent from instead of being heard. Measured on a stub engine at a known rate,
on a 36-second answer:

| `playback` | banks | first word at 4× | gaps at 4× | gaps at 0.9× |
|---|---|---|---|---|
| `auto` *(default)* | as much as it must | 1.3s | none | none |
| `instant` | 2.5s | 1.3s | none | 3 |
| `buffered` | 15s | 5.5s | none | 1 |
| `whole` | all of it | 12.8s | none | none |

It changes **when** the audio is handed over and nothing else: it is one generation in
every mode, because several would bring the changing voice back. `whole` hands over a
single file, after which a gap is not unlikely but impossible.

Below realtime, no finite lead is enough — synthesis is losing ground for as long as the
message runs — which is the whole reason `whole` exists rather than a bigger `buffered`.
The engine re-reads this **per message**, so it can be changed while the voice is breaking
up rather than after a restart. The log says `playback: whole` when it changes, and
`playback ran dry mid-message` each time the player actually starved.

Every message also writes a line to `logs\playback-trace.jsonl` — when each piece of audio
arrived, and how much existed by then. That curve is the only machine-dependent thing in
the whole question, and **`playback report`** turns it into the one number that matters:

> Playback started at time *s* is at position *t − s*, so it stalls the moment *t − s* runs
> past what has been made. Turn that round and the earliest safe start is the largest gap
> between a piece arriving and the audio in hand just before it. The lead needed is simply
> how much audio that is.

Exact, per message, after the fact — which is a better basis for an automatic mode than a
threshold picked in advance. Lengths and timings only; nothing of what was said is written
there, and nothing is sent anywhere.

The old road — a generation per chunk — is still there behind `streaming: false`, for a
Studio build that ships without the streaming entry points. It has the seam.
**[Why, and what it cost to find →](engine-notes.md)**

Lines **queue** rather than interrupt. Barging in was right when one hook spoke one
finished answer; it is wrong for a running commentary, where cutting off the previous line
mid-word simply loses it. Only a deliberate `say`, `replay` or `stop` takes the floor.

Between two messages there is a real pause (`gapSeconds`). That seam is worth keeping — it
is how you hear that a new line has started rather than the same one continuing.

## Threading, and one trap

A **JNIEnv pointer belongs to the thread that created the JVM**. The engine therefore lives
on one dedicated thread and HTTP handlers only enqueue; calling `generate()` from a request
thread is undefined behaviour, not a race you get away with.

And do not name a method `_handle` on a `threading.Thread` subclass. CPython 3.13 keeps an
attribute of that name on the instance, it shadows the method, and you get
`'_thread._ThreadHandle' object is not callable` from a line that looks perfectly correct.

## Things that cost real time to find

Kept because they will not be obvious to the next person either. The ones about the
*engine* misbehaving — stopping early, changing voice between sentences, buzzing — have
their own page now: **[engine-notes.md](engine-notes.md)**, with the measurements behind
each and where the guards are still missing.

**A pause at a seam was being paid for three times.** `quiet_cut` found the quietest 40ms
window and cut at the end of it — but a pause in speech is far longer than 40ms, so the
rest of it survived: some trailing the piece, the remainder leading the next, and both
were played, with the 0.2s handover in the middle. Three silences where the engine made
one. It landed by preference on commas and full stops, because the quietest window is
exactly where punctuation is, so the message stopped dead where it was already pausing
for effect. Measured across eight consecutive messages: median heard gap 0.42s, worst
0.88s, on a machine with *zero* underruns and synthesis running at 4.3× realtime.

The tell was that nothing in the logs showed it. `seam_typical()` measures from the end of
one **file** to the start of the next, so the silence inside the audio was invisible to it
— it reported 0.227s while the room heard 0.87s — and `SEAM_WORTH_SAYING` was 0.40, above
every value the meter could ever produce. *An instrument whose threshold sits above its own
subject will report healthy forever.* The fix is `quiet_span`: find the whole silent run,
subtract what the handover supplies, keep the remainder. Streaming now pauses for exactly
as long as `whole` mode does, which is the right target — that mode never had the fault.
The same pass drops the engine's opening dead air, which was up to 0.86s of pure latency
before the first word.

**A piece must pay for the next piece, not for one five times its size.** The first piece
was 2.5 seconds and every piece after it was twelve. That first piece therefore bought 2.5
seconds in which to synthesise twelve — which needs three, *on an idle machine at 4×
realtime*. So there was a gap about two seconds into every message, and it was blamed on
the machine being busy for a long time before anybody did the arithmetic. Doubling costs
nothing — the first word still arrives on the first 2.5 seconds — and it removes the step
entirely.

**Hooks are not dependable, so this does not need them.** A hook runs only if the client
chooses to run it, and nothing from outside can tell whether it did — which looks exactly
like the voice being broken. Claude Code writes every turn to a transcript on disk
regardless, so that is what gets followed. No hooks, no restart, every session at once.

**`SO_REUSEADDR` means something else on Windows.** `HTTPServer` sets it, and Windows then
lets a *second* process bind a port already in use rather than failing — two engines, two
model loads, every sentence spoken twice. Hence the explicit refusal and the health check
before binding.

**A synchronous `PlaySound` cannot be interrupted.** A purge from another thread does not
cut it; it queues up behind it and returns once the clip has finished of its own accord. So
`stop` quietly meant "drop the queue and wait out this sentence" — measured at 5.8 seconds
on a long one. Playback is asynchronous now and a purge cuts within about 0.12s.

**A pause you cannot see is worse than a slow one.** Chrome can be told to pause a video
with a window message aimed at it, and it obeys instantly — but its audio is already in
flight, so a check a fifth of a second later still hears sound and calls it a failure. The
video is paused; nothing recorded that it was; nothing ever puts it back. Wait for silence
rather than demanding it. That, and the rest of what a browser will and will not answer,
is in **[pausing-other-media.md](pausing-other-media.md)** — the feature itself was built,
proved and deliberately left out.

**Saving a config wrote every default into it**, freezing them, so improving a default later
reached nobody who had already run the tool. Only what differs from the defaults is written.

**A filter that asks for `[A-Za-z0-9]` is asking "is this English".** Two of them decided
whether a line was worth speaking, and threw away Russian, Greek and Chinese as though they
were punctuation. The test is for a letter or digit in any script.

**A PATH is a property of a process, not of a machine.** The panel is spawned from the Desktop
shortcut, so it gets Explorer's copy, built from the registry at login; a terminal gets
whatever started it, and Claude Code prepends a git of its own for its children. So `git`
resolved for the command line and not for the button on the same machine — and the button was
the one telling somebody their machine had no git. Nothing that shells out now assumes PATH
knows where a tool is: `update_check._git_exe` asks PATH first and then the folders git
installs into.

**A BOM is not invisible.** `install.ps1` writes UTF-8 without one, because Claude Code
stops reading `settings.json` the moment it finds a BOM there — and PowerShell's `>`
redirect adds one by default.
