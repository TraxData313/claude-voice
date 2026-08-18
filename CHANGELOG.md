# What changed

Newest first. Each heading is a version of `version.json`, and the update check reads the
headline out of that file rather than out of this one — so a release means editing both.

To move from one of these to the next: `/voice update --apply`, or by hand,
**[updating →](docs/updating.md)**.

## 1.5.0 — 2026-08-18

**The panel has a settings window now, and every tick in it says what it actually does.**

The top strip had grown three tick boxes, each of which had to explain itself in two words
and could not. *auto start* and *on top* are not the same kind of thing at all, and nothing
on screen said which of them was about the window and which was about three and a half
gigabytes of model. So they have gone behind a cog, where each of them gets a sentence.

- **A cog in the top right, and four settings behind it.** *auto start*, *start with
  Windows*, *dark* and *on top*, each with a line under it saying what ticking it would do.
  Esc closes it, it opens over the panel, and flipping *dark* while it is open recolours it
  where it stands.
- **`start with Windows` is new**: it opens this window when you log in. Tick it together
  with *auto start* and the whole thing simply talks — the window comes up by itself, loads
  the engine, turns the voice on, and there is nothing left to press. Both are off until you
  ask, because neither opening a window nor taking three and a half gigabytes should happen
  because you logged in.
- **That tick has no setting behind it.** Windows opens whatever is in the Startup folder, so
  the folder *is* the setting: ticking writes the same shortcut the installer puts on your
  Desktop, unticking deletes it, and the box is then read back off the folder rather than
  left where the click put it. A shortcut that could not be written leaves the box unticked,
  which is the truth, rather than ticked, which would be a promise.
- **The engine button wears a chip.** The cog it used to wear now means settings, the way a
  cog means settings everywhere else, and what that button loads and hands back is a model —
  so it gets the chip. Drawn against a power symbol, a bolt and a robot, and chosen by eye.
- **`auto-check for updates` can say what it means again.** In the footer it had to be called
  `auto-check`, because the longer label ate the room the line beside it needed. In a dialog
  there is room. What stays in the footer is the report rather than the controls: what is in
  an update, how the last look went, and the version itself — which now turns the link colour
  when there is something to take, since the button that used to announce it is behind a cog.
- **The volume slider moved up beside the buttons, and the window is a row shorter.** It had
  a row to itself because the old button row was full; the tick boxes leaving made the room.
  The word *volume* and the percentage went with it — there is one slider in this window and
  it does not need labelling, and the number is in the hover text now, which follows the
  handle while you drag it. Worth 23 pixels of height, measured.
- **A check that finishes after you close the dialog no longer has a button to write to.**
  What it would have said is remembered instead, so the next dialog opens already saying it —
  and one opened while a check is still running finds the button greyed out and reading
  *checking…*, which is where it left off.

## 1.4.1 — 2026-08-18

**The engine has a switch of its own now, so you can hand its memory back without a terminal.**

`/voice off` only stops her talking. The engine stays loaded on purpose — that is what makes
turning it back on instant — but it is sitting on about 3.5 GB while it waits, and the panel
could start one and never stop one. Getting that memory back meant opening a terminal, which
is the one thing this window exists to avoid.

- **A cog, leftmost in the button row.** Green means the model is loaded, red means it is
  not, and pressing it does whichever is left. It replaces the old `start engine` button,
  which only ever appeared when there was nothing running.
- **Unloading turns the voice off as well**, and has to: the hook loads an engine again the
  moment Claude says anything, so unloading with the voice still on would free three
  gigabytes for about five seconds. Same *off first, then kill* that `/voice kill` needs.
- **Loading says so.** The cog goes grey for the minute a first load takes, rather than
  wearing a colour that is not true yet, and says *loading the model* if you rest on it. It
  drops that the moment the engine answers rather than waiting out a timer.
- **`dark` and `on top` have moved to a strip along the very top**, the way an ordinary
  window has one. The left of that strip is empty on purpose — it is where a File or a
  Settings would go if this ever grows one.
