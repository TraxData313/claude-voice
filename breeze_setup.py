r"""
Installing Breeze TTS 2 -- on purpose, and only on purpose.

It is about eleven gigabytes to fetch and it needs a particular kind of
graphics card, so it is never downloaded because an update arrived or because
the engine was mentioned. Somebody picks it, is shown what it needs beside
what this machine has, and says yes. The panel's window and
'voice_cli.py install breeze' both come through here, so the two cannot
disagree about what the machine needs or where anything goes.

What goes where, all inside the one folder that was chosen:

    env\          a Python environment of its own: CUDA torch, transformers, qwen-tts
    breeze-tts\   BreezeBlue's inference code, at a pinned commit
    weights\      the model, at a pinned revision

Nothing lands anywhere else. pip's cache, its unpacking and Hugging Face's
downloads are all pointed inside the folder and emptied at the end, because
the drive somebody picked is the drive they have room on -- and on the machine
this was written for, C: was the one that did not.

Every step can be run again. A failure half way -- a dropped connection, a
full disk -- leaves what finished in place, and trying again picks up from the
step that did not.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))

# The versions that were tested together on 2026-09-24, and so the ones fetched.
# Newer may well work; these did. See docs/laughing.md for what was measured.
CODE_REPO = "breezeblue-ai/breeze-tts"
CODE_COMMIT = "008f769016b0a24711becd7a4925030bc93f608c"
WEIGHTS_REPO = "BreezeBlue/Breeze-TTS-2"
WEIGHTS_REVISION = "3e28c5151381a722f1d8661b4118c298caa77aa4"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH = ("torch==2.9.1", "torchaudio==2.9.1")
# The CUDA build, pinned with its label. A bare "torch==2.9.1" would still let
# pip swap in PyPI's CPU build; "+cu128" makes that impossible rather than
# unlikely. Found the hard way on OpenAudio S1, whose CUDA extra only worked
# under uv -- see docs/laughing.md.
CONSTRAINTS = "torch==2.9.1+cu128\ntorchaudio==2.9.1+cu128\n"
# Two of the fast stages go through torch.compile, which needs Triton, which
# Windows does not ship; 3.5.x is the line built against torch 2.9.
TRITON = "triton-windows==3.5.1.post24"

# What the card needs, and why each number is what it is.
#
# Breeze asks for 12 GB and uses 7.7 GiB unaccelerated. A card sold as 12 GB
# reports about 12,280 MiB, so the line sits just under that.
NEED_VRAM_MIB = 11_500
# The fast stages -- the ones that make it faster than speech -- peaked at
# 13.1 GB while starting, on top of about 2 GB the desktop holds. Only a 16 GB
# card (16,300-odd MiB reported) fits that. Below it, it runs unaccelerated:
# at about half realtime on the laptop it was measured on.
FULL_SPEED_VRAM_MIB = 15_500
# bf16 all the way through, which wants Ampere or later.
NEED_CAPABILITY = (8, 0)
# torch's cu128 build needs a driver that can run CUDA 12.8.
NEED_CUDA = (12, 8)
# 5.4 GB of environment and 7.7 GB of weights when done, plus the wheels and
# their unpacking while it installs.
NEED_DISK_GB = 20
DOWNLOAD_GB = 11
# Wheels for everything Breeze pulls in exist for these; outside them pip
# would be building something from source, which is where installs go to die.
PYTHONS = ((3, 10), (3, 13))

LOG_DIR = os.path.join(ROOT, "logs")
STATUS_PATH = os.path.join(LOG_DIR, "breeze-install.json")
LOG_PATH = os.path.join(LOG_DIR, "breeze-install.log")

CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------
# is this machine able to run it at all
# --------------------------------------------------------------------------

def _nvidia_smi():
    found = shutil.which("nvidia-smi")
    if found:
        return found
    fallback = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                            "System32", "nvidia-smi.exe")
    return fallback if os.path.exists(fallback) else None


def gpus():
    """Every NVIDIA card this machine has, biggest first. [] when there are none.

    Asked of nvidia-smi rather than of torch, because torch is exactly the
    thing not installed yet -- and the driver's own tool is on every machine
    that has the driver, which is every machine this could run on.
    """
    exe = _nvidia_smi()
    if not exe:
        return []
    fields = "name,memory.total,driver_version,compute_cap"
    try:
        out = subprocess.run([exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    cards = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            memory = int(float(parts[1]))
        except ValueError:
            continue
        cap = None
        if len(parts) > 3 and re.fullmatch(r"\d+\.\d+", parts[3]):
            cap = tuple(int(x) for x in parts[3].split("."))
        cards.append({"name": parts[0], "memory_mib": memory, "driver": parts[2],
                      "capability": cap})
    cuda = cuda_version(exe)
    for card in cards:
        card["cuda"] = cuda
    return sorted(cards, key=lambda c: c["memory_mib"], reverse=True)


def cuda_version(exe=None):
    """The newest CUDA this driver can run, off nvidia-smi's own header."""
    exe = exe or _nvidia_smi()
    if not exe:
        return None
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=20,
                             creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def fixed_drives():
    """The machine's own disks, as 'C:\\' and friends. No network or USB drives."""
    import ctypes

    kind = ctypes.windll.kernel32.GetDriveTypeW
    kind.argtypes = [ctypes.c_wchar_p]
    out = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.exists(root) and kind(root) == 3:          # DRIVE_FIXED
            out.append(root)
    return out


