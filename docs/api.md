# Talking to her from another program

The engine is a small HTTP server on `127.0.0.1:8765`. Everything that speaks through it —
the transcript watcher, the hooks, the panel — does so with one of the requests below, and
anything else on this machine can do the same: an assistant, a script, a game. This page is
the whole contract, written for whoever is teaching a program to use it.

It listens on localhost only and asks for no key, so anything running on this machine can
talk to it and nothing off it can.

## Say something

```
POST /speak
{"text": "(laugh) Okay, I did not expect that to work on the first try.", "mood": "excited"}
```

| field | |
|---|---|
| `text` | what to say. The only one required. |
| `mood` | how to say all of it, by name — `sad`, `whisper`, `excited`… — see [Moods](#moods-beside-the-words). A `(whisper)` in front of the text does the same |
| `instruction` | how to say it in your own words, if no mood fits. Wins over `mood`. 200 characters at most |
| `voice` | who says it, by id or any unambiguous part of one. Leave it out for whoever is set |
| `queue` | `true` waits its turn behind whatever is playing. Left out, the line cuts in, because a request is usually somebody asking for this now |
| `project` | a label for the panel's history, and the name said aloud when the speaker changes |
| `announce` | `false` never says that name. For a program with a screen that already shows who is talking |
| `unreadable` | `"refuse"` answers 422 instead of speaking, when the engine cannot read the alphabet — see [For a game](#for-a-game) |

The reply comes at once, before a word is spoken, and says what will actually be done with
what was asked for — judged against the engine the next line comes out of:

```json
{"queued": 1, "voice": "abby", "mood": "excited",
 "instruction": "Speak quickly and excitedly, bright and breathless, full of delight.",
 "instructed": true, "events": ["laugh"], "eventsDropped": []}
```

`instructed: false` means the engine speaking next will not perform the mood — Pocket
performs none. `eventsDropped` lists sounds it cannot make; they are taken out rather than
read aloud. A `warning` appears when a mood's name was not recognised, and the line is
spoken plainly rather than refused, because a typo should not cost the sentence.

## Sounds, written where they happen

On Breeze, four sounds are performed when written into the text, at the place they happen:

```
(laugh)   (sigh)   (cough)   (clears throat)
```

```
So I told it to stop, and it just kept going, (laugh) like it hadn't heard me at all.
```

The spelling is forgiving, since the one writing these is usually a language model with
habits of its own: `(laughs)`, `[giggles]`, `*chuckling*`, `(laughs softly)` and
`(a nervous laugh)` all arrive as `(laugh)`. A chuckle or a giggle becomes a laugh, because
Breeze has one laugh.

**Do not follow a laugh with "ha ha".** The tag makes the sound, and Breeze's own examples
never add the words; they would be read on top of it. The takes Toni listened to had the
laugh both opening a sentence and in the middle of one, and he heard her laugh — he did not
single either placement out.

Qwen and Pocket make none of these sounds, and never read the tag out either: it is taken
out, and the sentence closes up around it. So text written for Breeze is safe to send
whichever engine is speaking.

## Moods, beside the words

A mood is about the whole line and rides beside it. By name:

| mood | the instruction it becomes |
|---|---|
| `happy` | Speak brightly and happily, warm and smiling, with a lift at the end of each phrase. |
| `excited` ✓ | Speak quickly and excitedly, bright and breathless, full of delight. |
| `playful` | Speak playfully and teasingly, light and bouncy, as if holding back a grin. |
| `calm` | Speak calmly and gently, unhurried and soft, as if settling someone down. |
| `tender` | Speak softly and tenderly, warm and close, full of affection. |
| `sad` ✓ | Speak slowly and sadly, quiet and downcast, with long pauses. |
| `tired` | Speak slowly and wearily, low and heavy, as if at the end of a long day. |
| `serious` | Speak slowly with a restrained, serious tone. |
| `whisper` ✓ | Whisper quietly and secretively, as if leaning in to share a secret. |
| `surprised` | Speak with sudden surprise, quick and rising, as if caught off guard. |
| `angry` | Speak sharply and angrily, clipped and forceful, barely holding it in. |

✓ were heard in Abby's voice on Breeze before they were written here: the whisper whispers,
and the sad one is very sad. The rest follow the same pattern and have not been listened to
yet.

**Your own instruction wants the same shape**: a verb and two or three things about the
delivery. One adjective steers nothing — [measured](engines.md#telling-qwen-how-to-say-it) —
and that is why the names above expand to sentences rather than being sent as they are.

**A near miss still lands.** An `instruction` that names one mood and says nothing else about
the sound — `whisper this line`, `very sad, please` — is taken as that mood, and gets its
whole sentence. `whisper it slowly` asks for more than the mood does, so it goes as written.
And one slip in a name of five letters or more is forgiven, anywhere a mood is read:
`wisper` is `whisper`. That was a line typed into the panel, which went out plain with
nothing to say why. The short names are never guessed at, because one letter off `sad` is
`said`.

Breeze performs a mood strongly. Qwen performs it too, more gently — the same sad
instruction there is "a little sad", in Toni's words. Pocket ignores it.

**Or write it into the text**, as a stage direction in front of the words: `(whisper) I
found it.` That is how a Claude session asks, having no field of its own, and anything else
may do the same, a line typed into the panel included. The direction comes out of the words
whether or not it is used, so it is never read aloud, and a `mood` or an `instruction` sent
in its own field wins over it. Only a bracket holding one word counts, and only when that
word is one of the names above, a spelling like `whispering` or `softly`, or one slip off
either.
**[What a session is told, and when →](writing-for-the-ear.md#a-mood-and-a-laugh-when-the-engine-has-them)**

A mood can slow a line down a great deal — that is often the point — so a line with one is
allowed to run twice as long as the text alone would justify before it is cut off as a
runaway. Breeze's slow sad line ran 11.4 seconds for a sentence that reads in six.

## Asking first

```
POST /capabilities
```

```json
{"engine": "breeze", "engineLoaded": "breeze", "voice": "abby",
 "instruction": true, "maxInstruction": 200,
 "events": ["laugh", "sigh", "cough", "clears throat"], "eventSyntax": "(laugh)",
 "moods": {"sad": "Speak slowly and sadly, ...", "...": "..."},
 "moodsHeard": ["sad", "excited", "whisper"],
 "engines": {"qwen":   {"label": "Qwen — GPU 3.3 GB", "installed": true, "instruction": true, "events": []},
             "pocket": {"label": "Pocket TTS — CPU", "installed": true, "instruction": false, "events": []},
             "breeze": {"label": "Breeze 2 — GPU 13 GB", "installed": true, "instruction": true,
                        "events": ["laugh", "sigh", "cough", "clears throat"]}}}
```

Everything a program needs to decide how to write its lines, in one answer: whether to put
a laugh in, which moods to offer itself. `engine` is the one the next line comes out of;
`engineLoaded` is the one in memory now, and the two differ only until the next line
arrives. Ask once at the start and again whenever the reply to a `/speak` says less was
performed than you expected — somebody may have switched engines.

## The rest

| request | does |
|---|---|
| `POST /stop` | stops now, and drops everything queued |
| `POST /skip` | drops the line being spoken and carries on with the queue |
| `POST /pause` `{"on": true}` | holds her where she is, losing nothing. No argument toggles |
| `POST /state` | everything the panel draws: what is playing, the queue, the history, the voices |
| `POST /set-voice` `{"voice": "abby"}` | who speaks from now on, for every session |
| `POST /set-engine` `{"engine": "breeze"}` | which engine speaks; it loads on the next line |
| `POST /volume` `{"level": 0.6}` | 0 to 1, audible mid-sentence |
| `POST /health` | whether it is up, which version, and whether a mood or a sound would be performed |
| `POST /voices` | every voice the engine speaking now can use, each with its `sex` and `culture` |
| `POST /voice-roots` `{"add": "C:\\…"}` | also read the voices in that folder, where they lie. `remove` takes one away |
| `POST /storage` | where each engine's files are on disk and how many bytes they take, and the app's own folder |
| `POST /panel` | opens the panel window, or raises the one already open |
| `POST /quit` | closes the engine and hands its memory back |

`/set-engine` answers 404 for Breeze when it is not installed, with a sentence saying how.
It is never downloaded by a request: that takes somebody at the panel or the command line
saying yes to eleven gigabytes.

## For a game

The Immersive AI mod for Mount & Blade II: Bannerlord speaks every character through this, and
these are the few things it needed that a Claude session did not. Anything that gives a voice to
many characters can use them the same way.

- **Find it asleep.** Every engine start writes `%LOCALAPPDATA%\claude-voice\where.json` —
  `root`, `python`, `port`, `version`. Start it with `python <root>\voice_cli.py start` (no
  window), then wait for `/health` to say `ready: true`; the first load takes up to a minute.
- **Bring your own voices.** `POST /voice-roots {"add": "<folder>"}` and the voices in it join the
  catalogue as they lie, never copied, never written to. The folder may be laid out as
  `<sex>\<id>`, `<sex>\<culture>\<id>`, or flat `<id>` with `Gender` (1 woman, 2 man) and
  `Culture` in each `voice.json`. It is remembered in `extraVoicesDirs`; asking again is harmless.
- **Cast from `/voices`.** Each engine has its own voices — Pocket speaks a voice only if it has a
  baked state, or a clip where cloning is available; Breeze only if it has a clip and its words —
  so read the list again after `/set-engine`. A voice folder speaks on all three engines when it
  holds `embedding.json` **and** `breeze-reference.wav` with its `.txt`;
  `make_reference_clips.py <folder>` renders that pair for a whole library through Qwen.
- **Keep your name out of it.** `announce: false` — the screen already says who is speaking.
- **Silence over a note.** Breeze and Pocket cannot read Cyrillic, and would normally say so
  aloud before reading the rest. `unreadable: "refuse"` answers
  `422 {"unreadable": true, "engine": "pocket"}` instead, and says nothing; tell the player on
  your own screen that Qwen reads every language.
- **Installing it for somebody.** `setup-app\` builds `ClaudeVoiceSetup.exe`, a one-window
  installer for people who have never opened a terminal; a game can download it from the latest
  release and run it with `--for "<your game>"`. See [setup-app/README.md](../setup-app/README.md).
- **Installing it without a window.** `--quiet --engine qwen --data D:\claude-voice` installs with
  nothing on screen — right for a full-screen game, where a window popping up is the bug — and
  writes `%LOCALAPPDATA%\claude-voice\setup-status.json` as it goes: `state` (running, done,
  failed, cancelled), which of four `phase`s (app, engine, model, start), a `headline` and a
  `detail` ("1.2 of 2.2 GB · 14 MB/s · about 2 min left"), `phaseFraction` and `fraction`, and
  `error`. Draw your own progress from it. Leaving a file named `setup-cancel` beside it asks the
  install to stop; nothing downloaded is lost. The install is its own process, so a game that
  quits does not stop it, and the next one can pick the progress up from the same file.
- **Show where the gigabytes went.** `POST /storage` answers each engine's folders and their size,
  so a player can see what each is for, open the folder, and take it away again —
  `<root>\uninstall.ps1 -Yes`, the same thing Settings → Apps runs.

## Things worth knowing

- **One line at a time, in order.** Send with `queue: true` to line them up, as a
  conversation does. Without it a line is heard at once. It cuts off what is playing and
  replaces your own lines still waiting, where yours are the ones sent with the same
  `project`. A line of anyone else's that it cuts off is said again straight after it, from
  the start, and anyone else's that were waiting keep their places.
- **Long text is fine.** Breeze is handed it in pieces of up to 500 characters, cut at
  sentence ends and sent one after another into the same stream. Each piece is a
  generation of its own, from the same clip of the voice.
- **The first line after switching to Breeze waits for it to load** — about half a minute,
  two minutes the very first time. A line sent straight after a skip starts about two
  seconds late, while Breeze's server lets go of the one it was on.
- **Cyrillic is dropped on Breeze and Pocket**, with a spoken note saying how much. Qwen
  reads it. See [languages.md](languages.md).
- Nothing is kept of what was said beyond the panel's history of recent lines, on this
  machine.

## From Python, with nothing to install

```python
import json, urllib.request

def say(text, **how):
    body = json.dumps({"text": text, "queue": True, **how}).encode()
    req = urllib.request.Request("http://127.0.0.1:8765/speak", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)

say("(laugh) You caught me. I got the watermelon wrong again.")
say("I looked everywhere, and I couldn't find it.", mood="sad")
```
