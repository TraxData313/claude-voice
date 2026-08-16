# The panel — build plan

**This page is addressed to Claude.** The user will open a fresh session and ask you to
build this. Everything you need is here; the conversation that decided it is not, so do
not guess beyond what is written. Where this plan and the code disagree, read the code —
`speak_server.py` and `voice_lib.py` are the ground truth.

## What is being built

A small always-on-top window — deliberately plain — that shows what the voice is doing
and lets the user steer it without typing commands:

```
┌─ claude-voice ────────────────────────────┐
│ ▶ "Now I'm checking whether the test..."  │   now playing + progress
│   from: NPC dialogue on event rolls       │
│  [ stop ]  [ play ]  [ skip ]             │
├───────────────────────────────────────────┤
│ queued (2)                                │
│  · Voice output with Sibylla TTS: "The…"  │
│  · NPC dialogue on event rolls: "Done…"   │
├───────────────────────────────────────────┤
│ history — click to replay                 │
│  · 12:04 NPC dialogue…  "It works. The…"  │
│  · 12:03 Voice output…  "Both of your…"   │
├───────────────────────────────────────────┤
│ sessions                                  │
│  [x] NPC dialogue on event rolls          │   checkbox = heard / muted
│  [ ] ImmersiveAI post wedding tasks       │
├───────────────────────────────────────────┤
│ voice: [ Abby ▾ ]           engine: ready │
└───────────────────────────────────────────┘
```

Decisions already made — do not reopen them:

- **Tkinter**, stdlib only. No pip installs, no web page, no admin rights anywhere.
- **Fake pause.** `stop` cuts the line; `play` re-speaks the current line from its start.
  True pause is out of scope — `winsound` cannot do it and we are not adding a dependency.
- **Per-session mute** lives in the panel (checkbox list of active sessions).
- **Voice switching** lives in the panel (dropdown of the catalog).
- Plain look is accepted. Function over beauty; it floats on top and stays out of the way.

## How it fits the existing design

One rule keeps this maintainable: **the panel owns no state.** It is a dumb view over the
engine's HTTP API, polling `/state` about twice a second. Everything it shows comes from
the server; everything it does is a POST. If the panel is closed nothing changes anywhere.

The engine process (`speak_server.py`) already holds almost everything needed: the current
job, the queues, the per-transcript session labels, and the spoken count. The work is
mostly *exposing* that, then drawing it.

## Stage 1 — keep the audio (do this first, it is useful alone)

Today each chunk's wav is deleted after playback. Instead, keep a ring of the last N
utterances so replay is instant and free — no re-synthesis.

- New config keys in `voice_lib.DEFAULTS`: `"historyKeep": 40`.
- In `Speaker`: give each Job an id and a `label` (session) + `text` field; after a chunk
  plays, move the file into `%TEMP%\claude-voice\history\<jobid>-<n>.wav` instead of
  unlinking. Trim the directory to `historyKeep` jobs, oldest first, on every add.
- Record per job: id, session label, first ~80 chars of text, timestamp, list of wav
  paths, voice id. Keep in a deque on the Speaker; this is what `/state` reports.

Acceptance: speak three lines, `ls` the history dir, replay one via the API below and
hear it without the engine synthesising anything (check the log — no `synth` lines).

## Stage 2 — the API

Extend `Handler.do_POST` with:

- `POST /state` → everything the panel draws, in one shot:
  ```json
  {
    "ready": true, "speaking": true,
    "current": {"id": 17, "text": "Now I'm...", "session": "NPC dialogue...", "voice": "abby"},
    "queue":   [{"id": 18, "text": "...", "session": "..."}],
    "history": [{"id": 16, "text": "...", "session": "...", "when": "12:04"}],
    "sessions": [{"path": "...jsonl", "label": "NPC dialogue...", "muted": false}],
    "voices":  [{"id": "abby", "name": "Abby"}, ...],
    "voice": "abby", "enabled": true
  }
  ```
- `POST /skip` → cancel current job only; the queue continues. (Note: today `cancel()`
  drains everything — factor a narrower `skip_current()`.)
- `POST /play` → re-submit the current (or last) job's text from its start, barging.
  This is the fake pause's "play" half; `stop` is already there.
