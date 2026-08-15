"""
Keeps one Qwen talker model warm and speaks whatever is POSTed to it.

Loading the model takes the better part of a minute, which is fine once and
unbearable per sentence -- so the engine lives here, in a long-running process,
behind a tiny HTTP API on localhost:

    POST /speak   {"text": "...", "voice": "sibylla", "source": "embedding"}
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
    def submit(self, job):
        """Newest utterance wins: a fresh answer cuts off the previous one."""
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
        while True:
            job, path = self.play_q.get()
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


class Handler(BaseHTTPRequestHandler):
    speaker = None
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
            return self._reply(200, sp.status())

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
            sp.submit(Job(pieces, voice["id"], kwargs))
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
    log(f"listening on http://127.0.0.1:{args.port} (pid {os.getpid()})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log("shutting down")


if __name__ == "__main__":
    main()
