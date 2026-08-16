# Writing for the ear

Half of whether this feels good is the speech engine. The other half is what it is handed.
An answer written for a screen is written for eyes that skip around, backtrack, and ignore
the code block; read aloud, the same answer is a wall.

## The contract, in one line

**A message with a `## TL;DR` is spoken as that summary alone. A message without one is
read in full.**

That is the whole rule, and it gives you a deliberate lever:

| you want | you write |
|---|---|
| the gist, detail stays on screen | a normal answer ending in `## TL;DR` |
| every word of it out loud | an answer with no `## TL;DR` |
| nothing spoken | `/voice off` |

## The short lines between commands

These are the ones you hear most, and they are spoken exactly as written, with no screen to
explain them. So each has to carry its own meaning: **a whole sentence, present tense,
saying what is being done and why.**

The failure mode is writing captions — labels for whatever comes next. They read perfectly
on screen, because the command underneath supplies the meaning, and they are useless heard:

| caption — reads fine, sounds wrong | said aloud instead |
|---|---|
| "Now the fix itself." | "Now I'm writing the fix." |
| "First, the failing test —" | "Let me start by running the test that fails." |
| "The interesting part: the watcher." | "The interesting part is the watcher, so let me look at that." |
| "Next, the config." | "Now I'm updating the config so it points at the new folder." |

A caption has no verb. There is no intonation a voice can give it, so it lands as an
unfinished thought and the listener waits for a sentence that never arrives.

## Writing the TL;DR

Only the summary is spoken, so it has to stand completely alone.

- **No file names, paths, commands, flags or line numbers.** If a detail can only be acted
  on by looking at it, it belongs in the body.
- **Plain sentences, in order:** what changed, whether it works, what to do next.
- **Five sentences at most.** `maxChars` is a hard cut and it truncates mid-thought.
- **It is a summary, not a pointer.** "The details are above" is worth saying; "see the
  third bullet" is not — a listener has no third bullet.
- **Say the state of the voice itself when it changed.** Which voice is set, whether a
  restart is pending. That is the one thing the listener cannot see without stopping to
  read.

## What is stripped before speaking

You do not have to write around these; they are removed automatically. Fenced code blocks,
tables, horizontal rules, URLs, link targets (the text survives), heading marks, bullet and
number markers, emphasis. Backticked paths are dropped rather than spelled out, while short
identifiers survive with their underscores read as spaces. Em dashes become pauses, `×`
becomes "times", and emoji and box drawing are removed rather than handed to the model as
unpronounceable tokens.

Which means: **write code blocks freely.** They cost the listener nothing and the reader
gets the exact command.

## Telling Claude all this

**The installer does it.** All of the above is condensed into
[`speaking-notes.md`](../speaking-notes.md), and `install.ps1` writes it into
`~\.claude\CLAUDE.md` between `<!-- claude-voice -->` markers — the user-level file, which is
loaded at the start of every session in every project, so a new conversation knows it is
being listened to without being told.

This matters more than it sounds. A session that does not know it is heard writes for the
screen, and a screen answer read aloud is the wall this page exists to prevent. The contract
only works if both ends know about it.

Only the marked block is touched, so anything else in that file survives an install, and
deleting the block is the whole of turning it off. `-NoNote` skips it entirely.

The one line inside it that changes is the current voice: `voice_cli.py set` and the panel's
dropdown both rewrite it between `<!-- current-voice -->` markers, so a new session knows
which voice it is speaking as without running anything.

Keep any additions short. The voice supplies the character; the model does not need to be
told to act it, only to know it is being heard.

**One session never gets it: the one that installed it.** `CLAUDE.md` is read when a session
begins, so the conversation that ran the installer carries on unaware. Tell it directly, or
restart it.
