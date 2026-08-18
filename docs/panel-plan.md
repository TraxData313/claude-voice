# The panel — build plan

> **Built.** `panel.py` and the API behind it exist. The plan is kept as written because it
> still describes the shape of the thing; what actually happened — including the one
> assumption that turned out to be wrong — is at the bottom, under *What changed in the
> building*. Read that before changing anything here.

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

## What changed in the building

Everything above was built as described. These are worth knowing before touching it.

**`stop` did not cut the line, and now does.** The plan assumed stopping already worked and
only `play` had to be invented. It did not: the player called `winsound.PlaySound` *synchronously*,
and a synchronous PlaySound cannot be interrupted at all. A purge from another thread does
not cut it — measured, it queues up behind it and returns when the clip ends of its own
accord, up to half a minute later. So `stop` had always meant "drop the queue and wait out
this sentence", quietly, and `/voice stop` mid-sentence would time out and print *"Engine
not running."* at you, which is the worst possible thing for it to say. Playback is now
asynchronous, with the wait driven by the wav's own header and broken by cancellation; a
purge then cuts within about a twentieth of a second. Measured before and after: 5.8s, then
0.12s. Do not put the synchronous call back — the fake pause depends on this.

**The play queue carries a third field.** Items are `(job, path, keep)`. A replay plays
history's own files, and those must not be deleted when the queue is drained; anything
draining that queue has to respect the flag.

**Replaying and skipping needed different verbs.** `cancel()` empties everything and is
what `stop` means; `skip_current()` cancels the current job only and hands the play queue
back its other items. They are easy to confuse — `skip` that drains the queue is a bug the
user will read as lines going missing.

**The lists are Treeviews, not Listboxes.** A Tk Listbox holds text and nothing else, so no
row of one can carry a picture. That decided it once the icons arrived, and it also gave
the rows real columns instead of padding every line to a fixed width. Two consequences
worth knowing: each row is *named* after its utterance id, so a click reads the id straight
off the row with no lookup table; and the `#0` column has to be wider than the picture,
because Treeview adds its own indent in front of it and the time beside it gets sat on
otherwise.

**Tick boxes are the classic Tk widget, not ttk's.** ttk's is drawn by the theme, and clam —
the only theme that accepts colour instructions at all — draws its *ticked* state as a black
cross. Under a heading that says "ticked means heard", that reads as precisely the opposite
of what it means. The old plain widget draws a real tick and takes its colours directly.

**Things the sketch did not have**, added on request while it was being built: *on top* and
*dark* tick boxes (`panelTopmost`, `panelDark`); the portrait beside the now-playing line
with the voice's name under it, clickable to swap between the two shipped voices, and
resizable from a box that takes a preset or a typed number (`panelFace`); 24px portraits on
every row; the session rows named *project · conversation*, because two sessions in
different repos can carry near enough the same title; and a credit line at the very bottom,
packed first among the bottom-up widgets so that it is the last thing a shrinking window
gives up.

**Two things about scaling the portrait.** Tk scales by whole numbers only — `zoom`
multiplies, `subsample` divides — so an arbitrary size is reached by doing both, and 160
comes from the 256px file as five up and eight down. And the rule for *which* file to
start from is quality, not arithmetic: a file drawn at that exact size wins, then coming
down from a larger one, then going up from a smaller one. Picking the cheapest sum instead
drew 192 by doubling the 96px file, and it looked precisely like a doubled 96px file.

**Dark mode is a change of ttk theme, not just colours.** The native Windows theme draws
real Windows widgets and ignores most colour it is given, so dark switches to `clam`, which
does as it is told. Two consequences: every style has to be set again on the way in, since
ttk keeps its settings per theme and not per widget; and clam's own maps have to be
overridden state by state, because its default hover made a white button under white text.

**The buttons are not the three in the sketch.** They were stop / play / skip; they are now
*turn off* / *stop* / *skip*. The master switch is the control actually reached for most,
and it needed the one route the plan said the engine deliberately did not have — `/enabled`,
which writes the setting and silences what is playing, exactly as `voice off` does. *Play*
went: clicking a line in the history says it again, from the audio it was heard as, which
covers nearly everything play was for. `POST /play` is still there for the one case a click
cannot cover — re-saying a line that was cut halfway, in full, since history holds only the
part that was actually heard.

