"""
Keeps one Qwen talker model warm and speaks whatever is POSTed to it.

Loading the model takes the better part of a minute, which is fine once and
unbearable per sentence -- so the engine lives here, in a long-running process,
behind a tiny HTTP API on localhost:

    POST /speak   {"text": "...", "voice": "abby", "source": "embedding"}
    POST /stop    stop talking now, drop anything queued
    POST /health  {"ready": true, "speaking": false, ...}
    POST /quit    shut the process down

Two threads do the work. The engine thread owns the QwenEngine and never lets
anyone else touch it -- a JNIEnv pointer belongs to the thread that made the
JVM, so calling generate() from an HTTP worker would be undefined behaviour.
It synthesises chunk by chunk and hands finished wavs to the player thread,
which is plain winsound and knows nothing about JNI. That split is also what
makes speech start on the first sentence instead of the last.
"""

import argparse
import json
import os
import queue
import sys
import tempfile
import threading
import time
import winsound
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import voice_lib
from qwen_engine import Engine, write_wav

CACHE_DIR = os.path.join(tempfile.gettempdir(), "claude-voice")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Job:
    __slots__ = ("chunks", "voice", "kwargs", "cancelled")

    def __init__(self, chunks, voice, kwargs):
        self.chunks = chunks
        self.voice = voice
        self.kwargs = kwargs
        self.cancelled = False


class Speaker:
    def __init__(self, state):
        self.state = state
        self.jobs = queue.Queue()
        self.play_q = queue.Queue(maxsize=2)
        self.lock = threading.Lock()
        self.current = None
        self.ready = threading.Event()
        self.error = None
        self.speaking = False
        self.spoken = 0
        os.makedirs(CACHE_DIR, exist_ok=True)
        threading.Thread(target=self._engine_loop, name="engine", daemon=True).start()
        threading.Thread(target=self._play_loop, name="player", daemon=True).start()

    # -- public ------------------------------------------------------------
    def submit(self, job, barge=False):
        """Queue an utterance, or cut off what is playing and jump the queue.

        Barging in was right when one hook spoke one finished answer: a newer
        answer replaced an older one. It is wrong for a running commentary,
        where each line is a different thing worth hearing -- there, cutting off
        the previous line mid-word is just losing it. So the watcher queues,
        and only a deliberate 'say this now' (replay, say) interrupts.
        """
        if barge:
            self.cancel()
        self.jobs.put(job)

    def cancel(self):
        with self.lock:
            if self.current:
                self.current.cancelled = True
        self._drain(self.jobs)
        self._drain(self.play_q)
        winsound.PlaySound(None, winsound.SND_PURGE)

    def status(self):
        return {
            "ready": self.ready.is_set() and self.error is None,
            "error": str(self.error) if self.error else None,
            "speaking": self.speaking,
            "queued": self.jobs.qsize() + self.play_q.qsize(),
            "spoken": self.spoken,
            "pid": os.getpid(),
        }

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _drain(q):
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, tuple):
                _unlink(item[1])

    def _engine_loop(self):
        try:
            eng = Engine(self.state["studioDir"], verbose=True)
            eng.load_models(self.state["modelDir"], self.state["talker"])
            log("engine ready")
        except Exception as exc:
            self.error = exc
            log(f"engine failed to start: {exc}")
            self.ready.set()
            return
        self.ready.set()

        while True:
            job = self.jobs.get()
            with self.lock:
                self.current = job
            for i, chunk in enumerate(job.chunks):
                if job.cancelled:
                    break
                try:
                    samples = eng.synthesize(chunk, **job.kwargs)
                except Exception as exc:
                    log(f"synth failed on chunk {i}: {exc}")
                    break
                if job.cancelled:
                    break
                path = os.path.join(CACHE_DIR, f"say-{os.getpid()}-{time.time_ns()}.wav")
                try:
                    write_wav(path, samples)
                except Exception as exc:
                    log(f"wav write failed: {exc}")
                    break
                if job.cancelled:
                    _unlink(path)
                    break
                self.play_q.put((job, path))       # blocks while the player is behind
            with self.lock:
                if self.current is job:
                    self.current = None

    def _play_loop(self):
        last = None
        while True:
            job, path = self.play_q.get()
            # A beat between one message and the next. The seam is useful --
            # it is how you hear that a new line has started rather than the
            # same one continuing -- so make it deliberate instead of leaving
            # it to whatever gap the synthesiser happens to leave.
            if last is not None and job is not last:
                time.sleep(self.state.get("gapSeconds", 0.45))
            last = job
            try:
                if not job.cancelled:
                    self.speaking = True
                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
                    self.spoken += 1
            except Exception as exc:
                log(f"playback failed: {exc}")
            finally:
                self.speaking = False
                _unlink(path)


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