- **The whole transport row is icons, and every one of them says what it does.** A cog for
  the engine, a **■** or a **▶** for the voice, **⏩** for one line and **⏭** for all of them —
  they differ by the bar at the end, which is the difference itself: one more, or straight to
  where there is nothing left. The button on the queue's heading is a **+**. Rest on any of
  them for a moment and a small label says the rest, including which way the two switches are
  about to go.
- **The window can be narrower than it has ever been: 348, down from 420.** As words those
  four buttons wanted 332 pixels; as icons they want 159, and the row stopped being what sets
  the width at all. Below about 396 the update note in the footer truncates, which is what it
  is cut to 30 characters for.
- **The panel comes back on the monitor it was left on.** It never did if that monitor was to
  the *left* of the main one: Tk only knows about the primary screen, so the saved position
  was a negative number, and the check that decides whether a window is still on screen read
  that as a monitor which had been unplugged and threw it away. Windows knows the shape of
  the whole desktop; it is asked now.
- **Her picture and name are drawn before any engine is running.** The window used to open on
  an empty circle and an empty dropdown until something answered, but the voice is in the
  config, the catalogue is a directory listing and the portraits are files — none of it needs
  anybody running.

## 1.4.0 — 2026-08-18

**The panel can now read out anything you type into it, not only what Claude says.**

Everything in that window steered what Claude was already saying. There was no way to hand it
a sentence of your own short of `/voice say` in a terminal, which meant opening a terminal to
use a thing whose whole point is not having to.

- **`read custom text`, on the queue's own heading.** It opens a small box; type into it, press
  **ctrl+enter** or **read it**, and the line joins the queue. Esc or **cancel** closes it
  without saying anything. The button greys out with the rest when the engine is down.
- **It waits its turn.** `/voice say` cuts off whatever is playing, because that is what
  *say this now* means. This does not — you asked for a line to be read, not for the three
  behind it to be thrown away.
- **It speaks with the voice turned off**, exactly as `/voice say` does. The master switch is
  about Claude talking unprompted; this is you asking for one line.
- **Typed lines are filed under `manual input`** in the queue and the history, so they read
  apart from the folders Claude has been working in. Clicking one in the history says it
  again, like any other.
- The API behind it: `/speak` takes `project`, and a `queue` flag that asks it not to barge
  in. Every existing caller is unchanged.

**And the on-off switch is now green or red**, so which way it is set can be seen rather than
read. Green is the voice working, red is it silent — the colour is the state, the label is
still the action, which is why a green button says `turn off`. Grey while there is no engine:
not working, but the switch is not the reason. It had to become a plain Tk button to be
coloured at all — the native Windows theme ignores colour on a themed button, so it would
have worked in dark and done nothing in light.

## 1.3.2 — 2026-08-17

**Updating no longer depends on how git was installed — she goes and finds it herself.**

The panel's update button said *git is not on PATH* on a machine with git on it, while the
same pull from the command line worked. Both were telling the truth, and that is the whole of
it: **a PATH belongs to a process.** The panel starts from the Desktop shortcut, so it
inherits Explorer's — built from the registry at login. A terminal inherits whatever started
it, and Claude Code hands its own children a PATH with a git of its own prepended. Git for
Windows lists itself only if you ticked the box during install, so a machine can use git all
day and have nothing about git in the registry.

- **The updater asks PATH first, then looks.** `%LOCALAPPDATA%\Programs\Git\cmd`,
  `%ProgramFiles%\Git\cmd`, the 32-bit folder and GitHub Desktop's bundled copy — newest
  first where the folder is versioned. An entry somebody put on PATH is a choice, and it
  still outranks every guess here.
- **It names the git it used, but only when PATH did not name one.** That single line is the
  explanation of why the button used to fail, printed at the moment somebody is wondering.
- **The two failures now read differently.** No git anywhere is not the same as git that is
  present and refusing to run in this folder, and the second quotes what git actually said.
  The by-hand zip instructions no longer open with *"no git here"* in front of the case that
  has nothing to do with git.
