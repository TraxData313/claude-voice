"""
Breeze TTS 2 -- BreezeBlue's 3B model -- as the third engine: the one that laughs.

What it is for. It clones a voice from a clip and its exact transcript, as the
other two clone theirs, and then does two things neither of them can. It
performs a sound written into the text -- (laugh), (sigh), (cough), (clears
throat) -- and it follows an instruction given beside the words, so a whisper
whispers and a sad line is sad. Toni heard all of that in Abby's voice on
2026-09-24; docs/laughing.md has the takes, the numbers and the verdict.

Why it runs in a process of its own. It needs CUDA torch 2.9.1, transformers
4.57.3 and qwen-tts, and this tool's Python has none of them and should not
grow them -- the answer docs/engines.md gave for Kyutai, for the same reason.
So Breeze's own streaming API, `python -m breeze_infer.api`, runs out of
Breeze's own environment, and this module is an HTTP client for it that needs
nothing beyond the standard library. It also makes close() honest: ending the
process gives back every byte of graphics memory, which an in-process model
never manages -- Qwen's CUDA context outlives its weights.

It streams for real. With the three fast decode stages the first sound arrives
about a fifth of a second in, while the rest is still being made, in pieces of
80 ms, at about twice realtime. Without them it is slower than speech.

The interface is the one speak_server.py already calls -- load_models,
synthesize_streaming, synthesize, close, and a module-level SAMPLE_RATE.
"""

import array
import ctypes
import http.client
import os
import random
import re
import socket
import subprocess
import time
import uuid
from ctypes import wintypes

SAMPLE_RATE = 24000          # what Breeze's codec emits, and what everything downstream assumes

# The sounds it performs when one is written into the text, in its own
# spelling. Nothing else in parentheses means anything to it -- it reads the
# words -- which is why voice_lib turns "(laughing)" and "*giggles*" into these
# before anything is sent.
EVENTS = ("laugh", "sigh", "cough", "clears throat")

# An instruction switches Breeze to what BreezeBlue call voice direction, and
# their docs ask for classifier-free guidance at 4 with it. Without one the
# API's own 1.0 is right: guidance steers away from a second prompt, and a
# plain clone has no second prompt to steer away from -- the server refuses it.
DIRECTED_CFG = 4.0

# The stages that make it faster than speech, and the only ones worth their
# memory here. Each 80 ms frame costs one pass through a 28-layer backbone and
# fifteen through a 12-layer depth decoder, and unaccelerated that is a string
# of small kernel launches the laptop cannot keep up with: 0.46-0.65x realtime,
# measured, so the voice stops mid-sentence to wait for itself. These three
# replay each step as a CUDA graph instead, which made it 1.6-2.1x. The other
# two stages, the text encoder and the prefill, run once per line and could only
# shave a first sound that already beats Qwen's four times over -- and --fast-all
# wants 14.4 GiB, which a 16 GB card beside a desktop does not have.
FAST_FLAGS = ("--fast-backbone-decode", "--fast-depth-decoder", "--fast-codec")

# How much text one generation is given. The prompt -- the reference clip,
# about 300 frames for Abby's 24 seconds, its transcript, and the text -- shares
# one 2048-position window with everything generated, and the server stops at
# 1500 frames whatever happens. A slow instructed line was measured at twice
# the length of a plain one, so 500 characters is the most that still fits at
# that pace with room to spare. Longer text is split at sentence ends and sent
# as several generations, one after the other, into the same stream.
MAX_CHARS = 500

# The first start compiles two stages and captures their graphs: 113 s, most
# of it compiling. Later starts find the compiled kernels on disk and take
# about half a minute. Anything past fifteen minutes is not coming up.
START_TIMEOUT = 900.0

# A long line can spend a while before its first byte on an unaccelerated
# start; nothing it does takes longer than this between two reads.
READ_TIMEOUT = 120.0

# The server takes one request at a time. One that was hung up on mid-line
# lets go a moment later, when its generator is collected, so a 409 straight
# after a skip is waited out rather than reported.
BUSY_WAIT = 5.0