def free_gb(path):
    """Free space on the drive a folder is on, whether or not it exists yet."""
    probe = os.path.abspath(path)
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        return shutil.disk_usage(probe).free / 1e9
    except OSError:
        return 0.0


def default_target():
    """Where to install, before anybody has said: the fixed drive with the most
    room. On C: that means under the user's own AppData, because the root of C:
    wants rights nothing else here asks for; any other drive gets a folder at
    its root, where people look for things."""
    best = max(fixed_drives() or ["C:\\"], key=free_gb)
    if best.upper().startswith("C:"):
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                            "claude-voice", "breeze")
    return os.path.join(best, "claude-voice", "breeze")


def check_machine(target=None):
    """What Breeze needs, beside what this machine has.

    Returns {"verdict": "full" | "slow" | "no", "rows": [...], "gpu": card or
    None, "target": folder}. Each row is {"need", "have", "ok"} with ok True,
    False, or None for "it will run, but worse" -- which is the answer a card
    between 12 and 16 GB gets, and worth being told plainly before ten
    gigabytes arrive rather than after.
    """
    target = target or default_target()
    rows, verdict = [], "full"

    def row(need, have, ok):
        nonlocal verdict
        rows.append({"need": need, "have": have, "ok": ok})
        if ok is False:
            verdict = "no"
        elif ok is None and verdict == "full":
            verdict = "slow"

    cards = gpus()
    card = cards[0] if cards else None
    if card is None:
        row("an NVIDIA graphics card, 12 GB or more",
            "no NVIDIA card found (nvidia-smi is not on this machine)", False)
    else:
        gb = card["memory_mib"] / 1024
        if card["memory_mib"] >= FULL_SPEED_VRAM_MIB:
            row("an NVIDIA card with 16 GB for full speed",
                f"{card['name']}, {gb:.0f} GB", True)
        elif card["memory_mib"] >= NEED_VRAM_MIB:
            row("an NVIDIA card with 16 GB for full speed",
                f"{card['name']}, {gb:.0f} GB -- it runs, but slower than speech, "
                "so it will wait before it starts", None)
        else:
            row("an NVIDIA graphics card, 12 GB or more",
                f"{card['name']}, {gb:.0f} GB -- too small", False)
        cap = card.get("capability")
        if cap is not None:
            row("a card from 2020 or later (RTX 30 series and up)",
                f"compute capability {cap[0]}.{cap[1]}",
                cap >= NEED_CAPABILITY)
        cuda = card.get("cuda")
        row("a driver that runs CUDA 12.8",
            f"driver {card['driver']}"
            + (f", CUDA {cuda[0]}.{cuda[1]}" if cuda else ", CUDA version unknown"),
            (cuda >= NEED_CUDA) if cuda else None)

    free = free_gb(target)
    row(f"about {NEED_DISK_GB} GB free where it goes",
        f"{free:.0f} GB free on {os.path.splitdrive(os.path.abspath(target))[0] or target}",
        free >= NEED_DISK_GB)

    lo, hi = PYTHONS
    here = sys.version_info[:2]
    row(f"Python {lo[0]}.{lo[1]} to {hi[0]}.{hi[1]} to build its environment from",
        f"Python {here[0]}.{here[1]}", lo <= here <= hi)

    return {"verdict": verdict, "rows": rows, "gpu": card, "target": target}


# --------------------------------------------------------------------------
# the install itself
# --------------------------------------------------------------------------

