# What changed

Newest first. Each heading is a version of `version.json`, and the update check reads the
headline out of that file rather than out of this one — so a release means editing both.

To move from one of these to the next: `/voice update --apply`, or by hand,
**[updating →](docs/updating.md)**.

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
