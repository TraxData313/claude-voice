# Abby for Codex

Codex runs no Claude hooks, but it can run one program at the end of every turn: the
`notify` setting in `~/.codex/config.toml`. Point it at `speak_hook.py` and each finished
answer is handed to the voice engine, which speaks its `## TL;DR` (or the whole of a short
answer), exactly as it does for a Claude session. Nothing reads `~/.codex/sessions` any
more; that was the old `watchCodex` switch, gone with the transcript watcher.

```toml
notify = ["C:/Users/<you>/AppData/Local/Programs/claude-voice/runtime/python.exe",
          "C:/Users/<you>/AppData/Local/Programs/claude-voice/app/speak_hook.py"]
```

That is an installed copy's own Python and hook; for a git checkout, your Python and the
checkout's `speak_hook.py`. Forward slashes, so nothing reads a backslash as an escape.

Codex keeps **one** notify program. If the Codex app already put its own there (its
computer-use helper does), keep it by putting it after `--then`. It is started with the
same argument it always got, and not waited for:

```toml
notify = ["C:/Users/<you>/AppData/Local/Programs/claude-voice/runtime/python.exe",
          "C:/Users/<you>/AppData/Local/Programs/claude-voice/app/speak_hook.py",
          "--then", "C:/.../codex-computer-use.exe", "turn-ended"]
```

What you hear: the end of each turn. Codex's progress lines between tool calls are not in
what notify is handed, so they are not spoken. Codex tasks appear in the panel under their
first request and share its mute, pause and volume controls. This uses the local voice; it
does not change the Codex app's built-in voice chat.

A mood or a sound Codex writes is read the same way as a Claude session's: `(whisper)` in
front of a line, `(laugh)` where it happens. Nothing tells Codex which the engine can do, so
that has to go in `AGENTS.md` by hand. See
[writing for the ear](writing-for-the-ear.md#a-mood-and-a-laugh-when-the-engine-has-them).

Save the name Abby, the light warm and slightly nerdy manner, and the instruction to end
long replies with a spoken summary in `~/.codex/AGENTS.md`. New Codex tasks load those
preferences automatically. Keep summaries short and self-contained, without paths, code, or
commands; detailed technical writing stays in the body.

```markdown
# Abby: voice and conversation preferences

The user calls you Abby. Respond naturally to that name. Use a light touch of
Abby's personality: warm, calm, friendly, and slightly nerdy. Let this show in
the spoken summaries; keep technical explanations precise.

The user's local Abby voice engine speaks the end of each Codex turn. End every
reply longer than a few lines with a `## TL;DR` section. Only that section is
spoken when present. Write one to three short bullets, each a complete thought,
covering the outcome, any important limitation, and anything the user must do.
Keep paths, code, commands, URLs, and line numbers in the written body. The spoken
summary must make sense on its own. Short replies are spoken in full.
```