STEPS = [
    ("env", "making its Python environment"),
    ("torch", "downloading PyTorch with CUDA (2.9 GB)"),
    ("cuda", "checking PyTorch can see the graphics card"),
    ("code", "downloading Breeze's code"),
    ("deps", "installing what Breeze needs"),
    ("weights", "downloading the model (7.7 GB)"),
    ("start", "starting it once, to compile it and hear it work"),
]


def paths(target):
    """Where each piece lives inside the chosen folder."""
    return {
        "python": os.path.join(target, "env", "Scripts", "python.exe"),
        "env": os.path.join(target, "env"),
        "code": os.path.join(target, "breeze-tts"),
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


def _pid_alive(pid):
    import ctypes

    if not pid:
        return False
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, int(pid))    # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259                           # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def running():
    """The install in progress, if there is one: its status, else None."""
    status = read_status()
    if status.get("state") == "running" and _pid_alive(status.get("pid")):
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
        json.dump({"steps": sorted(done), "code": CODE_COMMIT,
                   "weights": WEIGHTS_REVISION}, fh, indent=2)


def _run(cmd, log, env=None, cwd=None):
    """A subprocess whose output goes to the install log, and no console window.

    The window flag matters more than it looks. The panel starts this from
    pythonw, which has no console, so every console program it starts would
    otherwise open a black window of its own on top of whatever you were doing.
    """
    log.write(f"\n$ {' '.join(cmd)}\n")
    log.flush()
    code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW).returncode
    if code:
        raise RuntimeError(f"{os.path.basename(cmd[0])} {' '.join(cmd[1:3])} failed "
                           f"(exit code {code}); the install log has the rest: {LOG_PATH}")


def _console_python():
    """python.exe beside whichever Python is running this, pythonw or not."""
    here = sys.executable
    sibling = os.path.join(os.path.dirname(here), "python.exe")
    return sibling if os.path.exists(sibling) else here


def _env(p):
    """The environment every download runs in: all caches inside the folder."""
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV"):
        env.pop(key, None)
    tmp = os.path.join(p["cache"], "tmp")
    os.makedirs(tmp, exist_ok=True)
    env.update(PIP_CACHE_DIR=os.path.join(p["cache"], "pip"), TMP=tmp, TEMP=tmp,
               PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1",
               HF_HOME=os.path.join(p["cache"], "hf"), HF_HUB_DISABLE_TELEMETRY="1",
               PYTHONIOENCODING="utf-8")
    return env


