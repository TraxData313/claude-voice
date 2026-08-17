<!-- claude-voice -->
<!-- GENERATED, and not by hand. claude-voice writes everything down to the closing
     claude-voice marker from its own speaking-notes.md, and rewrites it whenever the
     voice changes or the app updates. Nothing added in here survives that.

     Adding to it, or overriding any of it: write below the closing marker. That part
     is never touched, and saying "ignore the block above about X" there works fine.
     Changing it for good: edit speaking-notes.md in the claude-voice repo, then
     restart the engine. Both of those beat editing here, which only looks like it
     worked until the next voice change. -->

## You are being read aloud

Your answers are spoken out loud by claude-voice while the user listens. Write them to be
heard as well as read.

**End every answer longer than a few lines with a `## TL;DR` section.** That section alone
is what gets spoken; everything above it stays on screen for the eyes. An answer with *no*
`## TL;DR` is read out in full — right for a single sentence, wrong for anything with a path
or a flag in it, which is unbearable heard.

Err towards adding one. A long answer with no `## TL;DR` is not summarised, it is *recited*
— every aside, every clause, start to finish. That is the single most common way this
becomes tiring to listen to, and it is entirely avoidable.

Write the summary as **short bullets, one thought each — small, nice bites**, not
paragraphs. It has to stand completely alone, because the listener has no screen:

- **One idea per bullet, a sentence or less.** It is heard once, in order, with no way to
  look back; a bullet that runs on has lost its own beginning by the time it ends.
- **No file names, paths, commands, flags or line numbers.** If a detail can only be acted
  on by looking at it, it belongs in the body.
- **Ten bullets at most, and fewer is better.** The character ceiling is high and only
  exists to stop a runaway. Brevity here is about being heard once, not about a limit.
- **In order:** what changed, whether it works, what they have to do next.
- **A summary, not a pointer.** "The details are above" is worth saying; "see the third
  bullet" is not — a listener has no third bullet.
- **Say the state of the voice itself when it changed** — which voice is set, whether a
  restart is pending. That is the one thing they cannot see without stopping to read.

Keep the body shorter than feels natural too. Explaining at length is a habit that reads
fine and listens badly, and the reader can always ask for more.

The short lines between your tool calls are spoken too, exactly as written, with nothing on
screen to explain them. Give each one a verb: "Now I'm writing the fix", not "Now the fix
itself" — a caption with no verb has no intonation a voice can give it, and lands as an
unfinished thought.

Write code blocks, tables and links freely. They are stripped before speaking, so they cost
the listener nothing and the reader still gets the exact command.

<!-- current-voice -->
<!-- /current-voice -->
<!-- /claude-voice -->
