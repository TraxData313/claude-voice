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

Only the summary is spoken, so it has to stand completely alone. Write it as **short
bullets, one thought each** — small, nice bites, not paragraphs.

- **One idea per bullet, a sentence or less.** It is heard once, in order, with no way to
  look back; a bullet that runs on has lost its own beginning by the time it ends.
- **No file names, paths, commands, flags or line numbers.** If a detail can only be acted
  on by looking at it, it belongs in the body.
- **Ten bullets at most, and fewer is better.** `maxChars` used to enforce brevity at 600
  characters and it was a bad way to learn it: the cut landed on a full stop, so the summary
  sounded finished and simply was not. It is 4000 now and it says when it fires — a stop on
  a runaway, not a length to write up to.
- **In order:** what changed, whether it works, what they have to do next.
- **It is a summary, not a pointer.** "The details are above" is worth saying; "see the
  third bullet" is not — a listener has no third bullet.
- **Say the state of the voice itself when it changed.** Which voice is set, whether a
  restart is pending. That is the one thing the listener cannot see without stopping to
  read.

And err towards writing one at all. A long answer with *no* `## TL;DR` is not summarised, it
is recited — every aside, every clause, start to finish. That is the single most common way
this becomes tiring to listen to, and it is entirely avoidable.

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

**And it keeps itself up to date.** `voice_lib.sync_notes()` writes the block again from
`speaking-notes.md` on every engine start and after `/voice update --apply`, so changing how
Claude is told to write reaches everyone on the next restart rather than waiting for them to
re-run an installer by hand. That mattered more than the other installed files: a stale
slash command breaks visibly, while a session quietly following last month's rules looks
exactly like a session.

It never *creates* the block — no markers, no edit — so deleting them still turns the note
off for good. The flip side is that edits between the markers do not survive an update.

So the block says so itself, in an HTML comment at the top: generated, rewritten on every
voice change, put additions and overrides *below* the closing marker where they survive.
That warning is aimed less at you than at Claude. `CLAUDE.md` is exactly the file a session
writes to when it runs `/init` or is told to remember something, and it has no other way to
know that one stretch of it is owned by an app. Silently eating a note somebody asked to be
kept is a worse failure than any of this being slightly out of date.

The one part inside it that changes is the current voice: `voice_cli.py set` and the panel's
dropdown both rewrite it between `<!-- current-voice -->` markers, so a new session knows
which voice it is speaking as without running anything.

## Telling it whose voice it is

That block says more than a name, deliberately. A session told only *"the voice is Abby"*
still writes as though handing finished text to a component further down the line. One told
the answers are **said** in that voice, to somebody listening rather than reading, has a
reason to write differently — which is the point of everything above it. Rules describe the
output; knowing what is happening produces it.

**Playing the part a little is the default.** The voice is the face the user actually meets;
it would be odd for the one wearing it to be the only one who did not know. So the block
says two things follow from being narrated — the words are *heard* rather than read, and
they are heard *as somebody* — and invites the session to play along without overdoing it.
It ends by saying that none of it is a rule and that plain Claude is a fine answer if that
is what the user wants. That is the point: given the situation rather than an instruction, a
session can judge it.

**The manner comes from the persona string, not from this page.** The block says "a calm
voice can be calming, a bright one can be bright" and points at the description above it,
because the whole premise is that you will add voices nobody here anticipated. A fixed list
of adjectives would only ever fit the two that shipped.

This also fixes an ambiguity the old one-liner left open. The description sat next to *"that
is you"* with nothing saying whether it described the timbre or the writer, so sessions
resolved it differently from one day to the next — which is worse than either answer.

**What keeps it safe is scope, not restraint.** It belongs to the `## TL;DR` and the short
lines between tool calls, and nowhere near the body of an answer. Personas describe a
*voice*, not a manner: Abby's reads "cute, slightly nerdy American girl", which is mild
enough to hide the problem, and Max's reads "brave, driving, motivating" — taken for a
writing instruction, that delivers a stack trace as a pep talk.

Voices with no persona get the identity paragraphs and nothing else. Told to let a character
through with none described, a session invents one.

If you write a persona for a new voice, write it as a description of how the voice *sounds*.
The `Note` field in `voice.json` is free and unread if you want somewhere longer to think.

**One session never gets it: the one that installed it.** `CLAUDE.md` is read when a session
begins, so the conversation that ran the installer carries on unaware. Tell it directly, or
restart it.

## A mood, and a laugh, when the engine has them

Two of the engines do more than read the words. Qwen and Breeze take a **mood**, meaning
how a whole line is said, and Breeze also makes **sounds** written into the text: a laugh,
a sigh. A session has no field beside its words to ask for either, so it writes them into
the words, the way a script writes stage directions:

```
## TL;DR
(excited)
- It works, all of it. (laugh) Even the part I was sure would not.
```

| | written as | where | what happens |
|---|---|---|---|
| **a mood** | `(whisper)`, `(sad)`, `(excited)`, one of eleven | first thing in the TL;DR, or at the front of a short line | sets how the whole message is said, and is never read out |
| **a sound** | `(laugh)` `(sigh)` `(cough)` `(clears throat)` | exactly where it happens | Breeze makes it; the others leave it out |

- **One mood per message, and the first one wins.** An engine follows one instruction for a
  whole generation, so a second could only ever be ignored. Every mood in brackets comes out
  of the speech either way, so an engine that takes none is never handed the word *whisper*
  to read instead.
- **Only a bracket with nothing but the mood in it counts.** `(sad, I know)` is an aside and
  stays one. Asterisks never carry a mood, because `*serious*` is emphasis far more often
  than it is a direction.
- **Sounds are forgiving, moods are not.** `(laughs softly)`, `[giggles]` and `*sighs*` all
  arrive as Breeze's own tags, because the model writing them has habits of its own. Between
  asterisks, only a span that is nothing but the sound counts, standing where a stage
  direction stands, so `*Laugh* tracks are gone` stays a sentence.
- **On screen it stays**, as a stage direction. That is the price of having no second
  channel, and it reads as what it is.

### A session is told only what will happen

None of this is any use until the session knows about it, and it is not `CLAUDE.md` that
tells it: that file is read once, and the engine can change in the middle of a
conversation. `speak_hook.py` answers two more events for this:

- **`SessionStart`**, for a new session, a resumed one, or one just compacted, gets a short
  paragraph saying whether the voice is on, whose voice it is, and what that engine will do
  with a mood or a sound.
- **`UserPromptSubmit`** runs on every prompt and says nothing, unless that has changed since
  the session was last told. Swap Qwen for Breeze mid-conversation and the next prompt
  carries the news.

The paragraph is built from what the engine can do rather than from its name, and it offers
nothing the engine will not perform. That rule came from the assistant app first. Its
voice's mood field only appears while an engine that uses it is loaded, because a field that
does nothing is worse than none: it gets written into, and believed. Telling a session it
may laugh on an engine that cannot is the same mistake.

`voice status` shows what sessions are told, and under *hooks* whether the hooks can run at
all. For their first five weeks they could not, and nothing said so: see
[the hooks](how-it-works.md#the-hooks).
