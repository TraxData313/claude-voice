"""
Qwen on PyTorch: the Qwen engine on a machine where Studio cannot run.

Studio's DLLs are unsigned and Smart App Control refuses every one; the same
model on PyTorch and qwen-tts loads with it enforcing (measured 2026-10-17,
not one Code Integrity event). qwen_server.py is that model as a streaming
server in an environment of its own, installed by qwen_setup.py, and this
module is the client speak_server.py talks to -- the shape breeze_engine.py
already has, and most of its parts: a free port, a job object so the server
cannot outlive the engine, PCM that arrives as it is made.

The voices are the ones Studio made. Every voice folder has the embedding.json
Studio extracted, and it is the same speaker encoder's output: 0.99 cosine
against what qwen-tts extracts from the voice's own clip. So a voice that
spoke on Studio speaks here as it is, nothing re-made. Asked for the closer
clone ('source icl'), it learns from the voice's clip and its transcript
instead, where the folder has both. Studio's own icl-prompt.json is not used:
it stores a description of the voice where the transcript belongs, and Qwen
reading against the wrong words garbles the line.

What it cannot do that Studio could: take a mood beside the words. The Base
model has no instruction input; voice_lib.engine_can says so for this backend.
"""

import array
import http.client
import json
import os
import random
import subprocess
import time

from breeze_engine import (_close_handle, _free_port, _kill_with_us, pcm_to_float,
                           split_text)

SAMPLE_RATE = 24000

# A line longer than this is sent as several, cut at sentence ends. The model
# would take far more; a derail -- minutes of babbling reported as success --
# is rarer and cheaper to catch on short generations.
MAX_CHARS = 600

# Loading the 4 GB of weights and capturing the predictor's graph took 10 s on
# the laptop it was written on; the first start after an install reads them
# cold off the disk. Anything past five minutes is not coming up.
START_TIMEOUT = 300.0
READ_TIMEOUT = 60.0
BUSY_WAIT = 5.0

START_STAGES = (
    ("torch ", "starting Python", 0.10),
    ("loading ", "reading the weights", 0.30),
    ("loaded in", "warming up on the GPU", 0.70),
    ("warmed up", "almost there", 0.95),
)

CLIP = "breeze-reference.wav"
CLIP_TEXT = "breeze-reference.txt"


def server_script():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "qwen_server.py")


def available(python, model):
    """Are the environment and the weights where the config says. Three
    existence checks, no import and no process: a dropdown asks this."""
    return bool(python and model
                and os.path.isfile(python)
                and os.path.isfile(os.path.join(model, "config.json"))
                and os.path.isfile(os.path.join(model, "speech_tokenizer", "config.json")))


def voice_request(embedding_path=None, icl_prompt_path=None):
    """What the server is told about a voice, from what voice_lib.resolve gave.

    embedding_path -> its speaker embedding. icl_prompt_path -> the clip beside
    it when there is one, else the speaker embedding inside the prompt file.
    """
    if icl_prompt_path:
        folder = os.path.dirname(icl_prompt_path)
        clip, words = os.path.join(folder, CLIP), os.path.join(folder, CLIP_TEXT)
        if os.path.isfile(clip) and os.path.isfile(words):
            with open(words, encoding="utf-8-sig") as fh:
                text = fh.read().strip()
            if text:
                return {"clip": clip, "clipText": text}
        sibling = os.path.join(folder, "embedding.json")
        return {"embedding": sibling if os.path.isfile(sibling) else icl_prompt_path}
    if embedding_path:
        return {"embedding": embedding_path}
    raise ValueError("this voice has no embedding for Qwen")


