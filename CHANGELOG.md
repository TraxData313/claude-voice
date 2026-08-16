# What changed

Newest first. Each heading is a version of `version.json`, and the update check reads the
headline out of that file rather than out of this one — so a release means editing both.

To move from one of these to the next: `/voice update --apply`, or by hand,
**[updating →](docs/updating.md)**.

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
