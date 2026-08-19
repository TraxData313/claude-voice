# Pausing whatever else is playing — what we found, and why it is not in

A podcast in a browser and a voice reading Claude's answer over it is not two things at
once, it is neither. So: could the engine ask the other one to stop while she talks, and
start it again after?

**Yes, and it was built and proved on a real machine. It is not switched on.** Nothing
imports it, no setting turns it on, and the engine does not know it exists. It is
`hush.py`, a script you can run by hand, kept for the day it is worth wiring in.

The reason it is not in is not that it failed. It is that it is a second background thread
polling COM, a config key, a tick box, and a class of failure that ends in *somebody's
meeting* — in exchange for not having to press space. That is a lot of surface for a
convenience, and this project is meant to stay small.

```bash
python hush.py            # what it can see
python hush.py --try      # pause it, wait three seconds, start it again
```

## What actually works

Everything below was run against Chrome playing YouTube, on the machine this was written
on, with a Teams call going at the same time.

**Finding what is playing is Core Audio, and it is exact.** The same COM the volume slider
uses: the session list of the default playback device, the process behind each session, and
each session's peak meter. That answers *chrome.exe is making a sound right now*, which is
the whole question. A browser with a paused video keeps its session open and silent, so the
meter is the thing to ask, not the session's own state. It still saw a video whose volume
had been turned right down.

**Chrome answers `WM_APPCOMMAND`, both ways.** This was the surprise, and it is the good
news. `APPCOMMAND_MEDIA_PAUSE` posted to a Chrome top-level window pauses the video, and
`APPCOMMAND_MEDIA_PLAY` starts it again. That is a message aimed at one window: it cannot
touch a call, a music player, or anything else on the machine. Firefox handles the same
message directly.

Chrome plays all its audio through one process, so the pid on the audio session is not the
pid holding the window — the window has to be found by the executable *name* instead.

**The global media key is the fallback, and it is the dangerous one.** It goes to whatever
holds the media focus, and it is a *toggle*: sent blind, it starts music nobody asked for.
Only worth pressing when the polite message was ignored.

## The two things that cost real time

**A pause you cannot see is worse than a slow one.** The first version sent the pause and
gave it a fifth of a second to go quiet. Chrome pauses at once, but its audio is already in
flight, so the meter kept reading — the check called it a failure, skipped the fallback,
and recorded nothing as paused. The video *was* paused, and because nothing had been
recorded, nothing ever put it back. It sat there paused. The fix is to **wait for silence
rather than demand it**: up to a second and a half, and then the silence has to hold for a
third of a second, which is what separates a paused video from the gap between two words.

**Never press the media key while a meeting is running.** Teams, Zoom, Discord and the rest
are never candidates for pausing, and while any of them is making a sound the global key is
not pressed at all — only the targeted message, which cannot reach them. Being in a meeting
is worth more than pausing a video, and *it did nothing* is a much better failure than *it
did something to my meeting*. In the live test that rule fired exactly as intended.

Everything sent is checked afterwards. A pause counts as ours only if the sound really
stopped; a resume only happens if it is still stopped when she finishes; a key press that
changed nothing is pressed once more to undo whatever else it did; and a player that
refuses is left alone for two minutes rather than being poked at every sentence.

## What it would take to wire in

`hush.Watcher` is written and works: a thread on a fifth-of-a-second tick, given something
that answers *is she talking*, which pauses on the way up and resumes a couple of seconds
after she stops. Wiring it in is three things:

1. A `busy()` on `Speaker` — true while anything is being synthesised, played or queued.
   Wider than `speaking`, so the pause lands before the first word rather than after it.
2. `hush.Watcher(speaker.busy, log).start()` in `main()`, and `.stop()` in the `finally`,
   so a podcast is never left paused by an engine that has gone.
3. A `pauseMedia` key in the config, off by default, read on the watcher's own timer, and a
   tick box under the cog.

## What it will never do

- **A muted tab is silent to the meter**, so a video playing on mute is not noticed.
- **It looks once, when she starts.** Something started mid-answer waits for the next thing
  she says.
- **A call taken in a browser tab** is a browser making a sound, so it would be sent the
  message. A call has no pause to answer with, so nothing should happen — but that is the
  case to think hard about before turning any of this on.
- **If the engine is killed outright mid-sentence**, whatever was paused stays paused.
