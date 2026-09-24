"""
Carry a voice across from the Qwen engine to Pocket TTS.

    python make_pocket_voice.py abby
    python make_pocket_voice.py max --seconds 25
    python make_pocket_voice.py abby --from some-recording.wav

The point of the first form: **the original recordings are not needed.** Pocket
TTS clones from any wav, and the Qwen engine is itself a source of clean audio
for every voice it already has. A render from it has no room tone, no breath
and no microphone in it, which is the best case a cloning prompt can be -- the
model reproduces the quality of the sample it is given as faithfully as it
reproduces the voice.

It is a clone of a clone, so something shifts. Whether what comes back still
sounds like her is a question for the ear, which is why this writes a
before-and-after pair of wavs and tells you where they are.

What it leaves behind is `voices\\<sex>\\<id>\\pocket.safetensors` -- a baked
speaker state that loads instantly, beside the embedding that made it. The
catalogue picks it up on its own; nothing else has to be told.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pocket_engine
import voice_lib
from qwen_engine import write_wav

# What the voice reads to be cloned from.
#
# Not a random paragraph. A cloning prompt is the only evidence the model gets
# about how this person sounds, so it wants to be broad rather than long: every
# vowel, the awkward consonant clusters, a question and an exclamation so the
# pitch moves, and numbers, which are where a flat reading shows up first.
#
# It is also in character on purpose. The prompt carries delivery as well as
# timbre -- a bored sample gives a bored clone -- and these voices have a manner
# that the tool's whole point depends on. Read docs/voices.md before pointing
# this at anybody who has not agreed to it.
REFERENCE = (
    "Hii! Let's see if this works. I'm going to read for about twenty seconds, "
    "so there's enough of my voice here to learn from. "
    "The quick brown fox jumps over the lazy dog, which is the sentence "
    "everybody uses, and it really does cover almost everything. "
    "Numbers next: one, four, seven, thirteen, forty-two, ninety-nine. "
    "Strength, twelfth, sixths — those are the awkward ones, aren't they? "
    "And a question, to see the pitch go up at the end. "
    "That should be plenty. Let's hear how it turned out!"
)


def render_reference(voice, text, out_path, state):
    """Speak the reference passage in the Qwen voice, and keep the wav."""
    from qwen_engine import Engine

    eng = Engine(state["studioDir"], verbose=False)
    eng.load_models(state["modelDir"], state["talker"])
    try:
        kwargs = {"embedding_path": voice["embedding"]} if voice["embedding"] \
            else {"icl_prompt_path": voice["icl"]}
        began = time.monotonic()
        samples = eng.synthesize(text, **kwargs)
        # The engine leaves a different amount of dead air at the top every
        # time, and silence at the front of a cloning prompt is silence the
        # model learns to open on.
        drop = voice_lib.trim_head(samples, pocket_engine.SAMPLE_RATE)
        if drop:
            samples = samples[drop:]
        seconds = write_wav(out_path, samples)
        # The words beside the clip, under the same name. Breeze TTS 2 clones
        # from exactly this pair and nothing less -- a transcript that drifts
        # from the audio teaches it a wrong alignment -- so a voice carried
        # across to Pocket is ready for Breeze too. See voice_lib.BREEZE_REFERENCES.
        with open(os.path.splitext(out_path)[0] + ".txt", "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text + "\n")
        print(f"  rendered {seconds:.1f}s in {time.monotonic() - began:.1f}s "
              f"-> {out_path}")
        return out_path, seconds
    finally:
        eng.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("voice", help="which voice to carry across, e.g. abby")
    ap.add_argument("--from", dest="source", metavar="WAV",
                    help="clone from this recording instead of rendering one")
    ap.add_argument("--text", help="what the reference passage says")
    ap.add_argument("--language", help="which Pocket TTS model to bake against")
    ap.add_argument("--rerender", action="store_true",
                    help="render the reference again even if one is already there")
    args = ap.parse_args(argv)

    state = voice_lib.load_state()
    # Against the Qwen catalogue whatever the config currently says, because
    # that is where the voice being carried across lives.
    voices = voice_lib.catalog(state, "qwen")
    hit = next((v for v in voices if v["id"].lower() == args.voice.lower()), None)
    if hit is None:
        raise SystemExit(f"no voice called '{args.voice}'. Have: "
                         + ", ".join(v["id"] for v in voices))
    if not (hit["embedding"] or hit["icl"]) and not args.source:
        raise SystemExit(f"'{hit['id']}' has nothing to render from; "
                         "pass --from <recording.wav>")

    language = args.language or voice_lib.engine_language(state)
    out = os.path.join(hit["dir"], voice_lib.POCKET_VOICE)
    wav = args.source or os.path.join(hit["dir"], "pocket-reference.wav")

    print(f"Carrying {hit['name']} across to pocket-tts ({language}).")
    if args.source:
        if not os.path.exists(wav):
            raise SystemExit(f"no such file: {wav}")
        print(f"  cloning from {wav}")
    elif os.path.exists(wav) and not args.rerender and not args.text:
        # Rendering it again costs a model load and half a minute of GPU, and
        # gives a *different* reading -- the model rolls its prosody afresh
        # every time. Reusing the one already there means a run that failed
        # further down, at the gated download, can be retried for nothing and
        # against the same sample. --rerender when you want a new take.
        print(f"  reusing the reference already rendered: {wav}")
    else:
        render_reference(hit, args.text or REFERENCE, wav, state)

    print("  loading pocket-tts...")
    eng = pocket_engine.Engine(language=language).load_models()
    from pocket_tts import export_model_state

    began = time.monotonic()
    try:
        voice_state = eng.model.get_state_for_audio_prompt(wav)
    except ValueError as exc:
        # Cloning lives in different weights from the built-in voices, and
        # those are gated. The package's own message is accurate but arrives as
        # a traceback halfway through a sentence about something else, so it is
        # caught here and said plainly -- the reference wav is already made and
        # a retry costs nothing.
        if "voice cloning" not in str(exc):
            raise
        raise SystemExit(
            "\nPocket TTS clones with weights that Kyutai gate, and this "
            "machine is not signed in.\n"
            "The built-in voices need no account; cloning does. Two steps, "
            "both yours to take:\n\n"
            "  1. Open https://huggingface.co/kyutai/pocket-tts and accept "
            "the terms.\n"
            "  2. Sign in locally:  hf auth login\n\n"
            f"Then run this again. The reference is already rendered\n"
            f"({wav}), so the retry reuses it and costs seconds.")
    export_model_state(voice_state, out)
    print(f"  cloned in {time.monotonic() - began:.1f}s -> {out}")

    # Which model this state belongs to, written where the catalogue reads it.
    # Without it a voice baked against English would be handed to whichever
    # model happened to be loaded -- and that gives nonsense rather than an
    # error, which is the worst way for it to go wrong.
    meta = os.path.join(hit["dir"], "voice.json")
    try:
        import json

        with open(meta, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
        if doc.get("PocketLanguage") != language:
            doc["PocketLanguage"] = language
            with open(meta, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=4)
                fh.write("\n")
            print(f"  noted the language in {os.path.basename(meta)}")
    except (OSError, ValueError) as exc:
        print(f"  could not write the language into voice.json: {exc}")

    # Say the same words back in the new voice, so the two wavs sit beside each
    # other and the only question left is the one only an ear can answer.
    after = os.path.join(hit["dir"], "pocket-after.wav")
    began = time.monotonic()
    samples = eng.synthesize(args.text or REFERENCE, pocket_voice=out)
    seconds = write_wav(after, samples)
    print(f"  spoke {seconds:.1f}s back in {time.monotonic() - began:.1f}s "
          f"= {seconds / max(0.01, time.monotonic() - began):.1f}x realtime")

    print()
    print(f"Before: {wav}")
    print(f"After : {after}")
    print(f"{hit['name']} is now in the pocket catalogue. To hear her:")
    print(f"  python voice_cli.py engine pocket")
    print(f"  python voice_cli.py set {hit['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
