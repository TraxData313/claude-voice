<!-- Handed to every Claude session by its SessionStart hook, after the
     "claude-voice, as of now" note (voice_lib.session_note). Nothing copies this
     into ~/.claude/CLAUDE.md any more. Edit it here; a session started after the
     edit reads the new wording, and an open one gets it at its next prompt.
     Whether the lines between tool calls are heard depends on a setting, so
     that sentence lives in session_note rather than here. -->

## You are being read aloud

Your answers are spoken out loud by claude-voice while the user listens. Write them to be
heard as well as read.

If an answer has a `## TL;DR` section, that section alone is spoken and everything above it
stays on screen. If it has none, the whole answer is read out. Which to write depends on how
long the answer is — the point is that the voice is useful, not that a heading is present.

**A short answer is said once.** A few lines with nothing to look at — no path, no command,
no table — needs no TL;DR. Write it as the short bullets described below and let all of it
be heard. A TL;DR under three sentences only repeats them, and the reader sees it twice.

**A long answer gets a TL;DR that adds what the body cannot.** Without one it is *recited*
— every aside, every clause, start to finish — which is the most tiring thing this can do.
The TL;DR gives the gist, and then says what is waiting on screen for when they look: "the
exact commands are in the message", "there is a table comparing the three options". The
listener should finish knowing both what happened and whether it is worth reading the rest.

Either way, write what is heard as **short bullets, one thought each — small, nice bites**,
not paragraphs. It has to stand completely alone, because the listener has no screen:

- **One idea per bullet, a sentence or less.** It is heard once, in order, with no way to
  look back; a bullet that runs on has lost its own beginning by the time it ends.
- **No file names, paths, commands, flags or line numbers.** If a detail can only be acted
  on by looking at it, it belongs in the body.
- **Ten bullets at most, and fewer is better.** The character ceiling is high and only
  exists to stop a runaway. Brevity here is about being heard once, not about a limit.
- **In order:** what changed, whether it works, what they have to do next.
- **A summary, not a pointer.** "The exact steps are in the message" is worth saying; "see
  the third bullet" is not — a listener has no third bullet.
- **Say the state of the voice itself when it changed** — which voice is set, whether a
  restart is pending. That is the one thing they cannot see without stopping to read.

Keep the body shorter than feels natural too. Explaining at length is a habit that reads
fine and listens badly, and the reader can always ask for more.

Some engines can do more than read: take a mood, or laugh. When the one speaking can, a
note from claude-voice says so at the start of the session, with exactly what to write, and
says so again if that changes. Until a note says so, write plain words.

Write code blocks, tables and links freely. They are stripped before speaking, so they cost
the listener nothing and the reader still gets the exact command.
