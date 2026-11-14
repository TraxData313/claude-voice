# Voice packs

A program with a library of voices of its own — the Immersive AI mod brings ninety-two —
registers its folder through `POST /voice-roots`, and every voice in it is a clip. Pocket TTS
can speak a clip only by cloning it, and cloning needs weights Kyutai keep behind a Hugging Face
sign-in. That is fine for one person at a desk and not something to ask of every player.

A **baked state** (`pocket.safetensors`) needs no sign-in: it is what cloning would have made,
made once, ahead of time. But a hundred of them come to a couple of hundred megabytes — too big
to ship inside the program that owns the voices — and that program's own deploy wipes its folder,
so nothing may be kept there either.

So the folder carries a small `pocket-pack.json` that says where a zip of the baked states can be
had. This app fetches it into its own data folder, checks it, and from then on speaks each of
those voices on Pocket, with or without cloning. The voice folder is never written to.

## `pocket-pack.json`

At the top of the registered voice folder, beside `female\` and `male\`:

```json
{
  "schema": "claude-voice/pocket-pack",
  "version": 1,
  "name": "immersiveai-calradia",
  "url": "https://github.com/TraxData313/ImmersiveAI/releases/download/pocket-voices-983151f/pocket-voices-983151f.zip",
  "sha256": "6978320583d895df9237c73ea847dddef4661a68e8254799f81e927f0eac49c1",
  "bytes": 205420890,
  "weights": {
    "repo": "kyutai/pocket-tts",
    "revision": "983151f13aaeab1b13c1e5e3c2c383d49a9edf3f",
    "language": "english"
  },
  "voices": [
    "female/aserai/aserai_female_curt",
    "male/vlandia/vlandian_male_curt"
  ]
}
```

| field | what it is |
|---|---|
| `schema`, `version` | always `"claude-voice/pocket-pack"` and `1`. A copy that reads an older version leaves a newer pack alone and says so |
| `name` | the pack's folder name here: letters, digits, `.` `_` `-`, up to 64 |
| `url` | the zip: `https://`, `http://` or `file:` |
| `sha256`, `bytes` | of the zip. Both are checked before anything is unpacked |
| `weights.repo`, `weights.revision` | the Hugging Face repo and the **full 40-digit commit** of the Pocket weights the states were baked on |
| `weights.language` | which Pocket model they belong to (`english`, `french`, …). It becomes each voice's language here, whatever its `voice.json` says |
| `voices` | every voice the zip covers, as its folder's path inside the voice root, with `/` |

A voice in `voices` is spoken from the pack only if the folder also exists in the voice root —
that is where its name, sex, culture and persona come from. A voice folder with its own
`pocket.safetensors` keeps using that one.

## The zip

One entry per voice, at the same path the voice has in the root:

```
female/aserai/aserai_female_curt/pocket.safetensors
male/vlandia/vlandian_male_curt/pocket.safetensors
...
```

No top folder around them. Anything else in the zip (a `manifest.json`, a readme) is ignored, and
nothing whose path climbs out is ever written. A zip missing any listed voice is refused whole:
that is a packaging mistake, and a pack that half-works hides it. Stored or deflated, either is
fine; float16 states are fine too — they are brought up to the model's dtype as they load.

## Where it goes

`<data>\pocket-packs\<name>\<first 12 digits of the revision>\`, laid out as in the zip, with a
`pack.json` written last that records the zip's checksum. The data folder is
`%LOCALAPPDATA%\claude-voice` for an installed copy (an update leaves it alone) and the checkout
itself for a developer's copy (ignored by git). `CLAUDE_VOICE_PACKS_DIR` moves it.

The zip is downloaded beside that folder as a `.part` (resumed if a try was cut off), checked,
unpacked into a sibling folder and renamed into place, so the catalogue sees the whole old pack or
the whole new one and never half of either. The zip is deleted afterwards.

**Present and verified** means that `pack.json` exists and names the same `sha256` as the root's
`pocket-pack.json`. Then nothing is downloaded. Publish a new zip with a new checksum and it
replaces the old one on the next fetch.

## The weights must match

A baked state belongs to one release of the model. Handed to another it does not fail — it speaks,
confidently, in garbage. So a pack is fetched and used only when its `weights.repo` and
`weights.revision` are what this copy's pocket-tts loads for that language: the config's
`weights_path`, or its `weights_path_without_voice_cloning` (the ungated weights a player
without a sign-in gets are the same release, and take the same states). Otherwise the log says,
once, which revision the pack wants and which this copy has, and the voices stay what they were.
A pack baked for the next pocket-tts is a new pack, with a new name or revision folder.

## When it is fetched

- **On install or update**, for any voice folder already registered: the installer runs
  `voice_cli.py packs fetch` in the installed copy.
- **When Pocket loads**, in the background, for any pack not in place yet — and when a folder with
  a pack is registered while Pocket is speaking.
- **On request**: `voice_cli.py packs fetch`, or `POST /voice-packs {"fetch": true}`.

Never in front of speech. Until a pack arrives its voices are what they were — clips, so absent
from Pocket where it cannot clone — and they join `/voices` when it is done. Offline, a background
try waits ten minutes before the next; a zip that fails its checksum waits an hour (a request
tries again at once).

## How it stands

`POST /voice-packs` (or `voice_cli.py packs`):

```json
{"packsDir": "C:\\Users\\…\\claude-voice\\pocket-packs",
 "packs": [{"root": "C:\\…\\Modules\\ImmersiveAI\\Voices", "name": "immersiveai-calradia",
            "state": "ready", "detail": "", "voices": 92, "bytes": 205420890,
            "revision": "983151f13aaeab1b13c1e5e3c2c383d49a9edf3f", "language": "english",
            "url": "https://…", "dir": "C:\\…\\pocket-packs\\immersiveai-calradia\\983151f13aae"}]}
```

`state` is one of `ready`, `downloading` (with `done` bytes), `absent` (not fetched yet),
`offline`, `failed` (with the reason in `detail`), `mismatch` (baked on other weights),
`waiting` (pocket-tts is not installed, so whether it fits cannot be told yet) or `invalid`
(the `pocket-pack.json` itself, with the reason).

## For the program that owns the voices

1. Bake each voice against the weights pocket-tts loads (`hf://kyutai/pocket-tts/…@<revision>`
   in its `config\<language>.yaml`), and zip them as above.
2. Publish the zip somewhere it can be downloaded without signing in.
3. Ship `pocket-pack.json` at the top of the voice folder you register. Nothing else changes:
   `POST /voice-roots` as before, and cast from `/voices`.