- `POST /replay-id {"id": 16}` → play a history entry's kept wavs directly (no synthesis).
- `POST /mute-session {"path": "...", "muted": true}` → maintained in a set on the
  watcher; persisted to config as `"mutedSessions": [...]` so it survives restarts. The
  watcher checks it in `_consider` before speaking anything from that transcript.
- `POST /set-voice {"voice": "max"}` → resolve, write config via `voice_lib.save_state`,
  and call `voice_lib.announce_voice` — same as `cmd_set` does. Do not duplicate that
  logic; move the body of `cmd_set` into `voice_lib` and call it from both places.

Every handler returns JSON and must not touch the engine thread directly — the JNI engine
belongs to its own thread and everything else only enqueues. That rule is absolute.

## Stage 3 — the window

New file `panel.py`, stdlib only (`tkinter`, `urllib.request`, `threading`).

- Poll `/state` every 500 ms **from a worker thread**; hand results to the UI thread via
  `widget.after()`. Never call urllib from the Tk mainloop (it freezes the window) and
  never touch Tk from the worker (Tk is single-threaded — same class of rule as the JNI
  thread, and it bites the same way).
- `wm_attributes("-topmost", 1)`, small default size, remember position in
  `config.json` (`"panelGeometry": "..."`).
- If the engine is down, show one line — "engine not running — [start]" — where the
  start button runs `voice_lib.start_server`. The panel must be useful to open *before*
  the engine is up.
- Add `panel` command to `voice_cli.py` (spawn `panel.py` detached, same pattern as
  `start_server`), and a `--panel` flag on `on`. Update `HELP` — `docs/commands.md`
  regenerates from it.

## Traps already paid for — respect them

- **BOM:** anything that writes JSON the server or Claude Code reads must write UTF-8
  *without* BOM. `install.ps1` has `Write-Utf8` for a reason.
- **Thread names:** do not name any method `_handle` on a `threading.Thread` subclass
  (CPython 3.13 shadows it with an instance attribute).
- **Queue, don't barge:** ordinary lines queue; only explicit user actions (play, replay,
  say) interrupt. The panel's buttons are explicit user actions.
- **Dedupe is on the words**, before any session label is prefixed. Replaying from
  history must bypass `already_spoken` (it is deliberate repetition).
- **Two engines double-speak:** the port guard exists; do not weaken it while adding
  endpoints.

## Build order, with a check after each

1. Stage 1, verify with the log.
2. `/state` + `/skip` + `/play`, verify with `curl` before any UI exists.
3. Minimal window: now-playing + stop/play/skip. Use it for a while.
4. Queue + history + replay-by-click.
5. Session list + mute. Verify a muted session stays silent *and* still logs
   `watcher: muted` so silence is diagnosable — silence with no trace is the one failure
   this project keeps re-learning to avoid.
6. Voice dropdown. Verify it updates `~\.claude\CLAUDE.md` between the markers (that is
   `announce_voice` doing its job).

## Icons

Round PNGs are ready and committed, one set per shipped voice:

```
docs/icons/<voice-id>-<size>.png      size ∈ {256, 96, 48, 24}
```

So `docs/icons/abby-48.png`, `docs/icons/max-24.png`, and so on. Transparent background,
rimmed in the voice's colour — teal for Abby, amber for Max.

**Load them straight into Tk.** `tkinter.PhotoImage(file=...)` reads PNG on Tk 8.6, which
is what ships with Python 3.9+. No Pillow, no rasterising, no new dependency:

```python
img = tk.PhotoImage(file=os.path.join(voice_lib.ROOT, "docs", "icons", f"{vid}-48.png"))
label.configure(image=img)
label.image = img      # keep a reference or Tk garbage-collects it and shows nothing
```

Use **48** beside "now playing" and **24** in the session and history rows. Keep a dict of
loaded images rather than re-reading per poll.

Most voices have no icon — the ninety-odd trained ones certainly do not. Check the file
exists and fall back to a coloured circle with the first letter. Do not let a missing icon
raise.

New voices get icons from `docs/icons/make_icons.py`, which is an **authoring** tool: it
needs Pillow, is run once by hand, and its output is committed. Nothing at speaking time
imports it.

Half a day, roughly. If something here turns out wrong in practice, prefer the smaller
change and note it in this file — the next session reads this too.