def available(python, repo, model):
    """Are Breeze's environment, code and weights where the config says.

    Cheap on purpose: three existence checks, no import and no process. The
    panel and the CLI ask before they offer the engine at all, and they ask in
    a dropdown's worth of time.
    """
    return bool(python and repo and model
                and os.path.isfile(python)
                and os.path.isfile(os.path.join(repo, "breeze_infer", "api.py"))
                and os.path.isfile(os.path.join(model, "config.json")))


def split_text(text, limit=MAX_CHARS):
    """Sentence-ended pieces of at most `limit` characters, in order.

    A sentence longer than the limit on its own is cut at its last comma, or
    failing that its last space, before the limit -- the only places a reader
    would pause anyway.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    out, buf = [], ""
    for sentence in re.split(r"(?<=[.!?;:])\s+", text):
        while len(sentence) > limit:
            head = sentence[:limit]
            cut = max(head.rfind(", "), head.rfind(" "))
            cut = cut if cut > limit // 3 else limit
            if buf:
                out.append(buf)
                buf = ""
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip(" ,")
        if not buf:
            buf = sentence
        elif len(buf) + 1 + len(sentence) <= limit:
            buf += " " + sentence
        else:
            out.append(buf)
            buf = sentence
    if buf:
        out.append(buf)
    return [p for p in out if p]


def multipart(fields, files):
    """A multipart/form-data body, which is what Breeze's API takes.

    Returns (body, content_type). Written out by hand because the standard
    library has no encoder for it and this module is not going to import
    requests into a process that has never needed it.
    """
    boundary = uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"{name}\"\r\n\r\n").encode("utf-8")
        out += str(value).encode("utf-8") + b"\r\n"
    for name, (filename, data, kind) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: {kind}\r\n\r\n").encode("utf-8")
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def pcm_to_float(data):
    """Signed 16-bit little-endian PCM, as the API streams it, to floats.

    Divided by 32767 because that is what the server multiplied by, so a
    sample makes the round trip unchanged. Windows is little-endian, which is
    what lets array's native 'h' read the bytes as they come.
    """
    pcm = array.array("h")
    pcm.frombytes(data)
    return array.array("f", [s / 32767.0 for s in pcm])


def _free_port():
    """A port nothing is listening on, chosen by Windows rather than guessed.

    Picked fresh at every start, so a server left over from somewhere else --
    or anything at all on a fixed number -- can never be the one we talk to.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- a job object, so the server cannot outlive the engine ------------------

class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