- **`setup.ps1` reports git**, tested against the registry PATH rather than its own, because
  the registry one is what the panel will get. Listed, installed but unlisted — with the one
  line that lists it — or absent. It reports and changes nothing: putting a folder on
  somebody's PATH is not a speech installer's decision to make.
- **The tour tells whoever installs this to check, and to ask first.** A new step 6: the check
  that tests the persistent PATH rather than the shell's, and what to do about each answer.
  Ask before editing a PATH, ask before installing git, and if there is no git at all then
  this copy did not arrive by cloning — so say that rather than guessing.
- **Written down where it will be looked for**, in updating.md, troubleshooting.md and
  *Things that cost real time to find*. Including why `setx PATH "%PATH%;..."` — the advice
  you will find everywhere — is wrong twice: it truncates at 1024 characters, and `%PATH%`
  there is the machine and user paths already joined, so it copies every machine entry into
  your own.

Worth re-running `setup.ps1` for this one, though nothing needs reinstalling — that is how the
git line gets seen, and seeing it is the point.

## 1.3.1 — 2026-08-17

**Her icon opens the window you already have, instead of a second one just like it.**

Nothing had ever asked whether a panel was up. Every way in — the Desktop shortcut,
`voice_cli.py panel`, the update's own restart — spawns `panel.py`, and `main()` built a
`tk.Tk()` regardless. Click the icon with the window already open and you got two of them,
both live, both polling the engine, and no way to tell which was which.

- **Keyed on the window, not on a pidfile or a named mutex.** The window is the thing being
  duplicated, and asking about it directly gets the awkward case right for free:
  `reopen_panel` destroys its own window *before* spawning the replacement, so the
  replacement finds nothing to raise while the old process is still winding down. A pid
  would have called that "already running" and left an update unable to reopen its panel.
- **Raising it takes more than `SetForegroundWindow`.** Windows refuses that call from a
  process that is not already in front, and a double click on the Desktop leaves Explorer
  holding the foreground — so on its own, the click would have looked ignored. It
  un-minimises first, then borrows the foreground thread's input queue for the length of
  the call, which is the documented way to be allowed.
- **Every handle signature is spelled out.** Window handles are pointer-sized and ctypes
  hands back a C int unless told otherwise. A truncated handle is not an error; it is a
  window that quietly cannot be found, which here would have read as the fix not working.
- **`--force` opens a second one anyway.** The guard is about the accidental double, not
  about forbidding two.
- **`voice_cli.py panel` says "Panel is up" rather than "Panel opened".** The spawn is
  detached and cannot know which of the two happened, and only one of them is opening.

A pull is enough for this one. Nothing it changes is installed outside the folder.

## 1.3.0 — 2026-08-17

**She knows whose voice she is speaking in now, and lets a little of it show.**

Every session already read one line saying which voice was set. A name is trivia: a session
told only *"the voice is Abby"* still writes as though handing finished text to a component
further down the line. This replaces the line with a short block that says what is actually
happening, and then trusts the session to judge it.

- **Playing the part a little is the default.** The voice is the face the user meets, so a
  bit of its manner belongs in what they hear, without overdoing it. The block says outright
  that none of it is a rule and that plain Claude is a fine answer if that is what is wanted
  — the point is to describe the situation rather than issue an instruction.
- **The manner comes from the persona string, not from the block.** It says "a calm voice
  can be calming, a bright one can be bright" and points at the description above it, so a
  voice you clone tomorrow works as well as the two that shipped.
- **What keeps that safe is scope, not restraint.** It belongs to the `## TL;DR` and the
  short lines between tool calls, and nowhere near the body of an answer. Personas describe
  a *voice*, not a manner — Max's reads "brave, driving, motivating", and a session that
  took that for a writing instruction would deliver a stack trace as a pep talk.
- **The old line was ambiguous rather than silent.** "Cute, slightly nerdy American girl"
  sat next to *"that is you"* with nothing saying whether it described the timbre or the
  writer, so sessions resolved it differently from one day to the next. Unstable is worse
  than either answer.
