# What changed

Newest first. Each heading is a version of `version.json`, and the update check reads the
headline out of that file rather than out of this one — so a release means editing both.

To move from one of these to the next: `/voice update --apply`, or by hand,
**[updating →](docs/updating.md)**.

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
