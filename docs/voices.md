# Voices

A speaker embedding is not a setting. It is a model of a particular person's voice,
made from a recording of them, good enough that this repo can put words in their mouth
they never said. Treat the `voices/` folder accordingly.

## What ships here

| voice | | |
|---|---|---|
| `abby` | female | the author's own |
| `max` | male | the author's own |

Both were made from recordings their owner is entitled to clone. That is the whole test
for anything added to this folder.

## What may go in `voices/`

Only voices you have the right to publish:

- **Your own voice.** Uncomplicated, and the best place to start.
- **Someone who explicitly agreed** to be cloned and to have that clone distributed.
  Agreeing to be recorded is not the same as agreeing to be cloned.
- **A voice with a licence that actually permits it** — some datasets on Hugging Face
  are released for exactly this, with the terms written down. Read them; "freely
  available" and "free to redistribute a clone of" are different claims.
- **A synthetic voice** generated from a prompt, belonging to nobody.

## What may not

- **Anything from a game, film, or show.** Game dialogue is the studio's audio and the
  actor's performance, and neither owning the game nor extracting the files yourself
  changes that. This applies to the derived embedding as much as to the recording — the
  embedding is the part that does the impersonation.
- **Any actor, presenter, streamer, or public figure**, however easy the audio is to
  find. Not a celebrity, not an actor, not someone off YouTube.
- **Anyone who has not agreed**, including friends and family. Ask first.

## Keeping voices you cannot publish

Cloning from a recording, on your own machine, for your own ears, is a different act
from redistributing the result. If you have voices in that category, keep them outside
this repo and point at them from `config.json`:

```json
"extraVoicesDirs": ["C:\\somewhere\\else\\my-voices"]
```

They appear in `voice_cli.py list` marked *local only*, work exactly like bundled ones,
and cannot be committed by accident because they are not in the tree. Either layout works
in those folders — `<sex>\<id>` or `<sex>\<culture>\<id>`.

## Making one

```powershell
python voice_cli.py clone C:\path\to\sample.wav --name "Ada" --sex female
```

A clean 20–40 second mono clip of **one** person talking, no music and no second speaker.
Two people in one clip produces a clone that sounds like neither. The embedding lands in
`voices\<sex>\<id>\embedding.json` alongside a `voice.json` describing it.

Optionally add an ICL prompt, which clones a little closer at the cost of a larger file.
It has to be a second run, because loading the ICL encoder unloads the talker model
underneath the engine — and its `--text` must be *what is actually said in the clip*:

```powershell
python voice_cli.py clone C:\path\to\sample.wav --icl-only --text "the words spoken in it"
python voice_cli.py source icl
```