- **`voice_cli.py status` says the same thing** as the block in `CLAUDE.md` rather than a
  terser version of it. Two renderings of one persona had already drifted apart once.
- **Voices with no persona get the identity paragraphs only.** Told to let a character
  through with none described, a session invents one.
- **The note reinstalls itself now, and this is the last release that needs setup for it.**
  `~\.claude\CLAUDE.md` was copied out of `speaking-notes.md` at install time and nothing
  ever put it back, so 1.2.0's new summary rule reached the repo and stopped there — every
  session carried on reciting the old one. `voice_lib.sync_notes()` rewrites the block on
  every engine start and after `/voice update --apply`. From here, changing how Claude is
  told to write takes a pull and a restart.
  - It only refreshes a block that is already there. No markers, no edit — deleting them is
    still how you turn the note off, and nothing will put it back.
  - It writes only when the result differs, so the engine-start check costs two small reads.
  - **The block now says so itself**, in a comment at the top: generated, rewritten on
    every voice change, put additions and overrides *below* the closing marker. That
    warning is aimed at Claude more than at you. `CLAUDE.md` is exactly the file a session
    writes to when it runs `/init` or is told to remember something, and nothing else in
    there tells it that one stretch of the file is owned by an app. Quietly eating a note
    somebody asked to be kept is a worse failure than any of this being out of date.
  - `speaking-notes.md` came off the list of files that force a `setup.ps1`. It was the
    worst one to leave to a hand-run installer, because a stale slash command breaks
    visibly and a session following last month's rules looks exactly like a session.
- **The panel's history says which project each line came from.** It showed the session
  title only, and four rows reading "Review feature idea" say nothing about which repo was
  talking — the answer was sitting in the session list at the bottom of the same window.
  The project gets a column of its own rather than the `project · title` prefix used down
  there: those rows are the full width of the window and have room to run a pair together,
  while a history row does not, and a joined pair truncates into porridge. A column also
  reads straight down, which is the actual question being asked of it.
- **`docs/writing-for-the-ear.md` had been left behind by 1.2.0** and still taught summaries
  as five plain sentences while the installed notes taught ten short bullets. Template and
  guide now agree. The 600-character lesson survives, attached to the rule it actually
  supports.

Re-run setup for this one. A pull does not rewrite `~\.claude\CLAUDE.md`, which is the only
place any of it takes effect.

## 1.2.0 — 2026-08-16

**She installs herself as a plugin now, and she says things in shorter bites.**

Everything up to here assumed you would clone a repository and read the instructions. Claude
Code has a plugin system and a directory to be listed in, and what this already had — a
slash command — is exactly what a plugin carries.

- **Installable.** `/plugin marketplace add TraxData313/claude-voice`, then `/plugin install
  claude-voice@claude-voice`. This repository is both the plugin and its own catalogue, so
  nobody waits on a review queue to get it. Passes `claude plugin validate --strict`.
- **`/claude-voice:setup`** fetches the engine and the model — the 3 GB a plugin download
  cannot carry. It says what it is about to pull down *before* it starts, takes `--whatif`,
  and refuses to run itself unasked, because a skill that can start a 3 GB download has no
  business being something a model reaches for on its own.
- **It names the hardware before the download, not after.** Both engine builds are CUDA
  builds and there is no CPU one, so on AMD, Intel graphics or a Mac this fails at model
  load. That is a terrible place to learn it, and now the README, the install guide and the
  site all say so at the door.
- **The video-memory figure is published as an estimate and labelled one.** The models are
  2.2 GB of weights, so ~4 GB is arithmetic plus room to work — but only a 16 GB card has
  ever run this, and pretending otherwise would be inventing a number. Reports from smaller
  cards welcome; it is the one figure here nobody has established.
- **Summaries are short bullets now, not paragraphs.** Heard once and in order, a sentence
  that runs on has lost its own beginning by the time it ends. The note also says plainly
  that an answer *without* a summary is not summarised but recited, start to finish, which
  was the actual reason listening got tiring.
