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

**Then the engine got a switch of the same kind, and took the leftmost place.** It replaced
`start engine`, which only ever appeared when there was nothing running — so the window could
start an engine and never stop one, and handing its three and a half gigabytes back meant
opening a terminal, which is the one thing this window exists to avoid. Making the two
switches by the same hand is what turned that code into `_switch`; there is no third caller
and there does not need to be, but two identical blocks of tk.Button arguments is a worse
place to fix a colour than one.

- **The row now reads biggest to smallest, left to right.** Loading a model is a minute and
  three gigabytes. Turning the voice off is instant and costs nothing. Skipping a line is
  about the one sentence in the air right now. That is the order they sit in.
- **Unloading turns the voice off as well, and has to.** The hook loads an engine again the
  moment Claude says anything, so unloading with the voice still on frees three gigabytes for
  roughly five seconds. `voice kill` has exactly the same trap and exactly the same answer —
  off first, then kill. The off is written straight to the config rather than posted, because
  the two have to happen in that order and the second one is what stops the engine answering.
- **Grey earns a third meaning here: not yet.** The voice switch is grey when there is no
  engine to ask; the engine switch is grey, reading `starting…`, for the minute a first load
  takes. Red is a state something has settled into, and neither of those has settled.
- **A reply beats a countdown.** Pressing it holds the starting state for 25 seconds so the
  window does not flash *engine not running* mid-load — but the moment `/state` answers, the
  hold is dropped rather than waited out. The timer is only there for the case where nothing
  ever answers.
- **It cost the window 46 pixels of minimum width**, 420 to 466 — and then gave back more
  than it took. Four buttons and two tick boxes do not fit in 420, so the tick boxes left
  that row entirely (below), and four buttons alone want 352. That number was never a taste;
  it is what the row measures, and it had to be measured again.

**A strip along the very top, the way an ordinary window has one.** `dark` and `on top` live
there now. Neither is about what is being said — they are about the window itself — and the
left of that strip is deliberately empty, because that is where a File or a Settings would go
if this ever grows one.

- **It is a row of its own, and that is not tidiness.** Tried in the corner of the header
  first, side by side: the tick boxes and the line being spoken then competed for the same
  pixels, and in a narrow window the line lost — *engine not running* came out as
  *engine not ru*. Tried them stacked, which halves their width to 57 and still lost. A row
  of its own is the only arrangement where neither has to give, and it costs about twenty
  pixels of height.
- **The number that matters is 352.** Measured, not chosen: Tk asks 332 for the button row in
  dark and 320 in light, plus 16 of padding. The window can now be narrower than it has ever
  been, having been 420 for most of its life. Below about 396 the update note in the footer
  starts to truncate, which is exactly what that label is anchored west and cut to 30
  characters for — and it is the only thing in the window that gives.

**The transport row became icons, and needed hover text the same day.** A cog for the
engine, a square or a triangle for the voice, `⏩` for one line and `⏭` for all of them —
they differ by the bar at the end, which is the difference itself: one more, or straight to
where there is nothing left. The button on the queue's heading is a `+`, because that is what
a button which adds one to a list looks like everywhere. It cost the row 173 pixels: 332 as
words, 159 as icons.

- **Name the font, or Windows picks the wrong one.** Left alone, Tk draws U+23F8 and its
  neighbours out of Segoe UI Emoji — little boxed colour pictures, which on a green button
  look like clip art stuck to it. Named as `Segoe UI Symbol` they come out as plain
  monochrome shapes that take the button's own foreground colour like any letter would.
  A missing font is not an error in Tk, it is a silent substitution, so the family is checked
  for rather than assumed and the buttons fall back to their old words if it is gone.
- **One glyph had to come from somewhere else.** U+2699, the cog, renders in Segoe UI Symbol
  as a small ring with a dot in it — at button size that reads as a record button, not a cog.
  Windows 10 and 11 ship `Segoe MDL2 Assets` with a proper one at `\ue713`, so the engine
  button borrows that and nothing else does. Private use codepoints are font-specific by
  definition, which is why there is a fallback behind it.
- **An icon nobody can name is a puzzle, not a control.** Tk has no tooltip, so `Tip` is a
  borderless `Toplevel` with a label in it, shown on a 450ms timer — without the delay,
  crossing the row on the way somewhere else flashes four of them. It takes its words as a
  *callable* where the button has more than one state, because a tooltip confidently saying
  the wrong one of two things is worse than no tooltip at all.
- **It sits below the button, never over it.** Whatever you are pointing at stays visible, and
  it is pulled left if it would otherwise run off the screen.

**She is drawn before anything has been asked.** The panel opened on an empty circle and an
empty dropdown until an engine answered, which says less than the truth: which voice is set is
in the config, the catalogue is a directory listing, and the portraits are files on disk. All
three are knowable with nothing running. `show_saved_voice` reads them once at startup and
hands them to the same `render_voices` the engine's reply goes through, so there is one code
path and not two. The first poll that gets an answer replaces it — that one can say who is
*speaking*, rather than who would.