def _download_code(p, log):
    """BreezeBlue's code at the pinned commit, as a zip -- no git needed."""
    url = f"https://codeload.github.com/{CODE_REPO}/zip/{CODE_COMMIT}"
    archive = os.path.join(p["cache"], "breeze-tts.zip")
    os.makedirs(p["cache"], exist_ok=True)
    log.write(f"\nfetching {url}\n")
    log.flush()
    with urllib.request.urlopen(url, timeout=120) as resp, open(archive, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    staging = os.path.join(p["cache"], "code")
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(staging)
    inner = next(os.path.join(staging, d) for d in os.listdir(staging))
    shutil.rmtree(p["code"], ignore_errors=True)
    shutil.move(inner, p["code"])


def _check_cuda(p, log, env):
    """Does the new torch see the card, and does it carry code for it?

    Asked before seven gigabytes of weights are fetched, not after. A torch
    that quietly came from the CPU index, or a card newer than the build knows,
    fails here in seconds instead of at the first sentence.
    """
    probe = ("import json, torch; "
             "ok = torch.cuda.is_available(); "
             "cap = torch.cuda.get_device_capability(0) if ok else None; "
             "print(json.dumps({'torch': torch.__version__, 'cuda': ok, "
             "'cap': cap, 'archs': torch.cuda.get_arch_list()}))")
    out = subprocess.run([p["python"], "-c", probe], capture_output=True, text=True,
                         env=env, creationflags=CREATE_NO_WINDOW, timeout=300)
    log.write(f"\n{out.stdout}{out.stderr}\n")
    try:
        found = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError("the new PyTorch did not start; the install log says why")
    if "+cu" not in found["torch"]:
        raise RuntimeError(f"pip installed {found['torch']}, which has no CUDA in it")
    if not found["cuda"]:
        raise RuntimeError("PyTorch is installed but cannot see the graphics card")
    sm = f"sm_{found['cap'][0]}{found['cap'][1]}"
    if sm not in found["archs"]:
        raise RuntimeError(f"this PyTorch has no code for this card ({sm})")
    return found


def _first_start(p, log, say):
    """Start it once and have it say something, then stop it.

    The first start compiles two stages, which took almost two minutes when it
    was measured; every later one finds that work on disk. Paying it here means
    the first answer after switching is not the one that waits -- and it is the
    only proof the whole thing works that does not involve somebody listening.

    If the fast stages will not start -- a card too small for their peak, or a
    compile that fails -- it tries again without them, and says so, because a
    slower Breeze is still a Breeze and giving up would waste the download.
    """
    import breeze_engine
    import voice_lib

    state = voice_lib.load_state()
    try:
        _, kwargs = voice_lib.resolve("abby", "embedding", {**state, "engine": "breeze"})
    except LookupError:
        kwargs = None
    tries = [True, False]
    for fast in tries:
        eng = breeze_engine.Engine(p["python"], p["code"], p["weights"], fast=fast,
                                   log=lambda m: (log.write(m + "\n"), log.flush()),
                                   log_path=os.path.join(LOG_DIR, "breeze-server.log"))
        try:
            eng.load_models()
            if kwargs:
                samples = eng.synthesize("Hi! I'm all set up.", **kwargs)
                if len(samples) < breeze_engine.SAMPLE_RATE // 2:
                    raise RuntimeError("it started, but said almost nothing")
            return fast
        except Exception as exc:
            log.write(f"\nstart {'with' if fast else 'without'} the fast stages "
                      f"failed: {exc}\n")
            if fast:
                say("the fast stages would not start; trying without them")
            else:
                raise RuntimeError(f"Breeze would not start: {exc}")
        finally:
            eng.close()
    return False


def install(target, say=print):
    """Install into `target`, step by step. Returns the paths it wrote down.

    `say` gets one line per step, for whoever is watching; the status file gets
    the same, for the panel. Raises on failure, with the status set to say so.
    """
    import voice_lib

    target = os.path.abspath(target)
    p = paths(target)
    os.makedirs(target, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    _write_status(state="running", pid=os.getpid(), target=target, step=0,
                  steps=len(STEPS), label="starting", error=None, started=time.time())
    done = _done_steps(target)
    fast = True
    with open(LOG_PATH, "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} installing into {target}\n")
        try:
            env = _env(p)
            for i, (step, label) in enumerate(STEPS, 1):
                if step in done and step not in ("cuda", "start"):
                    continue
                _write_status(step=i, label=label)
                say(f"[{i}/{len(STEPS)}] {label}")
                if step == "env":
                    shutil.rmtree(p["env"], ignore_errors=True)
                    _run([_console_python(), "-m", "venv", p["env"]], log, env=env)
                elif step == "torch":
                    # "raw" has pip write "Progress <bytes> of <total>" lines into the log,
                    # which is what lets a setup draw a real bar for these 2.9 GB. Without it
                    # the step sat on "no counter" long enough to look stuck (2026-09-25).
                    _run([p["python"], "-m", "pip", "install", *TORCH,
                          "--index-url", TORCH_INDEX, "--progress-bar", "raw"], log, env=env)
                elif step == "cuda":
                    found = _check_cuda(p, log, env)
                    say(f"      torch {found['torch']} sees the card")
                elif step == "code":
                    _download_code(p, log)
                elif step == "deps":
                    pins = os.path.join(target, "constraints.txt")
                    with open(pins, "w", encoding="utf-8") as fh:
                        fh.write(CONSTRAINTS)
                    _run([p["python"], "-m", "pip", "install", "-r",
                          os.path.join(p["code"], "requirements.txt"), "-c", pins, TRITON,
                          "--progress-bar", "raw"], log, env=env)
                elif step == "weights":
                    fetch = ("from huggingface_hub import snapshot_download; "
                             f"snapshot_download(repo_id={WEIGHTS_REPO!r}, "
                             f"revision={WEIGHTS_REVISION!r}, local_dir={p['weights']!r})")
                    _run([p["python"], "-c", fetch], log, env=env)
                elif step == "start":
                    fast = _first_start(p, log, say)
                _mark_done(target, step)
            voice_lib.patch_state(breezePython=p["python"], breezeDir=p["code"],
                                  breezeModel=p["weights"], breezeFast=fast)
            shutil.rmtree(p["cache"], ignore_errors=True)
            _write_status(state="done", step=len(STEPS), label="installed", fast=fast)
            say("Breeze TTS 2 is installed"
                + ("." if fast else ", without the fast stages -- it will speak "
                   "slower than realtime, so expect a wait before each line."))
            return p
        except BaseException as exc:
            log.write(f"\nFAILED: {exc}\n")
            _write_status(state="failed", error=str(exc) or type(exc).__name__)
            raise
