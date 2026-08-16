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
of the last. Roughly 4× realtime, so the player never catches up with the synthesiser.

Every chunk is a separate generation, and two takes of the same voice are not identical —
the timbre drifts across a boundary, heard as the speaker changing mid-thought. So
boundaries are spent carefully: **only the first chunk is short**, to get speech going, and
the rest is spoken in one take.

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

Kept because they will not be obvious to the next person either.

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

**Saving a config wrote every default into it**, freezing them, so improving a default later
reached nobody who had already run the tool. Only what differs from the defaults is written.

**A filter that asks for `[A-Za-z0-9]` is asking "is this English".** Two of them decided
whether a line was worth speaking, and threw away Russian, Greek and Chinese as though they
were punctuation. The test is for a letter or digit in any script.

**A BOM is not invisible.** `install.ps1` writes UTF-8 without one, because Claude Code
stops reading `settings.json` the moment it finds a BOM there — and PowerShell's `>`
redirect adds one by default.