**`auto start`: the window is a thing you open in order to be spoken to.** Most of the times
it gets opened at all, the next two clicks were going to be the cog and the switch beside it,
so a third tick box in the top strip does both as it opens. `panelAutostart`, off by default
and off for anyone who does not tick it — opening a window should not quietly take three and
a half gigabytes.

- **It waits for the first poll rather than firing at startup.** Whether an engine is already
  up is the whole difference between loading one and doing nothing, and nothing in the window
  knows that until `/state` answers. Starting a second one would do no harm — `start_server`
  checks the port before it launches anything — but the cog would sit there saying *loading
  the model* for a minute over a model that was already loaded, and being right about that is
  what the window is for.
- **The voice is turned on by writing the config, not by posting to the engine.** At that
  moment there may be no engine to post to: the one being started a line earlier is still
  loading, and the config is what it reads as it comes up. An engine already running hears it
  just as quickly, because the watcher re-reads that file every sweep. It is the same write
  `/enabled` makes, which is why the two cannot disagree.
- **Two words cannot say which engine or how much of it**, so the tick box has hover text like
  the buttons do. It is also the only thing in that strip that is not about the window itself,
  which is why it sits furthest from the two that are.

**The two coloured switches stopped guessing their own size.** They are `tk.Button`s because
ttk's cannot be green, and asking a `tk.Button` and a `ttk.Button` for the same three
characters does not give you the same shape: measured, the skip button wanted 37 by 29 and the
coloured pair 25 by 35 and 31 by 35 — narrower, taller, and not even matching each other. A
row of four buttons where two are a different size reads as a mistake, because it is one.

- **Measured, not guessed.** Most of that difference is the theme's own border and padding,
  which is not a number we are told. So `_match_switches` reads `winfo_reqwidth` off the skip
  button and hands it to the other two — and does it again on every theme switch, because the
  themes do not agree either: 37 by 29 in `clam`, 39 by 31 in the native one.
- **The transparent pixel is what makes it possible.** A Tk button showing an image takes its
  width and height in screen pixels; one showing only text takes them in characters of its
  font. So both wear a 1x1 transparent image, kept alive on the Panel because Tk silently
  draws nothing for an image nobody holds a reference to. Their own padding goes to zero at
  the same time — the box is now the size asked for, not that size plus a margin.
- **It cannot be done from `_build`.** Measuring means letting Tk settle the layout, and
  settling it half way through building the window delivers a `<Configure>` to a window whose
  widgets do not all exist yet — a stack trace per event, into `logs/panel.log`. `apply_theme`
  runs immediately after `_build` and again on every switch, so it is the one call site.
- **The window's minimum width did not move**: 476 before, 476 after. The strip grew from 110
  to 190 and the button row from 146 to 164, and neither of those is the row that sets it.
Everything from here down was planned in one sitting and built in one: a settings window, a
chip on the engine button, starting with Windows, and a memory readout. The plan itself is
kept at [settings-window-plan.md](settings-window-plan.md), with a note at the top of what it
turned out to be wrong about and which part of it was dropped.

**The tick boxes went behind a cog, and the strip has one thing in it.** Three of them had
gathered along the top, and each had to introduce itself in two words. *on top* is about the
window; *auto start* is about three and a half gigabytes of model; nothing on screen said
which was which, and there was no room for anything to. The strip was also the place a
Settings would go if this window ever grew one, so it grew one, and the four settings moved
in behind it where each of them gets a sentence.

- **The description is the whole point.** A hover text had been standing in for the
  explanation, and hover text can only be found by resting on a control you have already
  decided not to press. A line of grey under the tick is read by whoever opened the dialog to
  decide.
- **A cog on the settings button, so a chip on the engine.** The cog had been the engine's,
  which was always a compromise -- it said "something machinery" and nothing more precise.
  Now the cog means what a cog means everywhere, and the engine gets `U+E950`, the chip:
  what that button loads and hands back *is* a model. Drawn against a power symbol, a bolt
  and a robot, and picked by eye.
- **The chip does not want the point the cog needed.** The cog was drawn a point larger than
  the media glyphs because Segoe MDL2 draws it small inside its own box, and at twelve it
  read as the runt of a row of four. The chip is drawn nearly to the edges of that box, so
  the same bump made it heavier than the square and the arrows beside it. Rendered at twelve,
  thirteen and fourteen and looked at; twelve.
- **The dialog is the typer's shape, because the typer had already paid for it.** One at a
  time, Esc closes, opens over the panel rather than wherever the window manager fancied, and
  floats with the panel -- which is usually on top of everything, so a window it opened and
  then covered over would be a strange thing to have been handed. It also has the typer's one
  piece of housekeeping: its grey labels join the list the theme repaints, so closing it has
  to take them back out, or the next dark switch breaks half way through and leaves the rest
  of the panel in the wrong colours.
- **Unticking *on top* now reaches the dialog you unticked it in.** It did not, at first, and
  a settings window still glued over everything immediately after reads as the tick not
  having worked.
- **The window's minimum width did not move: 348 before, 348 after.** It was expected to
  drop, since the strip fell from 190 to 33 -- one button where three tick boxes were. It
  turns out the strip was never what set it. The header asks 254, and 348 is a hand-measured
  number for the longest thing the line above the words ever says. Measured again rather than
  assumed, which is the only reason that is known.

