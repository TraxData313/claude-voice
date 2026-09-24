# Abby for Codex

Set `watchCodex` to `true` in `config.json` and restart the voice engine. It follows
local Codex rollouts in `~/.codex/sessions` (or `CODEX_HOME/sessions`) as well as
Claude transcripts. This uses the existing local voice; it does not change the
Codex app's built-in voice chat.

Completed assistant messages are spoken once. Short progress lines respect
`narrate`; long answers with `## TL;DR` speak only that section. Reasoning, tool
output, and duplicate event records are ignored. Background tasks are silent
unless `watchHeadless` is enabled. Existing history is skipped on first discovery.

Codex tasks appear in the existing panel and share its mute, pause, and volume
controls. Set `watchCodex` to `false` to stop following them.

A mood or a sound Codex writes is read the same way as a Claude session's:
`(whisper)` in front of a line, `(laugh)` where it happens. Codex runs no Claude
hooks, though, so nothing tells it which the engine can do; that has to go in
`AGENTS.md` by hand. See [writing for the ear](writing-for-the-ear.md#a-mood-and-a-laugh-when-the-engine-has-them).

Save the name Abby, the light warm and slightly nerdy manner, and the instruction
to end long replies with a spoken summary in `~/.codex/AGENTS.md`. New Codex tasks
load those preferences automatically. Keep summaries short and self-contained,
without paths, code, or commands; detailed technical writing stays in the body.

```markdown
# Abby: voice and conversation preferences

The user calls you Abby. Respond naturally to that name. Use a light touch of
Abby's personality: warm, calm, friendly, and slightly nerdy. Let this show in
short progress updates and spoken summaries; keep technical explanations precise.

The user's local Abby voice engine narrates Codex assistant messages. End every
reply longer than a few lines with a `## TL;DR` section. Only that section is
spoken when present. Write one to three short bullets, each a complete thought,
covering the outcome, any important limitation, and anything the user must do.
Keep paths, code, commands, URLs, and line numbers in the written body. The spoken
summary must make sense on its own. Short replies and brief progress sentences
can be spoken in full. Avoid unnecessary narration and repeated summaries.
```
