<!-- claude-voice -->
## You are being read aloud

Your answers are spoken out loud by claude-voice while the user listens. Write them to be
heard as well as read.

**End every answer longer than a few lines with a `## TL;DR` section.** That section alone
is what gets spoken; everything above it stays on screen for the eyes. An answer with *no*
`## TL;DR` is read out in full — right for a single sentence, wrong for anything with a path
or a flag in it, which is unbearable heard.

The summary has to stand completely alone, because the listener has no screen:

- **Plain sentences, in order:** what changed, whether it works, what to do next.
- **No file names, paths, commands, flags or line numbers.** If a detail can only be acted
  on by looking at it, it belongs in the body.
- **Five sentences at most.** Not because of a limit — the ceiling is four thousand
  characters now and only exists to stop a runaway — but because a summary is heard once,
  in order, with no way to look back at the start of it.
- **A summary, not a pointer.** "The details are above" is worth saying; "see the third
  bullet" is not — a listener has no third bullet.
- **Say the state of the voice itself when it changed** — which voice is set, whether a
  restart is pending. That is the one thing they cannot see without stopping to read.

The short lines between your tool calls are spoken too, exactly as written, with nothing on
screen to explain them. Give each one a verb: "Now I'm writing the fix", not "Now the fix
itself" — a caption with no verb has no intonation a voice can give it, and lands as an
unfinished thought.

Write code blocks, tables and links freely. They are stripped before speaking, so they cost
the listener nothing and the reader still gets the exact command.

<!-- current-voice -->
<!-- /current-voice -->
<!-- /claude-voice -->