class TranscriptWatcher(threading.Thread):
    """Speak new assistant messages by watching the session files on disk.

    Hooks are the tidy way to do this, but they only fire if the client
    actually runs them, and there is no way to tell from in here whether it
    does. Claude Code writes every turn to a JSONL transcript regardless, so
    watching those needs no cooperation from the client at all: it works in any
    session, in any project, without a restart, and it keeps working if the
    hooks are never wired up.

    Starts at the end of each file it finds, so launching this does not replay
    the entire history of a conversation at you.
    """

    PROJECTS = os.path.expanduser(os.path.join("~", ".claude", "projects"))
    FRESH_SECONDS = 15 * 60      # ignore transcripts nobody has touched lately

    def __init__(self, speaker, interval=0.7):
        super().__init__(name="watcher", daemon=True)
        self.speaker = speaker
        self.interval = interval
        self.offsets = {}

    def run(self):
        if not os.path.isdir(self.PROJECTS):
            log(f"watcher: no transcripts at {self.PROJECTS}, not watching")
            return
        log("watcher: following session transcripts")
        while True:
            try:
                self._sweep()
            except Exception as exc:
                log(f"watcher error: {exc}")
            time.sleep(self.interval)

    def _transcripts(self):
        cutoff = time.time() - self.FRESH_SECONDS
        for proj in os.scandir(self.PROJECTS):
            if not proj.is_dir():
                continue
            for f in os.scandir(proj.path):
                if f.name.endswith(".jsonl") and f.stat().st_mtime > cutoff:
                    yield f.path, f.stat().st_size

    def _sweep(self):
        state = voice_lib.load_state()
        for path, size in self._transcripts():
            seen = self.offsets.get(path)
            if seen is None:
                self.offsets[path] = size      # start at the end, not the beginning
                continue
            if size <= seen:
                self.offsets[path] = min(seen, size)   # truncated or rewritten
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(seen)
                chunk = fh.read()
                self.offsets[path] = fh.tell()
            # Config is re-read every sweep so 'voice off' takes effect at once.
            if not state.get("enabled") or not state.get("watch", True):
                continue
            for line in chunk.splitlines():
                self._consider(line, state)

    # Not _handle: threading.Thread keeps a _handle attribute of its own on the
    # instance, and it shadows any method of that name.
    def _consider(self, line, state):
        line = line.strip()
        if not line:
            return
        try:
            entry = json.loads(line)
        except ValueError:
            return                                  # a half-written final line
        if entry.get("type") != "assistant":
            return
        msg = entry.get("message") or {}
        if msg.get("role") != "assistant":
            return
        content = msg.get("content")
        text = content if isinstance(content, str) else "\n".join(
            b.get("text", "") for b in (content or [])
            if isinstance(b, dict) and b.get("type") == "text")
        if not text.strip():
            return

        speech, what = voice_lib.speech_for(text, state)
        if not speech or voice_lib.already_spoken(speech):
            return
        try:
            voice, kwargs = voice_lib.resolve(state.get("voice"), state.get("source"), state)
        except LookupError as exc:
            return log(f"watcher: {exc}")
        pieces = voice_lib.chunks(speech)
        if pieces:
            self.speaker.submit(Job(pieces, voice["id"], kwargs))   # queued, never barging
            log(f"watcher: {what} [{voice['id']}] {len(pieces)} chunk(s) {speech[:45]}...")


class Handler(BaseHTTPRequestHandler):
    speaker = None
    watching = False
    server_version = "ClaudeVoice/1.0"

    def log_message(self, fmt, *args):        # the default logger spams stderr
        pass

    def _reply(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return self._reply(400, {"error": "bad json"})

        route = self.path.split("?")[0].rstrip("/") or "/"
        sp = Handler.speaker

        if route == "/health":
            return self._reply(200, {**sp.status(), "watching": Handler.watching})

        if route == "/stop":
            sp.cancel()
            return self._reply(200, {"stopped": True})

        if route == "/quit":
            sp.cancel()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return self._reply(200, {"quitting": True})

        if route == "/speak":
            if sp.error:
                return self._reply(500, {"error": str(sp.error)})
            # Requests that land mid-load are kept, not refused: the engine
            # thread picks them up once the model is warm, and newest-wins
            # means only the latest answer actually gets spoken.

            text = (payload.get("text") or "").strip()
            if not text:
                return self._reply(400, {"error": "no text"})
            try:
                voice, kwargs = voice_lib.resolve(
                    payload.get("voice") or sp.state.get("voice"),
                    payload.get("source") or sp.state.get("source"), sp.state)
            except LookupError as exc:
                return self._reply(404, {"error": str(exc)})

            pieces = voice_lib.chunks(text)
            if not pieces:
                return self._reply(400, {"error": "nothing speakable"})
            # An explicit request through the API is the user asking for this
            # now, so it takes the floor.
            sp.submit(Job(pieces, voice["id"], kwargs), barge=True)
            log(f"speak [{voice['id']}] {len(pieces)} chunk(s): {text[:60]}...")
            return self._reply(202, {"queued": len(pieces), "voice": voice["id"]})

        self._reply(404, {"error": f"no route {route}"})

    do_GET = do_POST


class SingleServer(ThreadingHTTPServer):
    # HTTPServer sets SO_REUSEADDR, which on Windows lets a *second* process
    # bind a port that is already in use instead of failing. Two engines would
    # mean two model loads and everything said twice, so refuse the steal.
    allow_reuse_address = False
    daemon_threads = True


def main():
    state = voice_lib.load_state()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=state["port"])
    args = ap.parse_args()

    if voice_lib.server_alive(args.port):
        log(f"another engine already owns port {args.port}; exiting")
        return

    try:
        httpd = SingleServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        log(f"cannot bind port {args.port} ({exc}); another engine is probably up")
        return

    Handler.speaker = Speaker(state)
    if state.get("watch", True):
        TranscriptWatcher(Handler.speaker).start()
        Handler.watching = True
    log(f"listening on http://127.0.0.1:{args.port} (pid {os.getpid()})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log("shutting down")


if __name__ == "__main__":
    main()
