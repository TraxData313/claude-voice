r"""
Installing Qwen on PyTorch -- the way the Qwen engine runs with Smart App
Control on.

Studio's DLLs are not signed, and Windows 11's Smart App Control refuses them;
it is on by default on a clean install and cannot be turned back on without
reinstalling Windows, so asking anybody to turn it off is not an answer. The
same Qwen3-TTS model on PyTorch loads with it enforcing. This puts that
together, on purpose and only on purpose: about seven gigabytes, so it is
fetched when somebody picks Qwen and says yes, never because an update came.
The panel's window and 'voice_cli.py install qwen' both come through here.

What goes where, all inside the one folder that was chosen:

    env\        a Python environment of its own: CUDA torch, transformers,
                qwen-tts, at the versions requirements-qwen.txt pins
    weights\    Qwen3-TTS-12Hz-1.7B-Base, at a pinned revision

The server that runs in it is qwen_server.py, from this tool's own folder, so
an update to it needs no reinstall. Every step can be run again; a failure
half way picks up from the step that did not finish. The machine checks and
the step runner are breeze_setup's, so the two installers cannot drift apart
on what a graphics card is or where pip's cache goes.
"""

import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import breeze_setup as _b  # noqa: E402
import voice_lib as _voice_lib  # noqa: E402

ENGINE = "qwen"
TITLE = "Qwen on PyTorch"
TAGLINE = "the best voice, with Smart App Control on"
NOTE = ("Qwen-TTS Studio's files are not signed, so Windows' Smart App Control "
        "refuses them. This runs the same Qwen model on PyTorch instead, which "
        "Smart App Control allows. Every voice you already have works on it as it is.")
LICENCE = "The model's weights are Apache 2.0: free to use, commercially too."

WEIGHTS_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
WEIGHTS_REVISION = "fd4b254389122332181a7c3db7f27e918eec64e3"
TORCH_INDEX = _b.TORCH_INDEX
TORCH = _b.TORCH
REQUIREMENTS = os.path.join(ROOT, "requirements-qwen.txt")
# Pinned packages PyPI has no wheel for. Pure Python only: a compiled one here
# would be a file Smart App Control has never seen, and would be refused.
SOURCE_ONLY = ("sox",)

# Measured: 3.9 GB on the card loaded, 4.65 GB at its peak while speaking.
NEED_VRAM_MIB = 6_000
NEED_CAPABILITY = (8, 0)                # bf16 throughout
NEED_CUDA = _b.NEED_CUDA
NEED_DISK_GB = 16                       # 5.5 GB environment, 4.3 GB weights, the downloads
DOWNLOAD_GB = 7
# The pins are the cp312 wheels seen to load under Smart App Control. Another
# Python gets other files, which Smart App Control has to judge afresh.
PYTHONS = ((3, 12), (3, 12))

LOG_DIR = _voice_lib.LOG_DIR
STATUS_PATH = os.path.join(LOG_DIR, "qwen-install.json")
LOG_PATH = os.path.join(LOG_DIR, "qwen-install.log")
CREATE_NO_WINDOW = _b.CREATE_NO_WINDOW


def default_target():
    best = max(_b.fixed_drives() or ["C:\\"], key=_b.free_gb)
    if best.upper().startswith("C:"):
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                            "claude-voice", "qwen")
    return os.path.join(best, "claude-voice", "qwen")


def check_machine(target=None):
    """What Qwen on PyTorch needs, beside what this machine has. The same
    shape breeze_setup.check_machine returns, so the panel draws either."""
    target = target or default_target()
    rows, verdict = [], "full"

    def row(need, have, ok):
        nonlocal verdict
        rows.append({"need": need, "have": have, "ok": ok})
        if ok is False:
            verdict = "no"
        elif ok is None and verdict == "full":
            verdict = "slow"

    cards = _b.gpus()
    card = cards[0] if cards else None
    if card is None:
        row("an NVIDIA graphics card with 6 GB or more",
            "no NVIDIA card found (nvidia-smi is not on this machine)", False)
    else:
        gb = card["memory_mib"] / 1024
        row("an NVIDIA graphics card with 6 GB or more", f"{card['name']}, {gb:.0f} GB",
            card["memory_mib"] >= NEED_VRAM_MIB)
        cap = card.get("capability")
        if cap is not None:
            row("a card from 2020 or later (RTX 30 series and up)",
                f"compute capability {cap[0]}.{cap[1]}", cap >= NEED_CAPABILITY)
        cuda = card.get("cuda")
        row("a driver that runs CUDA 12.8",
            f"driver {card['driver']}"
            + (f", CUDA {cuda[0]}.{cuda[1]}" if cuda else ", CUDA version unknown"),
            (cuda >= NEED_CUDA) if cuda else None)

    free = _b.free_gb(target)
    row(f"about {NEED_DISK_GB} GB free where it goes",
        f"{free:.0f} GB free on {os.path.splitdrive(os.path.abspath(target))[0] or target}",
        free >= NEED_DISK_GB)

    here = sys.version_info[:2]
    row("Python 3.12 to build its environment from",
        f"Python {here[0]}.{here[1]}", PYTHONS[0] <= here <= PYTHONS[1])
    return {"verdict": verdict, "rows": rows, "gpu": card, "target": target}


STEPS = [
    ("env", "making its Python environment"),
    ("torch", "downloading PyTorch with CUDA (2.9 GB)"),
    ("cuda", "checking PyTorch can see the graphics card"),
    ("deps", "installing qwen-tts and what it needs"),
    ("weights", "downloading the model (4.3 GB)"),
    ("start", "starting it once, to hear it work"),
]


