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

- `python -m py_compile speak_server.py voice_lib.py voice_cli.py panel.py speak_hook.py qwen_server.py qwen_torch_engine.py qwen_setup.py
  update_check.py installer.py`
- Changed the install? `python test_installer.py`. A real run must not touch the real install:
  `install.ps1 -Root <temp> -Product claude-voice-test -DataDir <temp> -ClaudeDir <temp>
  -ShortcutDir <temp> -Port 8799`, then the uninstall its Settings → Apps entry names. Bump
  `runtime.json` and `install.ps1` together (the test checks), and re-pin `requirements-pocket.txt` or `requirements-qwen.txt`
  only after seeing the new wheels load with Smart App Control on.
- Changed a command? `python voice_cli.py help --markdown` regenerates `docs/commands.md`.
- **Releasing?** Bump `version` and `date` in `version.json`, write its `headline` for the
  ear, and add the matching `## <version>` section to `CHANGELOG.md` — `update --apply`
  prints that section to whoever takes the release. Set `needsSetup` when the release
  changes `commands\`, `templates\` or the install scripts, since a `git pull` in a checkout does
  not reinstall those (an installed copy's update re-runs its installer by itself). Nobody hears about a release that does not bump that file.
- Changed anything the engine imports? It must be restarted to take effect:
  `python voice_cli.py kill` then `start`. Config changes need no restart — the engine
  re-reads it whenever the file changes.
- Do not commit anything from `logs\`, and `config.json` is machine-specific and ignored.