def _kill_with_us(pid):
    """Tie a process's life to this one's. Returns the job handle, or None.

    The server is a separate python.exe holding eight or nine gigabytes of
    graphics memory, and the engine that started it can end without a word --
    'voice kill' is a TerminateProcess, and a crash is a crash. A child left
    behind would hold that memory with nobody to talk to it, and the next start
    would find the card already full. In a job with KILL_ON_JOB_CLOSE it cannot
    be left behind: Windows closes this process's handles when it ends, however
    it ends, and the job takes everything in it along -- compile workers
    included, since a process started inside a job is born into it.

    The argtypes are not decoration. Left to guess, ctypes passes a HANDLE as a
    32-bit int, which is the same trap rss_mb in speak_server.py fell into.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    proc = None
    try:
        if not k32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
        proc = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not proc or not k32.AssignProcessToJobObject(job, proc):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
    except OSError:
        k32.CloseHandle(job)
        return None
    finally:
        if proc:
            k32.CloseHandle(proc)
    return job


def _close_handle(handle):
    k32 = ctypes.WinDLL("kernel32")
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle(handle)


class Engine:
    """Held to the same shape as the other two, and no wider.

    Constructed, then load_models(), then synthesize_streaming() or
    synthesize(). load_models starts Breeze's server and waits until it
    answers; close ends it, and everything it held goes with it.
    """

    def __init__(self, python, repo, model, fast=True, log=None, log_path=None,
                 start_timeout=START_TIMEOUT):
        self.python, self.repo, self.model = python, repo, model
        self.fast = bool(fast)
        self._log_to = log
        self.log_path = log_path
        self.start_timeout = start_timeout
        self.proc = None
        self.port = None
        self._job = None
        self._logfile = None
        # The reference clip goes up with every request -- the API takes it as
        # an upload and encodes it each time -- so it is read off disk once
        # per voice rather than once per line.
        self._clips = {}

    def _log(self, msg):
        if self._log_to:
            self._log_to(msg)

    # -- the server's life -------------------------------------------------
    def load_models(self, *_args, **_kwargs):
        """Start Breeze's server and wait until it answers.

        Takes and ignores positional arguments so the server can call it the
        same way for every engine. Returns self.
        """
        if not available(self.python, self.repo, self.model):
            raise RuntimeError(
                "Breeze TTS 2 is not where the config says: breezePython, "
                "breezeDir and breezeModel must name its python.exe, its "
                "breeze-tts checkout and its weights. See docs/engines.md.")
        self.close()
        self.port = _free_port()
        cmd = [self.python, "-m", "breeze_infer.api", self.model,
               "--host", "127.0.0.1", "--port", str(self.port)]
        if self.fast:
            cmd += list(FAST_FLAGS)
        env = dict(os.environ)
        # This process's Python must not leak into Breeze's: a PYTHONHOME or
        # PYTHONPATH meant for the voice engine would hand the other
        # environment the wrong standard library, and it fails far from here.
        for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
            env.pop(key, None)
        env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                   TOKENIZERS_PARALLELISM="false",
                   # The weights are on disk. Nothing needs the network, and a
                   # check against the Hub on every start is seconds to lose.
                   HF_HUB_OFFLINE="1",
                   # Inductor's static CUDA launcher passes a pointer through a
                   # C long, which is 32 bits on Windows, and every compiled
                   # kernel dies with an OverflowError. Found the hard way on
                   # OpenAudio S1; see docs/laughing.md.
                   TORCHINDUCTOR_USE_STATIC_CUDA_LAUNCHER="0")
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
            self._logfile = open(self.log_path, "a", encoding="utf-8")
            self._logfile.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} "
                                f"starting: {' '.join(cmd)}\n")
            self._logfile.flush()
        out = self._logfile or subprocess.DEVNULL
        began = time.monotonic()
        self._log("starting breeze tts 2 ("
                  + ("fast decode stages" if self.fast else "unaccelerated") + ")")
        self.proc = subprocess.Popen(
            cmd, cwd=self.repo, env=env, stdout=out, stderr=out,
            stdin=subprocess.DEVNULL, creationflags=0x08000000)   # CREATE_NO_WINDOW
        self._job = _kill_with_us(self.proc.pid)
        if self._job is None:
            self._log("  could not tie the breeze server to this process; "
                      "'voice kill' may leave it running")
        try:
            self._wait_ready(began)
        except BaseException:
            self.close()
            raise
        self._log(f"  breeze answered after {time.monotonic() - began:.0f}s "
                  f"(pid {self.proc.pid}, port {self.port})")
        return self

    def _wait_ready(self, began):
        """Until /health says ok. uvicorn listens only once the load and, when
        fast, the compiling and graph capture are done, so a refused
        connection means 'not yet' and nothing else."""
        while True:
            code = self.proc.poll()
            if code is not None:
                raise RuntimeError(f"breeze stopped while starting (exit code {code})"
                                   + self._log_tail())
            try:
                if self._get("/health") == 200:
                    return
            except OSError:
                pass
            if time.monotonic() - began > self.start_timeout:
                raise RuntimeError(f"breeze was not up after {self.start_timeout:.0f}s"
                                   + self._log_tail())
            time.sleep(0.5)

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()

    def _log_tail(self, lines=6):
        """The last few lines the server wrote, for an error worth reading."""
        if not self.log_path:
            return ""
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as fh:
                tail = [ln.rstrip() for ln in fh.readlines()[-lines:] if ln.strip()]
        except OSError:
            return ""
        return (": " + " | ".join(tail)) if tail else ""

    def _alive(self):
        """Start the server again if it has died since the last line.

        A crash between two answers should cost the next one a start, not
        every answer after it -- the engine thread would otherwise go on
        handing lines to a port with nobody behind it until a restart.
        """
        if self.proc is not None and self.proc.poll() is not None:
            self._log(f"the breeze server had stopped (exit code {self.proc.returncode})"
                      f"{self._log_tail(3)} -- starting it again")
            self.load_models()

    def close(self):
        """End the server, and with it every byte of memory it held.

        Closing the job handle is what does it: the job kills everything in
        it, compile workers included, which terminating the one process would
        not. The wait is for the memory -- it is back only once the process is
        really gone, and the engine being swapped in next is about to ask for it.
        """
        proc, self.proc = self.proc, None
        if self._job:
            _close_handle(self._job)
            self._job = None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._log("the breeze server took more than 30 s to end")
        if self._logfile:
            try:
                self._logfile.close()
            except OSError:
                pass
            self._logfile = None

    # -- what the server calls ---------------------------------------------
    def _clip(self, path):
        stamp = os.path.getmtime(path)
        hit = self._clips.get(path)
        if hit is None or hit[0] != stamp:
            with open(path, "rb") as fh:
                hit = (stamp, fh.read())
            self._clips[path] = hit
        return hit[1]

    def synthesize_streaming(self, text, on_piece, breeze_ref=None, breeze_ref_text=None,
                             instruction=None, max_seconds=None, **_ignored):
        """Speak `text`, handing audio over as it arrives.

        `on_piece(samples, None)` is called for each piece, on this thread;
        return False from it to stop, which hangs up on the server mid-line.
        `instruction` is how to say it, in words, and is performed rather than
        read. Sounds are written into `text` itself, as "(laugh)".

        Returns how many samples were handed over. Extra keyword arguments are
        swallowed, as the other engines swallow them: a kwarg meant for another
        engine means the config changed under a running one, and speaking is
        better than raising at the top of an answer.
        """
        if not breeze_ref or not breeze_ref_text:
            raise ValueError("breeze clones from a clip and its transcript, "
                             "and this voice has not got both")
        self._alive()
        # The same guard the other engines keep: count what actually arrives,
        # and stop it at the ceiling the server worked out from the text.
        budget = int((max_seconds or 120.0) * SAMPLE_RATE * 1.1) + SAMPLE_RATE
        clip = self._clip(breeze_ref)
        total = 0
        for part in split_text(text):
            made, stopped = self._generate(part, on_piece, clip, breeze_ref_text,
                                           instruction, budget - total)
            total += made
            if stopped:
                break
        return total

    def _generate(self, text, on_piece, clip, clip_text, instruction, budget):
        """One request, streamed. Returns (samples handed over, stopped early)."""
        fields = {"text": text, "ref_text": clip_text,
                  "cfg_scale": DIRECTED_CFG if instruction else 1.0,
                  # Fresh every time. The API's own default is a fixed 42, and
                  # with it the same line always came out the same way -- a
                  # take that missed its laugh would miss it every time asked.
                  "seed": random.randrange(1, 2**31 - 1)}
        if instruction:
            fields["instruction"] = instruction
        body, kind = multipart(fields, {"ref_audio": ("reference.wav", clip, "audio/wav")})

        deadline = time.monotonic() + BUSY_WAIT
        while True:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=READ_TIMEOUT)
            try:
                conn.request("POST", "/v1/audio/speech", body=body,
                             headers={"Content-Type": kind,
                                      "Content-Length": str(len(body))})
                resp = conn.getresponse()
                if resp.status == 409 and time.monotonic() < deadline:
                    resp.read()
                    conn.close()
                    time.sleep(0.1)
                    continue
                if resp.status != 200:
                    detail = resp.read(600).decode("utf-8", "replace")
                    raise RuntimeError(f"breeze answered {resp.status}: {detail}")
                return self._read(resp, on_piece, budget, len(text))
            finally:
                conn.close()

    def _read(self, resp, on_piece, budget, chars):
        total, carry = 0, b""
        while True:
            data = resp.read1(65536)
            if not data:
                return total, False
            data = carry + data
            even = len(data) - (len(data) % 2)
            carry = data[even:]
            if not even:
                continue
            samples = pcm_to_float(data[:even])
            total += len(samples)
            if on_piece(samples, None) is False:
                return total, True
            if total > budget:
                self._log(f"derail guard: {total} samples for {chars} characters "
                          f"-- stopping it here")
                return total, True

    def synthesize(self, text, **kwargs):
        """Speak `text` whole. Mono float samples at SAMPLE_RATE."""
        out = array.array("f")

        def keep(samples, _chunk):
            out.extend(samples)
            return True

        self.synthesize_streaming(text, keep, **kwargs)
        return out
