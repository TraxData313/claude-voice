# Hearing the voice

Click her portrait and the voice you have set says one short line you already know.

That is the whole point of it. It is not a way to make her say something — the **+** on
the queue reads anything you type, and always did. This is a *reference*: one sentence you
have heard fifty times, so that pressing it answers, in five seconds and against a memory
rather than against nothing —

- **does she still sound right**, or has something drifted;
- **is the engine actually alive**, or has it hung with the panel still looking fine;
- **is it still healthy** — a machine three hours into a session, a model that has been
  sitting in memory all afternoon, a first word that takes far longer than it used to.

They are Abby's and Max's lines from the samples on the website, which is where most
people hear this project first, so the same words out of your own machine are a comparison
rather than a new thing to judge.

## Changing them

They are in `TEST_LINES`, in `voice_lib.py`. That is the only place they live.

There was briefly a `test-lines.json` beside the config, on the theory that the words
should be editable without touching code. It was wrong twice over: nobody but whoever
hacks on this ever wants to change them, and a second home for the same sentence meant the
file quietly won — so an edit made in the code did nothing at all, and looked exactly like
a broken button. One place, in the code.

**The panel has to be restarted** to pick up an edit, since that is when it imports
`voice_lib`. `/voice panel` again is enough; the engine is untouched and stays warm.

A voice with no line of its own gets `default`, where `{name}` and `{persona}` are filled
in from that voice's `voice.json` — so a voice cloned this morning has something to say
without being written a line first.

## Two small things worth knowing

- **The test barges.** Everything else the panel queues; this one cuts in. Pressing it is
  asking to hear something *now*, and a sample that arrives four minutes later is not a
  sample. Whatever it interrupted is gone rather than resumed, exactly as `stop` is.
- **It speaks even when the voice is switched off**, like `voice say`. The switch means
  "do not read my sessions aloud"; pressing a button with a speaker on it is asking
  anyway.

## Why the exclamation marks

The model reads punctuation as performance. The same sentence ending in a full stop comes
out flatter, which is why these end the way they do. It also rolls its prosody afresh on
every generation, so two presses are never quite identical — **one dull reading is the
model, not the line.** Press it again before rewriting anything.

## The portrait, and only the portrait

A left click plays the line. **Right-click swaps between Abby and Max** — that is what the
picture used to do on a left click, and the dropdown right under it changes voice too,
which is what left the click free for something better.

There was a speaker button beside the **+** for an afternoon, doing the same thing. It was
one control too many on a row that already had one, and a hand goes to the picture to ask
a voice what it sounds like anyway.
