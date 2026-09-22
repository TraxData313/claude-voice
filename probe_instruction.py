"""Does the engine's 0x28 field do anything?

The field is in the parameter block because Studio writes it, and it has always
been passed as null here. Wiring it up is cheap; believing it works without
looking is not, so this asks the only question that matters: does the same
sentence come out different when the field is filled?

One generation cannot answer that, because the sampler is at temperature 0.9
and two runs of identical input already differ. So it is asked as a difference
of means: several runs under a slow, sad instruction against several under a
fast, excited one. If the field is read, the durations separate; if it is
ignored, the two sets are the same distribution and overlap.

It loads a model and spends a minute of GPU. Run it on purpose.

    python probe_instruction.py [runs-per-arm]
"""

import statistics
import sys
import time

import voice_lib
from qwen_engine import SAMPLE_RATE, Engine

LINE = "I did not expect you to come back so soon, and now I have to say something."
ARMS = [
    ("nothing", None),
    ("slow", "Speak very slowly and sadly, with long pauses."),
    ("fast", "Speak quickly and excitedly, rushing the words."),
]


def one(engine, kwargs, instruction):
    """A whole generation, measured in seconds of audio handed over."""
    got = [0]

    def piece(samples, _chunk):
        got[0] += len(samples)
        return True

    started = time.monotonic()
    engine.synthesize_streaming(LINE, piece, max_seconds=40,
                                instruction=instruction, **kwargs)
    return got[0] / SAMPLE_RATE, time.monotonic() - started


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    state = {**voice_lib.load_state(), "engine": "qwen"}
    _, kwargs = voice_lib.resolve("abby", "embedding", state)
    print("loading the talker...", flush=True)
    engine = Engine(state["studioDir"], verbose=False)
    engine.load_models(state["modelDir"], state["talker"])
    print("loaded\n", flush=True)

    results = {}
    try:
        for name, instruction in ARMS:
            seconds = []
            for i in range(runs):
                audio, spent = one(engine, kwargs, instruction)
                seconds.append(audio)
                print(f"  {name} {i + 1}/{runs}: {audio:5.2f}s of audio "
                      f"in {spent:4.1f}s", flush=True)
            results[name] = seconds
    finally:
        engine.close()

    print()
    for name, seconds in results.items():
        spread = statistics.pstdev(seconds) if len(seconds) > 1 else 0.0
        print(f"{name:8s} mean {statistics.mean(seconds):5.2f}s  "
              f"sd {spread:4.2f}  {[round(x, 2) for x in seconds]}")

    slow, fast = results["slow"], results["fast"]
    gap = statistics.mean(slow) - statistics.mean(fast)
    noise = max(statistics.pstdev(slow + fast), 0.01)
    print(f"\nslow minus fast: {gap:+.2f}s, against a spread of {noise:.2f}s")
    # Not a p-value. A field that is read should push the two arms apart by
    # more than the sampler pushes runs of one arm apart; anything less is
    # indistinguishable from the noise that was there anyway.
    print("the field is read" if gap > noise
          else "no difference the sampler does not already explain")


if __name__ == "__main__":
    main()