**The update controls went into the dialog, and the version number became the beacon.** The
tick and the button moved; the *report* stayed in the footer -- what is in an update, how the
last look went, and which version this is. Controls behind a cog, findings in plain sight.

- **`auto-check for updates` can say what it means again.** In the footer it had to be
  `auto-check`, because the longer label ate the room the line beside it needed. In a dialog
  there is room, and the compromise can be given back.
- **The version turns the link colour when there is something to take.** With the button
  behind a cog, nothing on the face of the window would otherwise say an update exists -- and
  that label was already the clickable way to the changelog, so the link colour is exactly
  what it should turn to say so. Grey the rest of the time.
- **A button that may not be on screen when its answer arrives.** A check runs on a thread,
  and the dialog can be closed before it comes back. Rather than guard every `configure` with
  a `winfo_exists`, what the button *would* say is kept on the panel: `say_on_button` writes
  it down and only then tries the widget. So a dialog opened later opens already saying it,
  and one opened while a check is still running finds the button greyed out and reading
  *checking…*, which is where it left off. Tested by stubbing a three-second check, pressing
  it, and closing the dialog underneath it.

**`start with Windows`, and it has no setting behind it.** Windows opens whatever is in the
Startup folder, so the folder *is* the setting. A config key beside it would be a second
opinion capable of disagreeing with the only one that matters.

- **The box is read back off the folder, never left where the click put it.** A shortcut that
  could not be written leaves the tick unticked, which is the truth, rather than ticked, which
  would be a promise. Tested by pointing it at a drive that does not exist.
- **It writes the same shortcut the installer does**, through the same `WScript.Shell` COM
  object `make_shortcut.ps1` uses -- pythonw so logging in does not also open a console
  behind the panel, the repo as the working directory, and `abby.ico` so the login entry and
  the Desktop icon are one thing in two places.
- **The values go in through the environment, not the command line.** A repo path with a
  space in it is the obvious way this breaks, and quoting PowerShell inside Python inside a
  shell is three chances to get it wrong.
- **A quarter of a second, measured, so it is done on the Tk thread.** A tick box that waits
  on a thread for its answer is a tick box that is briefly wrong, and this one is only ever
  pressed on purpose.
- **The two ticks compose, and that is the point of putting them in one section.** *start
  with Windows* opens the window at login; *auto start* then loads the engine and turns the
  voice on. Both ticked is the "it just talks" mode, and the descriptions say so.

**And the volume slider went up onto the button row, which the tick boxes had been in the way
of.** It had a row of its own for a good reason -- three buttons and two tick boxes filled the
row above, and a slider squeezed in beside them would have been a few pixels long. The tick
boxes went into the dialog, so the reason went with them.

- **The label and the percentage did not come with it.** There is one slider in this window;
  a word saying *volume* next to it is a caption on a thing that needs none. The number went
  into the hover text, which is where this window already puts the sort of detail a control
  cannot fit -- and it follows the handle while you drag, so nothing was actually lost.
- **Which meant teaching `Tip` two things.** It does not dismiss on a press for this one --
  pressing a slider is the *start* of using it, not the end of wondering what it is -- and it
  can be told to appear at once and to change what it is saying, rather than only fading in
  after 450ms. Ten lines, and the slider is the only caller.
- **The number is read off `drawn`, not off the widget.** While it is being dragged the handle
  is ahead of the engine by design, and `drawn` is what the drag writes and what the POST
  afterwards sends -- so the three cannot disagree. Reading the widget instead made it depend
  on Tk having stored the new value before it called the callback, which it does, but only by
  luck of ordering.
- **`fill="both"`, so it is the height of the buttons rather than a groove floating in the
  middle of their row.** clam draws the trough to whatever height it is handed, and it costs
  the row nothing because the slider still only *asks* for 16 -- the buttons go on setting how
  tall the row is. In light mode the native theme draws its own thin track and a fixed-size
  thumb whatever height it is given, and nothing in ttk will talk it out of that; the two
  themes already disagree about the size of the buttons.
- **23 pixels, measured** -- by building a replica of the old row, putting it back, and asking
  the window what its least height was with and without. The window's minimum width did not
  move: the button row asks 262 in dark and 270 in light, against the 332 it is given at 348.

**A memory readout was built here too, and then taken out again.** The footer was to say what
the engine was holding, with the split between RAM and the graphics card in its hover text. It
worked, and it was measured; Toni looked at it and did not want the number. Removing it put
`speak_server.py` back to exactly what it had been, which is the tidiest kind of removal there
is -- the panel's own change was five short pieces.

It is written up in full at [memory-readout.md](memory-readout.md), including the working code,
so putting it back is a paste rather than an evening. Two things there are worth reading even if
nobody ever does: `nvidia-smi` cannot report per-process VRAM on any consumer Windows machine
-- it answers `[N/A]` and never errors -- and the engine's committed RAM is 8 GB against a
working set of under one, so the "about 3.5 GB" this window says the engine holds is the
graphics card and not the memory.