def paths(target):
    return {
        "python": os.path.join(target, "env", "Scripts", "python.exe"),
        "env": os.path.join(target, "env"),
        "weights": os.path.join(target, "weights"),
        "cache": os.path.join(target, "cache"),
        "done": os.path.join(target, "installed.json"),
    }


def read_status():
    try:
        with open(STATUS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_status(**fields):
    status = {**read_status(), **fields, "updated": time.time()}
    os.makedirs(LOG_DIR, exist_ok=True)
    tmp = STATUS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(status, fh)
    os.replace(tmp, STATUS_PATH)
    return status


def running():
    status = read_status()
    if status.get("state") == "running" and _b._pid_alive(status.get("pid")):
        return status
    return None


def _done_steps(target):
    try:
        with open(paths(target)["done"], encoding="utf-8") as fh:
            return set(json.load(fh).get("steps", []))
    except (OSError, ValueError):
        return set()


def _mark_done(target, step):
    done = _done_steps(target) | {step}
    with open(paths(target)["done"], "w", encoding="utf-8") as fh:
        json.dump({"steps": sorted(done), "weights": WEIGHTS_REVISION}, fh, indent=2)


def _run(cmd, log, env=None, cwd=None):
    log.write(f"\n$ {' '.join(cmd)}\n")
    log.flush()
    code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW).returncode
    if code:
        raise RuntimeError(f"{os.path.basename(cmd[0])} {' '.join(cmd[1:3])} failed "
                           f"(exit code {code}); the install log has the rest: {LOG_PATH}")


def _first_start(p, log, say):
    """Start it once and have it say something, then stop it -- the only proof
    the whole thing works that does not need somebody listening. If the graphed
    predictor will not start it tries without, and says so."""
    import qwen_torch_engine
    import voice_lib

    state = voice_lib.load_state()
    try:
        _, kwargs = voice_lib.resolve("abby", "embedding", {**state, "engine": "qwen"})
    except LookupError:
        kwargs = None
    for graph in (True, False):
        eng = qwen_torch_engine.Engine(
            p["python"], p["weights"], graph=graph,
            log=lambda m: (log.write(m + "\n"), log.flush()),
            log_path=os.path.join(LOG_DIR, "qwen-server.log"),
            warm_voice=(kwargs or {}).get("embedding_path"))
        try:
            eng.load_models()
            if kwargs:
                samples = eng.synthesize("Hi! I'm all set up.", **kwargs)
                if len(samples) < qwen_torch_engine.SAMPLE_RATE // 2:
                    raise RuntimeError("it started, but said almost nothing")
            return graph
        except Exception as exc:
            log.write(f"\nstart {'with' if graph else 'without'} the graphed predictor "
                      f"failed: {exc}\n")
            if graph:
                say("the fast predictor would not start; trying without it")
            else:
                raise RuntimeError(f"Qwen would not start: {exc}")
        finally:
            eng.close()
    return False


def install(target, say=print):
    """Install into `target`, step by step. Returns the paths it wrote down."""
    import voice_lib

    target = os.path.abspath(target)
    p = paths(target)
    os.makedirs(target, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    _write_status(state="running", pid=os.getpid(), target=target, step=0,
                  steps=len(STEPS), label="starting", error=None, started=time.time())
    done = _done_steps(target)
    graph = True
    with open(LOG_PATH, "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} installing into {target}\n")
        try:
            env = _b._env(p)
            for i, (step, label) in enumerate(STEPS, 1):
                if step in done and step not in ("cuda", "start"):
                    continue
                _write_status(step=i, label=label)
                say(f"[{i}/{len(STEPS)}] {label}")
                if step == "env":
                    shutil.rmtree(p["env"], ignore_errors=True)
                    _run([_b._console_python(), "-m", "venv", p["env"]], log, env=env)
                elif step == "torch":
                    _run([p["python"], "-m", "pip", "install", "--only-binary=:all:", *TORCH,
                          "--index-url", TORCH_INDEX, "--progress-bar", _b._progress_bar(p, env)],
                         log, env=env)
                elif step == "cuda":
                    found = _b._check_cuda(p, log, env)
                    say(f"      torch {found['torch']} sees the card")
                elif step == "deps":
                    # Exactly the set seen to load under Smart App Control, and
                    # nothing compiled here: a file built on the spot is a file
                    # Microsoft's cloud has never seen. The one exception is
                    # sox, a qwen-tts dependency published only as source --
                    # plain Python, so building it makes nothing to be judged.
                    _run([p["python"], "-m", "pip", "install", "--only-binary=:all:",
                          *[f"--no-binary={name}" for name in SOURCE_ONLY],
                          "-r", REQUIREMENTS, "--extra-index-url", TORCH_INDEX,
                          "--progress-bar", _b._progress_bar(p, env)], log, env=env)
                elif step == "weights":
                    fetch = ("from huggingface_hub import snapshot_download; "
                             f"snapshot_download(repo_id={WEIGHTS_REPO!r}, "
                             f"revision={WEIGHTS_REVISION!r}, local_dir={p['weights']!r})")
                    _run([p["python"], "-c", fetch], log, env=env)
                elif step == "start":
                    graph = _first_start(p, log, say)
                _mark_done(target, step)
            voice_lib.patch_state(qwenPython=p["python"], qwenModel=p["weights"],
                                  qwenGraph=graph)
            shutil.rmtree(p["cache"], ignore_errors=True)
            _write_status(state="done", step=len(STEPS), label="installed", fast=graph)
            say("Qwen on PyTorch is installed"
                + ("." if graph else ", without its fast predictor -- it will speak "
                   "slower than realtime, so expect a wait before each line."))
            return p
        except BaseException as exc:
            log.write(f"\nFAILED: {exc}\n")
            _write_status(state="failed", error=str(exc) or type(exc).__name__)
            raise