class Engine:
    """Constructed, then load_models(), then synthesize_streaming() or
    synthesize(); close() ends the server and gives back its memory."""

    def __init__(self, python, model, graph=True, log=None, log_path=None,
                 start_timeout=START_TIMEOUT, progress=None, warm_voice=None):
        self.python, self.model = python, model
        self.graph = bool(graph)
        self._log_to = log
        self.log_path = log_path
        self.progress = progress
        self.start_timeout = start_timeout
        self.warm_voice = warm_voice
        self.proc = None
        self.port = None
        self._job = None
        self._logfile = None
        self._log_from = None

    def _log(self, msg):
        if self._log_to:
            self._log_to(msg)

    # -- the server's life -------------------------------------------------
    def load_models(self, *_args, **_kwargs):
        if not available(self.python, self.model):
            raise RuntimeError(
                "Qwen on PyTorch is not where the config says: qwenPython and "
                "qwenModel must name its python.exe and its weights. Install it "
                "with: python voice_cli.py install qwen")
        self.close()
        self.port = _free_port()
        cmd = [self.python, "-I", server_script(), "--model", self.model,
               "--host", "127.0.0.1", "--port", str(self.port)]
        if not self.graph:
            cmd.append("--no-graph")
        if self.warm_voice and os.path.isfile(self.warm_voice):
            cmd += ["--warm-voice", self.warm_voice]
        env = dict(os.environ)
        for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV"):
            env.pop(key, None)
        env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                   TOKENIZERS_PARALLELISM="false", HF_HUB_OFFLINE="1",
                   HF_HUB_DISABLE_TELEMETRY="1")
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
            self._logfile = open(self.log_path, "a", encoding="utf-8")
            self._logfile.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} "
                                f"starting: {' '.join(cmd)}\n")
            self._logfile.flush()
        self._log_from = self._logfile.tell() if self._logfile else None
        out = self._logfile or subprocess.DEVNULL
        began = time.monotonic()
        self._log("starting qwen on pytorch")
        self.proc = subprocess.Popen(
            cmd, cwd=os.path.dirname(server_script()), env=env, stdout=out, stderr=out,
            stdin=subprocess.DEVNULL, creationflags=0x08000000)   # CREATE_NO_WINDOW
        self._job = _kill_with_us(self.proc.pid)
        if self._job is None:
            self._log("  could not tie the qwen server to this process; "
                      "'voice kill' may leave it running")
        try:
            self._wait_ready(began)
        except BaseException:
            self.close()
            raise
        self._log(f"  qwen answered after {time.monotonic() - began:.0f}s "
                  f"(pid {self.proc.pid}, port {self.port})")
        return self

    def _wait_ready(self, began):
        while True:
            self._report_stage(began)
            code = self.proc.poll()
            if code is not None:
                raise RuntimeError(f"qwen stopped while starting (exit code {code})"
                                   + self._log_tail())
            try:
                if self._get("/health") == 200:
                    return
            except OSError:
                pass
            if time.monotonic() - began > self.start_timeout:
                raise RuntimeError(f"qwen was not up after {self.start_timeout:.0f}s"
                                   + self._log_tail())
            time.sleep(0.25)

    def _report_stage(self, began):
        if not self.progress or not self.log_path or self._log_from is None:
            return
        try:
            with open(self.log_path, "rb") as fh:
                fh.seek(self._log_from)
                text = fh.read().decode("utf-8", "replace")
        except OSError:
            return
        best = ("starting Python", 0.05)
        for marker, name, frac in START_STAGES:
            if marker in text:
                best = (name, frac)
        try:
            self.progress(*best)
        except Exception:                                  # noqa: BLE001
            pass

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
        if not self.log_path:
            return ""
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as fh:
                tail = [ln.rstrip() for ln in fh.readlines()[-lines:] if ln.strip()]
        except OSError:
            return ""
        return (": " + " | ".join(tail)) if tail else ""

    def _alive(self):
        if self.proc is not None and self.proc.poll() is not None:
            self._log(f"the qwen server had stopped (exit code {self.proc.returncode})"
                      f"{self._log_tail(3)} -- starting it again")
            self.load_models()

    def close(self):
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
                self._log("the qwen server took more than 30 s to end")
        if self._logfile:
            try:
                self._logfile.close()
            except OSError:
                pass
            self._logfile = None

    # -- what the server calls ---------------------------------------------
    def synthesize_streaming(self, text, on_piece, embedding_path=None, icl_prompt_path=None,
                             max_seconds=None, **_ignored):
        """Speak `text`, handing audio over as it arrives; on_piece returning
        False hangs up, which stops the generation on the other side.

        Extra keyword arguments are swallowed, as every engine here swallows
        them -- an instruction included, which the Base model has no input for.
        """
        voice = voice_request(embedding_path, icl_prompt_path)
        self._alive()
        budget = int((max_seconds or 120.0) * SAMPLE_RATE * 1.1) + SAMPLE_RATE
        total = 0
        for part in split_text(text, MAX_CHARS):
            made, stopped = self._generate(part, on_piece, voice, budget - total)
            total += made
            if stopped:
                break
        return total

    def _generate(self, text, on_piece, voice, budget):
        body = json.dumps({"text": text, "language": "Auto", **voice,
                           "seed": random.randrange(1, 2**31 - 1)}).encode("utf-8")
        deadline = time.monotonic() + BUSY_WAIT
        while True:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=READ_TIMEOUT)
            try:
                conn.request("POST", "/speak", body=body,
                             headers={"Content-Type": "application/json",
                                      "Content-Length": str(len(body))})
                resp = conn.getresponse()
                if resp.status == 409 and time.monotonic() < deadline:
                    resp.read()
                    conn.close()
                    time.sleep(0.1)
                    continue
                if resp.status != 200:
                    detail = resp.read(600).decode("utf-8", "replace")
                    raise RuntimeError(f"qwen answered {resp.status}: {detail}")
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
        out = array.array("f")

        def keep(samples, _chunk):
            out.extend(samples)
            return True

        self.synthesize_streaming(text, keep, **kwargs)
        return out
