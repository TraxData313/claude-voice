"""
Give every Qwen voice in a folder a clip of itself, so Breeze and Pocket can speak it too.

    python make_reference_clips.py C:\\path\\to\\voices
    python make_reference_clips.py C:\\path\\to\\voices --text "What the clip says."
    python make_reference_clips.py C:\\path\\to\\voices --force      # render every one again

Qwen keeps a voice as an embedding; Breeze and Pocket learn one from a recording
of it and the exact words said in it. A voice that exists only as an embedding
therefore speaks on one engine of three. This renders a short passage in each
voice through Qwen -- the same carry-across make_pocket_voice.py does for one
voice, done for a whole library with the model loaded once -- and leaves
breeze-reference.wav and breeze-reference.txt beside it. The catalogue finds
the pair on its own: Breeze reads it every line, and Pocket clones from it where
cloning is available (see voice_lib.catalog).

It is a clone of a clone, so something shifts. The render has no room tone and
no microphone in it, which is the best case a cloning prompt can be.

Nothing is written for a voice that already has its pair unless --force, and a
render that comes back far too long or too short -- the rare generation that
misses its own ending -- is tried once more and then skipped, said aloud, rather
than kept. A runaway kept here would be taught to two more engines.

The rules in docs/voices.md apply to what this makes exactly as to what it
reads: a clip of a voice is the voice.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import voice_lib
from qwen_engine import SAMPLE_RATE, write_wav

# In the world these voices were made for, and short. About ten seconds: long
# enough for a cloning prompt to hear the pitch move -- a statement, a turn, a
# question -- and short enough that a library of a hundred clips stays a small
# download. Every clip says the same words, so the voices differ by nothing but
# themselves.
CALRADIA = (
    "Well met, traveller. The road from the ford is long, and winter came early "
    "this year. Still, the grain reached the market before the rain. Tell me, "
    "what news do you bring from the north?"
)

CLIP = "breeze-reference.wav"


def voice_dirs(root):
    """Every folder under root that holds a Qwen embedding, at any depth."""
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")))
        if "embedding.json" in files:
            yield here


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("root", help="a folder of voices, in any of the catalogue's layouts")
    ap.add_argument("--text", default=CALRADIA, help="what every clip says")
    ap.add_argument("--force", action="store_true", help="render again where a clip exists")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many (for a trial run)")
    args = ap.parse_args(argv)

    todo = [d for d in voice_dirs(os.path.abspath(args.root))
            if args.force or not os.path.exists(os.path.join(d, CLIP))]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("Every voice there already has its clip.")
        return 0

    from qwen_engine import Engine

    state = voice_lib.load_state()
    print(f"{len(todo)} voice(s) to render. Loading Qwen...")
    eng = Engine(state["studioDir"], verbose=False)
    eng.load_models(state["modelDir"], state["talker"])

    # The words read at 13-17 characters a second; a clip outside a generous
    # band around that is a generation that went wrong, not a slow speaker.
    expect = len(args.text) / 15.0
    low, high = expect * 0.5, expect * 2.0

    done, skipped = 0, []
    began = time.monotonic()
    try:
        for i, d in enumerate(todo, 1):
            name = os.path.basename(d)
            seconds = 0.0
            for attempt in (1, 2):
                samples = eng.synthesize(args.text, embedding_path=os.path.join(d, "embedding.json"))
                drop = voice_lib.trim_head(samples, SAMPLE_RATE)
                if drop:
                    samples = samples[drop:]
                seconds = len(samples) / SAMPLE_RATE
                if low <= seconds <= high:
                    break
                print(f"  [{i}/{len(todo)}] {name}: {seconds:.1f}s is not a reading of that line"
                      + (", trying once more" if attempt == 1 else ", skipped"))
            else:
                skipped.append(name)
                continue

            wav = os.path.join(d, CLIP)
            write_wav(wav, samples)
            with open(os.path.splitext(wav)[0] + ".txt", "w", encoding="utf-8", newline="\n") as fh:
                fh.write(args.text + "\n")
            done += 1
            print(f"  [{i}/{len(todo)}] {name}: {seconds:.1f}s")
    finally:
        eng.close()

    took = time.monotonic() - began
    print(f"\n{done} clip(s) written in {took:.0f}s"
          + (f"; {len(skipped)} skipped: {', '.join(skipped)}" if skipped else "") + ".")
    return 1 if skipped and not done else 0


if __name__ == "__main__":
    raise SystemExit(main())