**The buttons changed once more, and the names now carry the difference.** *Stop* and *skip*
never said what set them apart, which is the queue: they are *skip line* — give up on this
sentence, keep the ones behind it — and *skip all*, which empties the queue as well. Neither
is called stop, because neither pauses anything: there is no place kept to come back to.
*Skip line* is the everyday one, so it sits nearest the switch.

**Volume is Windows' own, not a gain applied to the samples.** `winsound` plays a wav at
whatever level it was written at, and scaling the samples would only reach the *next*
sentence — the one already sounding would carry on, and every wav kept for replay would be
stuck at the level it was written with. Windows keeps a volume per application, the slider
in the mixer under our own name, and setting that is instant, applies mid-sentence, and
applies to replayed audio too. Reaching it is `win_volume.py`: ctypes against the Core Audio
vtables, because there is no Python binding and no `pip install` in this project. Two things
to know — the vtable slot numbers *are* the contract, counted from 3 because 0 to 2 are
always IUnknown's; and not every non-zero HRESULT is a failure, which is why the return type
is declared `HRESULT` and ctypes is left to tell the difference.

**The panel drives it through the engine, never directly.** The mixer slider belongs to the
process making the noise, and that is the engine, not the window. So the slider POSTs
`/volume` and the engine both applies it and writes it down — the same shape as every other
control here. The panel echoes what `/state` reports, except while it is being dragged; and
an engine too old to mention volume at all is left alone rather than assumed to be at full,
which otherwise dragged the slider back to 100% the moment it was let go.

**Abby along the bottom.** A full picture of her, whoever is speaking — she is the face of
the thing rather than a readout of anything, and the window says whose voice it is in three
other places. She is shown *whole*: scaled to one of the sizes Tk can actually reach, never
stretched, clipped a little at the sides so she reaches both edges rather than floating in a
margin, and never cropped top or bottom. Two rules make her behave. She takes only the room
that is spare once every row has what it asked for — asking for a share of the window took
it from the lists instead, and the queue and history simply left the window. And the
window's own minimum reserves enough height for a picture worth looking at, so she always
has somewhere to be. `docs/art/abby-384.png` and `-640.png` are the sources; two widths,
because whole-number scaling from those two reaches roughly every 40 pixels across the range
a panel is ever that wide.

**The controls moved to the top, and the bottom became about her.** The voice dropdown sits
directly under the portrait, where the name used to be printed — the dropdown says the name
anyway, and that is already where you click to change voice — with the size box beside it,
which is the only place that box has ever explained what it sizes. That empties the old foot
row: the engine's state joined the credit line, and the bottom of the window is now the
picture and who made the thing, with nothing in it to operate.

**And then one row went back to the bottom — the version row.** The rule above says the foot
of the window has nothing in it to operate, and this is the exception that keeps it: those
controls are about *the program*, not about what is being said. Turning a voice off, skipping
a line, changing who speaks — all of that stays at the top, where you look while it is
talking. Which version you have, and whether there is a newer one, is a thing you deal with
once and then forget for a month.

Left to right it reads as one sentence: **auto-check** (a tick box, the only control in the
window that can reach the network at all), the **button**, the **what's new** link, a line
saying how the last look went, and the version itself in the far corner. The tick box says
`auto-check` and not `auto-check for updates` because the longer label ate the room the line
beside it needs at the narrowest the window goes — and a row that ends in a version number
does not have to say what is being checked. The line itself is anchored west for the same
reason it exists: left to its own devices a label centres in the space it is given, and this
one drifted right until it was touching the version number.

- **One button, three stages.** `check now` until a check finds something, then
  `update to 1.4.2`, then `reopen panel`. Two buttons would have meant a permanently dead
  one, since you cannot update to a version nobody has looked for yet — and the third stage
  is there because the window is the one thing an update cannot put back for itself. The
  engine it can: it stops it and starts it again. This window is the process it was started
  as, so it offers to be replaced instead of telling you to go and do it.
- **Ticking the box looks straight away**, as well as weekly. Somebody who has just asked for
  update checks should watch one happen rather than wait a week to learn whether it works.
