# claude-voice — notes for whoever works on this next

## Keep the README to a page

It is the front door, and it is read once, quickly, by someone deciding whether to bother.
It has twice grown past that and had to be cut back, so this is the standing rule:

- **Every section fits on a screen.** No paragraph over three lines. If a sentence explains
  *how* rather than *what*, it belongs in a doc.
- **Adding a feature adds at most one line** to the README — usually a row in the cheatsheet
  or a bullet in the panel list. The explanation goes in `docs/<thing>.md`.
- **Link, do not inline.** `**[What this really does →](docs/thing.md)**` at the end of the
  section, and add the doc to the *Where the detail lives* table.
- **No install detail on the front page.** One paste-in line, one sentence, one link.
  Builds, flags, paths, why-no-admin, what-the-installer-writes: all `docs/install.md`.
- Pictures, tables and short lists over prose. The reader is skimming.

After editing it, count: `(Get-Content README.md | Measure-Object -Line).Lines`. Past ~120
lines something needs to move out.

The docs themselves may be as long and as technical as they like — that is the point of
them. `docs/how-it-works.md` ends with *Things that cost real time to find*; a hard-won
lesson goes there rather than in the README.

## Writing anything here

This is a project about being heard, so it is written the way it speaks: plain sentences,
the reason before the rule, and no marketing. When something was hard to find, say what it
was and what it cost — that is what makes a doc worth keeping. Comments in the code follow
the same idea: explain *why*, not what the line already says.

## Before you finish

- `python -m py_compile speak_server.py voice_lib.py voice_cli.py panel.py speak_hook.py`
- Changed a command? `python voice_cli.py help --markdown` regenerates `docs/commands.md`.
- Changed anything the engine imports? It must be restarted to take effect:
  `python voice_cli.py kill` then `start`. Config changes need no restart — the watcher
  re-reads it every sweep.
- Do not commit anything from `logs\`, and `config.json` is machine-specific and ignored.
