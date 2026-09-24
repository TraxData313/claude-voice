# Teaching her to laugh

Qwen clones a voice well, and it can be told how to read a line — see
[Telling Qwen how to say it](engines.md#telling-qwen-how-to-say-it) — but it has
never laughed. This page is the hunt for an engine that can: laugh, sigh or
whisper when asked, in a cloned voice, on this laptop. Each engine tried gets a
section here — what it did, what it cost, and what would change the answer.

The rules are Toni's. It has to clone a voice rather than offer presets, and it
has to run here: an RTX 5080 Laptop with 16 GB, under Windows 11. The bar is Qwen
as it runs here: **812 ms** to the first audio, **3.7–4.0×** realtime, streamed.

| engine | laughs | whispers | first audio | throughput | runs on |
|---|---|---|---|---|---|
| Qwen, the engine here | never | not tried | 812 ms | 3.7–4.0× | Windows |
| OpenAudio S1 Mini | **yes** | no | 1.4 s | 1.6–2.7×, 3.9× on longer text | Windows, past three traps |
| Breeze TTS 2 | **yes** | **yes** | 0.18–0.37 s | 1.6–2.1× | Windows, despite its docs |

## Why S1 Mini went first

The search on 2026-09-24 put Breeze TTS 2 in front, but Breeze's docs say Linux
only, and this machine has no WSL. (It turned out to run on Windows anyway.) OpenAudio S1 Mini (Fish Audio, 0.5B
parameters) tied the leading open models on the Artificial Analysis cloned-voice
arena (1014 ±15), runs on Windows, and documents markers such as `(laughing)`,
`(sighing)`, `(sad)` and `(whispering)`. So it was the cheap test: an hour, not a
Linux install.

## OpenAudio S1 Mini: it laughs

Tried on 2026-09-24, with fish-speech at `d3df50503b` — the last commit before
the repository moved on to S2, whose 24 GB would not fit. The model stayed
loaded in fish-speech's own `tools.api_server`. The voice was cloned from the
24-second Qwen render that Pocket's Abby was made from
(`voices\female\abby\pocket-reference.wav`), with the transcript read out of
`make_pocket_voice.py` rather than retyped: a transcript that drifts from the
audio teaches the model a wrong alignment, and the model would take the blame.

Eight lines, each rendered twice, because whether a laugh lands *every* time is
half the question:

```
1  Okay, the tests pass now. All forty-two of them.
2  (laughing) Ha,ha,ha! Okay, I did not expect that to work on the first try.
3  (chuckling) Hmm,hmm. You caught me. I got the watermelon wrong again.
4  (sighing) Fine. Let's run it one more time.
5  (sad) I looked everywhere, and I couldn't find it. I'm sorry.
6  (excited) Wait, it's working! Toni, come and listen to this!
7  (whispering) Don't tell anyone, but I think this one's my favourite.
8  So I told it to stop, and it just kept going, (laughing) ha,ha, like it hadn't heard me at all.
```

The markers follow Fish's own advice: emotion at the start of a sentence,
`Ha,ha,ha` after a laugh, `Hmm,hmm` after a chuckle. Sampling was the server's
defaults — top-p 0.8, temperature 0.8, repetition penalty 1.1.

### What it sounded like

Toni listened to the compiled takes. The numbers further down were measured;
these were heard.

| line | marker | heard |
|---|---|---|
| 2 | `(laughing)`, opening the line | **laughs** |
| 8 | `(laughing)`, in the middle | laughed in one take; the other made a long breathy *hhhhhh* instead |
| 5 | `(sad)` | a little sad — about what Qwen already manages with an instruction |
| 7 | `(whispering)` | no whisper |
| 3, 4, 6 | `(chuckling)`, `(sighing)`, `(excited)` | not singled out either way |

So it laughs, which Qwen never has, and that alone makes it the first real step.
A laugh that opens a sentence is the safe form. One in the middle came off once
in two tries, which is thin evidence either way.

### How fast

| | compiled | uncompiled |
|---|---|---|
| a line, start to finish | 1.2–1.6 s | 3.3–7.2 s |
| throughput on those lines | 1.6–2.7× realtime, median 2.2× | 0.53–0.70× |
| three sentences, 8.7 s of audio | 2.25 s, 3.9× | 12.8 s, 0.7× |
| first audio | when the whole line is done | when the whole line is done |
| tokens per second | 115–137 | 12–16 |
| server start | 292 s, 270 of it compiling | 26 s |

Uncompiled is slower than speech, so `--compile` is not optional.

**"Mini" is the parameter count, not the speed.** A token here is one frame of
audio — about 46 ms, ten codebooks deep — and every frame costs a pass through
the 28-layer model and ten through a 4-layer one. Uncompiled that manages about
15 tokens a second. Compiled into CUDA graphs it manages 130, which is about six
times realtime, and then two things spend most of it:

- **There is no streaming at this commit.** `generate_long` renders the whole
  request in one pass and the codec decodes it in one piece. The API's
  `streaming: true` still sends everything at the end — and as bare 16-bit PCM,
  because it builds a WAV header and then drops it: only `bytes` pass its
  filter, and the header is an ndarray. So the first audio is the end of the
  render, and it grows with the text: 1.4 s for line 1, 2.25 s for three
  sentences.
- **About 0.85 s of every compiled request is spent outside the model**, against
  0.25 s uncompiled. The code on either side of the model is the same in both
  modes, so most of that looks avoidable. It was not chased.

Rendering sentence by sentence, and finding that 0.6 s, might bring a short
sentence to about 0.7–0.8 s — level with Qwen. That is an estimate, not a
measurement.

### Memory

It holds 4.6 GB idle, peaks at about 5.3 GB per line, and reaches 6.7 GB once,
while it encodes the reference clip. That sat beside the 6.5 GB other programs
were already using, and nothing had to be stopped. Windows does not report
memory per process, so these are the whole card's use minus what was in use
before the server started; torch's own figure in the server log agrees.

### Getting it running on Windows

fish-speech says Linux or WSL. It runs on Windows, past three traps, each of
which fails quietly or late:

1. **The `cu128` extra installs CPU torch under pip.** The CUDA index is chosen
   only in `[tool.uv.sources]`, which pip ignores, and the torch on PyPI has no
   CUDA on Windows. Install torch from the PyTorch index first, then pin it so
   nothing can swap it back:

   ```powershell
   pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
   pip install -e ".[cu128]" -c constraints.txt   # pins torch==2.8.0, torchaudio==2.8.0
   ```

2. **Torch has to stay below 2.9.** The reference loader calls
   `torchaudio.list_audio_backends()`, which 2.9 removes. The project's own
   `stable` extra says `torch<2.9.0`.
3. **Every compiled kernel dies with `OverflowError: Python int too large to
   convert to C long`.** Inductor's static CUDA launcher, on by default in torch
   2.8, passes a pointer through a C `long`, and on Windows that is 32 bits. Set
   `TORCHINDUCTOR_USE_STATIC_CUDA_LAUNCHER=0` and launches go through Triton's
   own launcher, which works.

`--compile` needs Triton, which Windows lacks. `pip install "triton-windows<3.5"`
matches torch 2.8 (3.4.0.post21 here) and brings its own compiler pieces, so
there is no Visual Studio or CUDA toolkit to install. Torch 2.8's cu128 build
includes sm_120, so the Blackwell card needed nothing special.

The weights, `fishaudio/s1-mini` at 3.4 GB, are gated: accept the terms on the
model page once. They are **CC-BY-NC-SA-4.0**, non-commercial and share-alike —
fine at home, and something to settle the moment a voice goes into anything
shipped.

It all lived outside this repo, in its own conda environment (Python 3.12, 8.4 GB
with torch), and took about fifteen minutes to install, most of it downloading.

### What would change the answer

S1 Mini is not the third engine yet. It would need:

- first audio near Qwen's, from rendering sentence by sentence and finding the
  0.6 s;
- laughs that open a sentence landing every time, over more than two takes;
- a licence that suits wherever the voice is going.

`(whispering)` did nothing; `(soft tone)` was not tried.

## Breeze TTS 2: it laughs and whispers

Tried on 2026-09-24, with breeze-tts at `008f769` and the weights at Hugging
Face revision `3e28c51`. BreezeBlue's 3B model led the search: a clone, a
spoken instruction on top of it, `(laugh)` and `(sigh)` tags, and streaming —
exactly where S1 Mini fell short. The model stayed loaded in Breeze's own
streaming API, `breeze_infer.api`, run through a thin wrapper that only adds a
way to read torch's memory figures. The voice was cloned from the same
24-second reference as S1's, transcript read out of `make_pocket_voice.py`.
Breeze asked for nothing different: its docs want clean audio and the exact
transcript, and the clip went in whole, at the 24 kHz its codec works at anyway.

The same eight lines, each rendered twice, in Breeze's documented forms:

```
1  Okay, the tests pass now. All forty-two of them.
2  (laugh) Okay, I did not expect that to work on the first try.
3  (laugh) You caught me. I got the watermelon wrong again.
4  (sigh) Fine. Let's run it one more time.
5  I looked everywhere, and I couldn't find it. I'm sorry.
     + Speak slowly and sadly, quiet and downcast, with long pauses.
6  Wait, it's working! Toni, come and listen to this!
     + Speak quickly and excitedly, bright and breathless, full of delight.
7  Don't tell anyone, but I think this one's my favourite.
     + Whisper quietly and secretively, as if leaning in to share a secret.
8  So I told it to stop, and it just kept going, (laugh) like it hadn't heard me at all.
```

Breeze is told how in two ways. A sound is a tag in the text: `(laugh)`,
`(sigh)`, `(cough)` or `(clears throat)`, and nothing else. A manner is an
instruction, a sentence of its own beside the text, which switches it to what
BreezeBlue call voice direction and wants `cfg_scale` 4. Its examples put a tag
alone, with no *ha,ha* after it, so S1's companion words went; there is no
chuckle, so `(laugh)` stands in on line 3. Line 5 carries the very instruction
Qwen was measured with in [engines.md](engines.md#telling-qwen-how-to-say-it).
Sampling was the API's defaults — temperature 0.9, top-k 50, repetition
penalty 1.1.

### What it sounded like

Toni listened to both sets, four takes of each line. In his words: she
whispers and laughs, the sad is very sad, and the voice is very good.

| line | sent | heard |
|---|---|---|
| 2, 3, 8 | `(laugh)`, opening the line and in the middle | **laughs** — not told apart line by line |
| 7 | the whisper instruction | **whispers**, where S1 did nothing |
| 5 | Qwen's own sad instruction | **very sad**, where S1 and Qwen managed a little |
| 1, 4, 6 | plain, `(sigh)`, the excited instruction | not singled out either way |

Two measurements point the same way, though they decide nothing on their own:
the whisper takes came out 13–20 dB quieter than the plain ones, and the sad
takes ran 7–11 seconds against S1's three.

### How fast

| | eager | fast decode stages |
|---|---|---|
| first audio, a short line | 0.37–0.89 s | 0.18–0.21 s; 0.30–0.37 s with an instruction |
| throughput | 0.46–0.65× realtime | 1.6–2.1×, median 2.0× |
| three sentences | 9.2 s of audio: first sound 0.37 s, done 14.5 s | 10.0 s of audio: first sound 0.20 s, done 4.7 s |
| stream ran dry | on every line, 1–9 s in all | never |
| server start | 12 s | 113 s, 103 of it compiling and capturing graphs |

**It really streams.** The first sound arrived a fifth of a second in, while
the rest was still being made — the paragraph came in 125 pieces.

**Eager is slower than speech, so the fast stages are not optional.** Each
frame is 80 ms of audio and costs one pass through the 28-layer backbone and
fifteen through a 12-layer depth decoder, each pass a string of small kernel
launches. Eager spends its time launching them, and the stream runs dry; the
fast stages capture each step as a CUDA graph and replay it in one go. Three of
the five stages do that work: `--fast-backbone-decode --fast-depth-decoder
--fast-codec`. The other two, the text encoder and the prefill, run once per
line and could only shave a first sound that already comes four times sooner
than Qwen's.

**Some of that first sound is avoidable.** The API encodes the reference clip
again on every request — twice with an instruction, once for each CFG branch —
and that costs 0.05–0.11 s. Encoding it once would leave a plain line at about
0.13–0.16 s. That is an estimate, not a measurement.

### Memory

Eager holds 7.4 GB idle and peaks at 8.1–8.9 GB per line. The fast stages hold
8.6 GB idle and 9.1–9.2 GB per line, but reach **13.1 GB while they start**,
compiling and capturing; torch's own count agrees. The card had about 2 GB in
use during these runs. With the 6.5 GB of the S1 test, LM Studio holding a
model, the fast start would not have fit. `--fast-all` is documented at
14.4 GiB and cannot fit a 16 GB card beside the desktop, so it was not tried.
As with S1, these are the whole card's use minus what was in use before.

### Getting it running on Windows

Breeze's README says Linux, and everything written about it repeats that. It
runs on Windows unchanged: the default path is plain PyTorch, `qwen-tts` and
FastAPI, and the API asks for eager attention everywhere, text encoder
included. flash-attn appears only in the Docker image. What it took:

1. **Torch from the PyTorch index, pinned with its label.** `torch==2.9.1` in
   a constraints file would still let pip swap in PyPI's CPU build;
   `torch==2.9.1+cu128` makes that impossible rather than unlikely.

   ```powershell
   pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128
   pip install -r requirements.txt -c constraints.txt   # torch==2.9.1+cu128, torchaudio==2.9.1+cu128
   ```

2. **triton-windows 3.5.1.post24, for the two compiled stages.** The depth
   decoder and the codec's SnakeBeta activations go through `torch.compile`;
   the backbone stages are plain CUDA graphs and need nothing.
   `TORCHINDUCTOR_USE_STATIC_CUDA_LAUNCHER=0` was set from the start, after
   S1's OverflowError; whether Breeze needs it too was not tested.
3. **Two warnings that do not matter.** `qwen-tts` says SoX and flash-attn are
   missing. Breeze reads audio through soundfile, and attention falls back to
   plain PyTorch.

Torch 2.9.1's cu128 build includes sm_120, so the Blackwell card needed nothing
special. The weights, `BreezeBlue/Breeze-TTS-2` at 7.2 GB, are not gated, but
they are **non-commercial**, and so is anything a self-hosted copy says — fine
at home, and something to settle before a voice goes into anything shipped.

It all lives outside this repo, on D:, because C: had 21 GB free: a conda
environment (Python 3.12, 5.4 GB), the code and the weights. Installing took
about ten minutes, most of it the 2.9 GB torch wheel.

### What happened next

Nothing needed to change the answer: Toni heard what he was listening for, and
the same day Breeze became the third engine —
[Breeze TTS 2: the one that laughs](engines.md#breeze-tts-2-the-one-that-laughs)
is how it runs there, and [api.md](api.md) is how a program asks it to laugh or
be sad. What it lives with:

- a start of about half a minute, two the very first time, that needs 13 GB
  free and holds 9 GB afterwards — so one GPU engine at a time;
- about half Qwen's throughput, which is still twice as fast as speech;
- English and Chinese only, and a licence that rules out anything shipped.