- **The link appears with the button, not after it.** A version number is not a reason to
  take an update; the changelog is. It has to be readable *before* the decision, so it sits
  between the button and the outcome.
- **The empty half of the row earns its place**: with nothing else to say it reports
  `up to date, 2 days ago` — which answers the only two questions that row exists for.
- **Nothing here touches the network from the Tk thread.** The check and the pull run on
  threads of their own and post their answers to a queue that the existing drain timer picks
  up, exactly as engine state already does. A pull also restarts the engine and waits on a
  model load, which is a minute of frozen window if it is done in the wrong place.
- **The panel never checks by itself.** It reads the answer of whatever check last ran, off
  disk, by watching one small file's timestamp. Everything it knows about updates arrived
  because somebody asked for it.

**A box to type a line into.** `read custom text`, on the queue's own heading. Everything else
in this window steers what Claude is already saying; this is the one control that puts words
in that came from nowhere but here. It sits on that row rather than up with the transport
buttons because those three are about the line being spoken, and this one is about the ones
waiting behind it.

- **It queues, it does not barge in.** `/voice say` means *say this now* and cuts off
  whatever is playing. Typing a line here is asking for it to be read, which is no reason to
  throw away what is already in the queue — so `/speak` grew a `queue` flag, and the panel is
  the only thing that sets it. Every other caller behaves exactly as it did.
- **It is filed under `manual input`.** The panel draws a column of project names, and every
  other line in the queue came from a folder Claude was working in. An empty cell there says
  nothing; this says where it really came from. `/speak` now passes `project` through to the
  job for the same reason it already passed `session`.
- **It speaks even when the voice is turned off**, like `/voice say` and for the same reason:
  you asked for this line, specifically, just now. The master switch is about Claude talking
  unprompted.
- **The window forgets the words the moment they go.** The panel owns no state, and this
  comes closest to breaking that rule without actually doing it — the text is one POST, and
  the engine keeps it exactly as it keeps a line a hook sent.
- **The box is a `tk.Text`, so it takes no ttk styling.** Background, words and caret are
  three separate colours, and left alone in dark theme the caret is black on black — a box
  that looks like it is ignoring everything you type. Its grey hint label joins the list the
  theme repaints, so closing the window has to take it back out again: a destroyed widget
  left in that list breaks the *next* dark switch, halfway through, leaving the rest of the
  panel in the wrong colours.
- **Ctrl+Enter sends it**, because Enter has to keep making line breaks in a box that takes
  as many lines as you like. The handler returns `"break"` or Tk helpfully does both.
- **The label names both halves of what it does.** It read `say something` first, which was
  cheerful and said nothing: a button sitting on a queue could as easily have meant *say the
  next one*. `read custom text` says what goes in and what happens to it, which is the only
  thing a person who has never seen this window needs from it.

**The master switch wears its state: green working, red silent.** The label already said what
pressing it would *do*; nothing said what was happening *now* without reading the words on it
and working out which way round they were. So the colour is the state and the label is the
action, which means a green button reads `turn off` — and that is the right way round. The
alternative is either a button that does not say what it does or a colour that does not say
how things are.

- **It had to stop being a `ttk.Button` to be coloured at all.** The native Windows theme
  draws a real Windows button and ignores the background you ask it for; only `clam` obeys,
  and `clam` is the dark theme. A ttk button would have been green in dark and grey in light.
  The plain `tk.Button` is drawn by Tk in both — the same reason the tick boxes are plain
  ones. Flat and borderless, because a 3D grey frame around a coloured face looks broken.
- **It is packed `fill="y"`.** Matching the themed buttons beside it by choosing a padding
  means choosing two different numbers, one per theme, and being wrong in one of them — in
  light it sat four pixels short. Filling the row takes the height the tallest button in it
  already asked for, in either theme, without a number being picked at all.
- **No engine means grey, not red.** The voice is indeed not working, but the switch is not
  the reason and cannot know what it would be if there were one; the row is greyed out
  anyway, and `engine: down` in the corner is the answer to what is actually wrong.
- **It is out of the `transport` list** that greys the row, because that list is walked with
  `state([...])` — a ttk method a plain Tk button does not have. It takes `state` as an
  option instead, so it is switched by hand in the same two places.