- **A thirty-second demo, with sound**, on the pages — both voices, the panel, and what
  installing looks like. It only downloads if you press play.
- `commands\voice.md` moved to `templates\`. It was never a command; it is the template the
  installer fills in. But `commands\` at a plugin root is read as *real* commands, so
  leaving it there would have shipped a `/voice` still saying `__PYTHON__`.

Running this one needs `setup.ps1` again, and Claude Code restarted afterwards: the notes
it installs have changed, and hooks and slash commands are read once, at session start.

## 1.1.0 — 2026-08-16

**She stays the same person the whole way through an answer now, and she has stopped buzzing.**

Three complaints that sounded like one bug and were not: stopping partway through a summary,
changing voice between sentences, and occasionally buzzing or babbling. Each had its own cause,
in a different part of the pipeline. **[The measurements behind all of it →](docs/engine-notes.md)**

- **An answer is one generation now, streamed.** It used to be a generation per group of
  sentences, and the model rolls its prosody afresh for each one — so the same voice came back
  as a recognisably different person at every seam, which is what "she keeps changing voice"
  was. One streaming generation keeps two seconds of what it has already said as context, so
  the voice is continuous by construction rather than by care.
- **And it starts sooner.** First audio at 812 ms, against 1967 ms to synthesise the first
  chunk the old way. Playback is still cut into pieces, because the player takes one file at a
  time, but the cuts are placed in the gaps between words — found by listening for the quietest
  moment, since the engine's own text alignment lags far too much to trust.
- **The buzzing was a derail, and there was no guard against it at all.** Autoregressive TTS
  sometimes misses its end-of-speech token and generates until it hits a ceiling; the engine's
  own default is 327 seconds of noise. Every clip is now measured against its own text and made
  again if it is not speech. Replayed over the thousand generations in a real log, honest speech
  ran between 0.20× and 1.18× of an honest reading and the one real derail sat at 2.21×, alone —
  caught, with no false positives anywhere in the run.
- **Two ceilings, and the second trusts nothing.** Streaming reaches the engine's own token
  ceiling, which is exact to the sample. On top of it, audio is counted as it arrives and the
  generation is stopped from inside the callback if it runs away. Truncation is safe: a derail
  drifts, so every word before it is good.
- **Long answers are read in full.** The summary ceiling was 600 characters and it cut on a
  full stop, so an overlong summary *sounded* finished and simply was not — silently. It is
  4000 now, it exists only to stop a runaway, and it says in the log when it fires.
- **`history <count>`** sets how many utterances stay replayable. It was always a ring of 40;
  now it is adjustable, and documented.
- **The engine log stops growing forever.** It was opened in append mode and never rolled, at
  about a kilobyte per spoken line. Capped now, keeping one previous.

`streaming: false` puts the old road back, for a Studio build without the streaming exports.

## 1.0.0 — 2026-08-16

**It now knows what version it is, and can tell you when a newer one is out.**

The first numbered version. Everything before this was "whatever you happened to clone", which
made a bug report hard to answer and a new release impossible to hear about.

- **A version number.** `version.json` is the truth, `/voice status` prints it, and the panel
  shows it in the bottom right corner. Quote it when something goes wrong.
- **`/voice update`.** Asks GitHub whether there is a newer version, says what is in it, and
  with `--apply` pulls it and restarts the engine — which is the step people miss, because
  code changes do nothing until the running engine is replaced.
- **The same thing in the panel**, along the bottom row: a tick box for the weekly check, one
  button that checks and then updates, a **what's new** link to read before deciding, and how
  long ago it last looked.
- **Automatic checks, off by default.** `/voice update on` turns on one look a week; nothing
  contacts the network until you do. Every contact is written to `logs\update.log`, so the
  claim is one you can check rather than one you have to take on trust.
- **A changelog** — this file.

Everything up to here, in one line: an offline Qwen-TTS voice for Claude Code, with a Stop
hook and a transcript watcher, two shipped voices, the panel, per-app volume, cloning, the
one-paste installer, and the `## TL;DR` contract that decides what gets read aloud.
