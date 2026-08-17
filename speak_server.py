"""
Keeps one Qwen talker model warm and speaks whatever is POSTed to it.

Loading the model takes the better part of a minute, which is fine once and
unbearable per sentence -- so the engine lives here, in a long-running process,
behind a tiny HTTP API on localhost:

    POST /speak   {"text": "...", "voice": "abby", "source": "embedding"}
    POST /stop    stop talking now, drop anything queued
    POST /health  {"ready": true, "speaking": false, ...}
    POST /quit    shut the process down

The panel (panel.py) drives the rest, and owns no state of its own -- it draws
whatever /state last said and turns every click into one of these:

    POST /state         now playing, queue, history, sessions, voices
    POST /enabled       {"on": false} -- the master switch, as 'voice off' does
    POST /skip          drop this line, keep the queue
    POST /play          say the current line again from its start
    POST /replay-id     {"id": 16} -- play kept audio, synthesising nothing
    POST /mute-session  {"path": "...jsonl", "muted": true}
    POST /set-voice     {"voice": "max"}
    POST /volume        {"level": 0.6} -- 0 to 1, and audible mid-sentence

Two threads do the work. The engine thread owns the QwenEngine and never lets
anyone else touch it -- a JNIEnv pointer belongs to the thread that made the
JVM, so calling generate() from an HTTP worker would be undefined behaviour.
It synthesises chunk by chunk and hands finished wavs to the player thread,
which is plain winsound and knows nothing about JNI. That split is also what
makes speech start on the first sentence instead of the last.
"""

import argparse
import collections
import itertools
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
import win_volume
from qwen_engine import SAMPLE_RATE, Engine, write_wav

CACHE_DIR = os.path.join(tempfile.gettempdir(), "claude-voice")
# Played wavs move here instead of being deleted, so hearing something again
# costs nothing. Only the last historyKeep utterances are kept.
HISTORY_DIR = os.path.join(CACHE_DIR, "history")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# voice_lib cannot log on its own, and the one thing it does silently -- cutting
# the end off a long message -- is the thing most often mistaken for the engine
# failing. Give it somewhere to say so.
voice_lib.notify = log


def set_volume(level):
    """How loud this process is allowed to be. Returns the level asked for.

    Windows keeps the slider, not us -- see win_volume for why -- and a machine
    that will not hand it over is no reason to stop speaking, so a failure is
    logged and the level is still remembered for the next start.
    """
    level = win_volume.clamp(level)
    try:
        win_volume.set_level(level)
    except Exception as exc:
        log(f"could not set the volume: {exc}")
    return level


class Job:
    """One utterance: the chunks it was split into, and where it came from.

    The metadata is not decoration. The panel draws its queue and its history
    out of these fields, and saying a line again needs to know both its words
    and whose voice said them.
    """

    __slots__ = ("id", "chunks", "voice", "kwargs", "text", "session", "project",
                 "when", "cancelled")
    _ids = itertools.count(1)

    def __init__(self, chunks, voice, kwargs, text=None, session=None, project=None):
        self.id = next(Job._ids)
        self.chunks = list(chunks)
        self.voice = voice
        self.kwargs = kwargs
        # What was said, without the session name the watcher may have prefixed
        # -- the label is shown in its own column rather than read as the line.
        self.text = text if text is not None else " ".join(self.chunks)
        self.session = session
        self.project = project
        self.when = time.time()
        self.cancelled = False

    def as_dict(self):
        return {
            "id": self.id,
            "text": self.text[:200],
            "session": self.session,
            "project": self.project,
            "voice": self.voice,
            "when": time.strftime("%H:%M", time.localtime(self.when)),
        }


class Speaker:
    def __init__(self, state):
        self.state = state
        self.jobs = queue.Queue()
        self.play_q = queue.Queue(maxsize=2)
        self.lock = threading.Lock()
        self.current = None           # being synthesised
        self.playing = None           # actually coming out of the speakers
        self.history = collections.deque()
        self.hist_lock = threading.Lock()
        self.ready = threading.Event()
        self.error = None
        self.speaking = False
        self.spoken = 0
        os.makedirs(CACHE_DIR, exist_ok=True)
        # Before anything can play: Windows remembers a level per application,
        # so without this the voice comes back at whatever the last run -- or
        # a hand in the mixer -- left it at.
        set_volume(state.get("volume", 1.0))
        # Last run's wavs are orphans: the deque that knew which utterance each
        # one belonged to died with that process.
        _empty_dir(HISTORY_DIR)
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
            for job in (self.current, self.playing):
                if job:
                    job.cancelled = True
        self._drain(self.jobs)
        self._drain(self.play_q)
        winsound.PlaySound(None, winsound.SND_PURGE)

    def skip_current(self):
        """Drop the line being spoken and let the queue carry on.

        cancel() empties everything, which is what 'stop' means and not what
        'skip' means: not wanting to hear this line is no reason to throw away
        the three waiting behind it.
        """
        with self.lock:
            job = self.playing or self.current
            if job is None:
                return False
            job.cancelled = True
        held = []
        while True:
            try:
                item = self.play_q.get_nowait()
            except queue.Empty:
                break
            if item[0] is job:
                if not item[2]:
                    _unlink(item[1])
            else:
                held.append(item)
        for item in held:        # never more than was taken out, so it cannot block
            self.play_q.put(item)
        winsound.PlaySound(None, winsound.SND_PURGE)
        return True

    def repeat_current(self):
        """Say the current line again from its start.

        This is the 'play' half of a pause that cannot really pause: winsound
        has no way to resume a wav it stopped, so the honest thing to offer is
        starting the line over rather than pretending to have kept the place.
        """
        with self.lock:
            job = self.playing or self.current
        if job is None:
            with self.hist_lock:
                job = self.history[-1]["job"] if self.history else None
        if job is None or not job.chunks:
            return None
        again = Job(job.chunks, job.voice, job.kwargs, job.text, job.session)
        self.submit(again, barge=True)
        return again

    def replay(self, job_id):
        """Play a kept utterance again, straight from its wavs.

        Nothing is synthesised: the audio is the same audio, so this is
        instant however long the line was.
        """
        with self.hist_lock:
            rec = next((r for r in self.history if r["job"].id == job_id), None)
            wavs = list(rec["wavs"]) if rec else []
        if not wavs:
            return None
        src = rec["job"]
        self.cancel()            # asking for this one is asking for it now
        again = Job(src.chunks, src.voice, src.kwargs, src.text, src.session)
        # The play queue is deliberately short, so feeding it blocks -- and an
        # HTTP handler must not. Hand it to a thread that only ever enqueues.
        threading.Thread(target=self._feed, args=(again, wavs),
                         name="replay", daemon=True).start()
        return again

    def snapshot(self, keep=12):
        """Everything the panel draws, taken in one pass."""
        with self.lock:
            cur = self.playing or self.current
        # Peeking inside a Queue is reaching past its front door, so hold its
        # own mutex while doing it.
        with self.jobs.mutex:
            waiting = list(self.jobs.queue)
        with self.play_q.mutex:
            ahead = [item[0] for item in self.play_q.queue]
        queued, seen = [], {id(cur)}
        for job in ahead + waiting:
            if id(job) in seen:
                continue
            seen.add(id(job))
            queued.append(job.as_dict())
        with self.hist_lock:
            past = [r["job"] for r in self.history]
        history = [j.as_dict() for j in reversed(past) if j is not cur][:keep]
        return {"current": cur.as_dict() if cur else None,
                "queue": queued, "history": history}

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
            if isinstance(item, tuple) and not item[2]:
                _unlink(item[1])         # a history wav is not ours to delete

    # A shade longer than the clip: PlaySound takes a moment to get going, and
    # clipping the tail off every chunk would be heard as a stutter.
    TAIL = 0.2

    def _play(self, job, path):
        """Play one chunk, and come back the instant it is cancelled.

        Asynchronously, and deliberately so. A synchronous PlaySound cannot be
        interrupted at all: a purge from another thread does not cut it, it
        queues up behind it and returns once the clip has finished of its own
        accord -- which quietly made 'stop' mean 'stop after this sentence',
        measurably so on a long one. Played async, the same purge cuts within a
        twentieth of a second, and the wav's own header says how long to wait
        for one that nobody cuts.
        """
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)
        end = time.monotonic() + _wav_seconds(path) + self.TAIL
        while time.monotonic() < end:
            if job.cancelled:
                winsound.PlaySound(None, winsound.SND_PURGE)
                return
            time.sleep(0.05)

    def _feed(self, job, wavs):
        for path in wavs:
            if job.cancelled or not os.path.exists(path):
                return
            self.play_q.put((job, path, True))

    def _keep(self, job, path):
        """Move a chunk that has been heard into the history ring."""
        with self.hist_lock:
            rec = next((r for r in self.history if r["job"] is job), None)
            if rec is None:
                rec = {"job": job, "wavs": []}
                self.history.append(rec)
                limit = max(1, int(self.state.get("historyKeep", 40)))
                while len(self.history) > limit:
                    for wav in self.history.popleft()["wavs"]:
                        _unlink(wav)
            dest = os.path.join(HISTORY_DIR, f"{job.id}-{len(rec['wavs'])}.wav")
            try:
                os.replace(path, dest)
            except OSError:
                return _unlink(path)
            rec["wavs"].append(dest)

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
            try:
                if self.state.get("streaming", True) and self._stream(eng, job):
                    continue
                self._chunked(eng, job)
            finally:
                with self.lock:
                    if self.current is job:
                        self.current = None

    def _emit(self, job, samples):
        """Write one playable piece and hand it to the player. False if we stopped."""
        if job.cancelled:
            return False
        path = os.path.join(CACHE_DIR, f"say-{os.getpid()}-{time.time_ns()}.wav")
        try:
            write_wav(path, samples)
        except Exception as exc:
            log(f"wav write failed: {exc}")
            return False
        if job.cancelled:
            _unlink(path)
            return False
        self.play_q.put((job, path, False))       # blocks while the player is behind
        return True

    # How much audio to get out of the door before worrying about seams, and how
    # much to gather per piece after that. The first is small because it is the
    # only thing between the answer arriving and the first word being heard; the
    # rest are large because every one of them costs a gap in playback.
    FIRST_SECONDS = 2.5
    UNIT_SECONDS = 12.0

    def _stream(self, eng, job):
        """Speak the whole job as ONE generation, played as it is made.

        This is the fix for the voice changing between sentences. Several
        generations are several subtly different speakers, because the model
        rolls its prosody afresh each time; one generation keeps two seconds of
        what it has already said as context for the next piece, so the voice is
        continuous by construction. It is also quicker to the first word --
        measured 812 ms against 1967 ms for the first of two chunks.

        Playback still has to be cut somewhere, since winsound plays a file at a
        time. The cuts are placed in silence, which is why they are not heard.

        Returns True if it spoke. False means nothing was played and the caller
        should fall back -- never True-ish half measures, or the fallback would
        say the first half of the answer twice.
        """
        text = " ".join(job.chunks).strip()
        if not text:
            return True
        expect = voice_lib.expected_seconds(text)
        buf, spoken, unit = [], [0.0], [self.FIRST_SECONDS]

        def on_piece(samples, _chunk):
            buf.extend(samples)
            if job.cancelled:
                return False
            if len(buf) / SAMPLE_RATE < unit[0]:
                return True
            cut = voice_lib.quiet_cut(buf, SAMPLE_RATE)
            if not cut:
                # No breath to cut on yet. Gathering more is right: it moves the
                # seam to the next natural pause rather than into a word. Only
                # a genuine run-on gets cut regardless, and then only so that
                # one endless sentence cannot hold up playback for ever.
                if len(buf) / SAMPLE_RATE < unit[0] * 2.5:
                    return True
                cut = len(buf)
            if not self._emit(job, buf[:cut]):
                return False
            spoken[0] += cut / SAMPLE_RATE
            del buf[:cut]
            unit[0] = self.UNIT_SECONDS
            return True

        try:
            eng.synthesize_streaming(text, on_piece,
                                     max_seconds=voice_lib.ceiling_seconds(text),
                                     **job.kwargs)
        except Exception as exc:
            if spoken[0] > 0:
                # Already speaking, so there is no going back to the old road
                # without repeating what was heard. Keep what we have.
                log(f"streaming stopped after {spoken[0]:.1f}s: {exc}")
                return True
            log(f"streaming unavailable, falling back to chunks: {exc}")
            return False
        if job.cancelled:
            return True
        if buf:
            self._emit(job, buf)
            spoken[0] += len(buf) / SAMPLE_RATE

        verdict = voice_lib.audio_verdict(text, spoken[0])
        if verdict != "ok":
            log(f"  {verdict}: {spoken[0]:.1f}s of audio for {len(text)} characters, "
                f"where {expect:.1f}s is honest")
        return True

    def _chunked(self, eng, job):
        """The old road: a generation per piece of text, joined by playback.

        Kept because streaming reaches past the JNI wrappers into the DLL's own
        C ABI, and a build that does not export the streaming entry points would
        otherwise have no voice at all. It has an audible seam at every piece,
        which is the entire reason streaming exists.
        """
        for i, chunk in enumerate(job.chunks):
            if job.cancelled:
                return
            try:
                samples = self._say(eng, job, chunk)
            except Exception as exc:
                log(f"synth failed on chunk {i}: {exc}")
                return
            if not self._emit(job, samples):
                return

    def _say(self, eng, job, chunk):
        """Synthesise one chunk, and refuse to believe a clip unlike its text.

        The engine returns success for both of its real failures -- a derail
        hands back minutes of babbling, a swallowed line hands back a fraction
        of a second -- so 'did it return' catches neither. Length against text
        catches both, and it is the only check we can make from here, since the
        token ceiling that would stop a derail at the source is not reachable
        through the Kotlin wrapper we call.

        Retrying is cheap and a derail is a dice roll, not a property of the
        text: the same words read cleanly a minute later. At better than four
        times realtime a re-synthesis costs about a second per four seconds of
        speech, and the listener never learns it happened.
        """
        expect = voice_lib.expected_seconds(chunk)
        best = None
        for attempt in (1, 2):
            samples = eng.synthesize(chunk, **job.kwargs)
            seconds = len(samples) / SAMPLE_RATE
            verdict = voice_lib.audio_verdict(chunk, seconds)
            if verdict == "ok":
                if attempt == 2:
                    log(f"  retry was clean ({seconds:.1f}s)")
                return samples
            log(f"  {verdict}: {seconds:.1f}s of audio for {len(chunk)} characters, "
                f"where {expect:.1f}s is honest -- attempt {attempt} discarded")
            if job.cancelled:
                return samples
            if best is None or abs(seconds - expect) < abs(len(best) / SAMPLE_RATE - expect):
                best = samples
        # Twice is not luck. Keep whichever attempt came nearest an honest
        # reading, and cut the tail off it: a derail drifts, so every word
        # before it is good and only what follows is noise.
        ceiling = int(voice_lib.ceiling_seconds(chunk) * SAMPLE_RATE)
        if len(best) > ceiling:
            log(f"  keeping the first {ceiling / SAMPLE_RATE:.1f}s and dropping the rest")
            return best[:ceiling]
        return best

    def _play_loop(self):
        last = None
        while True:
            job, path, keep = self.play_q.get()
            # A beat between one message and the next. The seam is useful --
            # it is how you hear that a new line has started rather than the
            # same one continuing -- so make it deliberate instead of leaving
            # it to whatever gap the synthesiser happens to leave.
            if last is not None and job is not last:
                time.sleep(self.state.get("gapSeconds", 0.45))
            last = job
            played = False
            try:
                if not job.cancelled:
                    with self.lock:
                        self.playing = job
                    self.speaking = True
                    self._play(job, path)
                    self.spoken += 1
                    played = True
            except Exception as exc:
                log(f"playback failed: {exc}")
            finally:
                self.speaking = False
                with self.lock:
                    if self.playing is job:
                        self.playing = None
                if keep:
                    pass                      # already in the ring, and shared
                elif played:
                    self._keep(job, path)     # heard once, so worth keeping
                else:
                    _unlink(path)             # cancelled before it was ever heard


def _tidy_label(label, words=6):
    """Short enough to hear as a name rather than a sentence."""
    if not label:
        return None
    label = label.replace("_", " ").replace("-", " ").strip()
    parts = label.split()
    if len(parts) > words:
        parts = parts[:words]
    return " ".join(parts).rstrip(".,:;")


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _wav_seconds(path, default=6.0):
    """How long a clip runs, from its own header. Cheap, and exact."""
    import wave

    try:
        with wave.open(path) as fh:
            return fh.getnframes() / float(fh.getframerate() or 24000)
    except Exception:
        return default


def _empty_dir(path):
    os.makedirs(path, exist_ok=True)
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                _unlink(entry.path)
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
    OFFSETS = os.path.join(voice_lib.LOG_DIR, "watch-offsets.json")

    def __init__(self, speaker, interval=0.7):
        super().__init__(name="watcher", daemon=True)
        self.speaker = speaker
        self.interval = interval
        # Remembered across restarts. Without this, an engine that dies takes
        # every message written while it was gone with it -- the watcher would
        # resume at the end of the file and never mention them, which is
        # indistinguishable from the voice quietly breaking again.
        self.offsets = self._load_offsets()
        self.dirty = False
        self.labels = {}        # transcript -> what to call that session aloud
        self.projects = {}      # transcript -> the folder it is being run in
        self.last_source = None
        # Sessions the panel has silenced. Kept in config too, so muting one and
        # restarting the engine does not un-mute it behind your back.
        self.muted = set(voice_lib.load_state().get("mutedSessions") or [])

    def _load_offsets(self):
        try:
            with open(self.OFFSETS, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_offsets(self):
        if not self.dirty:
            return
        try:
            os.makedirs(voice_lib.LOG_DIR, exist_ok=True)
            with open(self.OFFSETS, "w", encoding="utf-8") as fh:
                json.dump(self.offsets, fh)
            self.dirty = False
        except OSError:
            pass

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
                if not f.name.endswith(".jsonl"):
                    continue
                st = f.stat()
                if st.st_mtime > cutoff:
                    yield f.path, st.st_size, st.st_mtime

    def sessions(self, limit=10):
        """Who is talking at the moment, newest first, and who is muted.

        Called from HTTP handler threads, so it only reads. Labels are already
        cached by the sweep; a session first seen here is read once and then
        remembered like any other.
        """
        if not os.path.isdir(self.PROJECTS):
            return []
        try:
            rows = sorted(self._transcripts(), key=lambda r: r[2], reverse=True)
        except OSError:
            return []
        out = []
        for path, _size, mtime in rows[:limit]:
            label = self.labels[path] if path in self.labels else self._ensure_label(path)
            out.append({
                "path": path,
                "label": label or f"session {os.path.basename(path)[:8]}",
                "project": self.projects.get(path),
                "muted": path in self.muted,
                "when": time.strftime("%H:%M", time.localtime(mtime)),
            })
        return out

    def set_muted(self, path, muted):
        """Silence one session, or let it speak again."""
        if muted:
            self.muted.add(path)
        else:
            self.muted.discard(path)
        voice_lib.patch_state(mutedSessions=sorted(self.muted))
        log(f"watcher: {'muted' if muted else 'unmuted'} "
            f"{self.labels.get(path) or os.path.basename(path)}")

    def _sweep(self):
        state = voice_lib.load_state()
        # Adopt the config's list each sweep, so editing it by hand works too.
        self.muted = set(state.get("mutedSessions") or [])
        for path, size, _mtime in self._transcripts():
            seen = self.offsets.get(path)
            if seen is None:
                # Never seen this file: start at its end rather than reading a
                # whole conversation aloud. A remembered offset, by contrast, is
                # resumed from -- that is the point of remembering it.
                self.offsets[path] = size
                self.dirty = True
                continue
            if size <= seen:
                self.offsets[path] = min(seen, size)   # truncated or rewritten
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(seen)
                chunk = fh.read()
                self.offsets[path] = fh.tell()
                self.dirty = True
            # Config is re-read every sweep so 'voice off' takes effect at once.
            if not state.get("enabled") or not state.get("watch", True):
                continue
            self._ensure_label(path)
            for line in chunk.splitlines():
                self._consider(line, state, path)
        self._save_offsets()

    def _ensure_label(self, path):
        """What to call this session out loud, read once from the transcript.

        A title the user set wins over the one Claude generated; failing both,
        the project folder. Titles arrive as their own entries and can appear
        before we start following a file, so the whole file is scanned once.
        """
        if path in self.labels:
            return self.labels[path]
        custom = ai = cwd = None
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    cwd = cwd or e.get("cwd")
                    if e.get("type") == "custom-title":
                        custom = e.get("customTitle") or custom
                    elif e.get("type") == "ai-title":
                        ai = e.get("aiTitle") or ai
        except OSError:
            pass
        # Which project this session is in. Two sessions can carry near-enough
        # the same title in different repos, and then the title alone tells you
        # nothing about which one is talking.
        self.projects[path] = os.path.basename(cwd) if cwd else None
        label = custom or ai or self.projects[path]
        self.labels[path] = _tidy_label(label)
        return self.labels[path]

    @staticmethod
    def _too_old(entry, state):
        """Skip a backlog. Catching up after a crash is worth it; reciting what
        was said while the machine was off overnight is not."""
        stamp = entry.get("timestamp")
        if not stamp:
            return False
        try:
            from datetime import datetime, timezone
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - when).total_seconds()
        except ValueError:
            return False
        return age > state.get("catchupSeconds", 300)

    # Not _handle: threading.Thread keeps a _handle attribute of its own on the
    # instance, and it shadows any method of that name.
    def _consider(self, line, state, path):
        line = line.strip()
        if not line:
            return
        try:
            entry = json.loads(line)
        except ValueError:
            return                                  # a half-written final line
        # A session can be renamed mid-flight; keep up with it.
        if entry.get("type") in ("custom-title", "ai-title"):
            new = _tidy_label(entry.get("customTitle") or entry.get("aiTitle"))
            if new:
                self.labels[path] = new
            return
        if entry.get("type") != "assistant" or self._too_old(entry, state):
            return
        msg = entry.get("message") or {}
        if msg.get("role") != "assistant":
            return
        content = msg.get("content")
        blocks = content if isinstance(content, list) else []
        text = content if isinstance(content, str) else "\n".join(
            b.get("text", "") for b in blocks
            if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            speech, what = voice_lib.speech_for(text, state)
            self._say(speech, what, state, path)

        # A question is spoken as its own utterance rather than as a tail on the
        # line before it, so that it matches what the PreToolUse hook says word
        # for word and the dedupe can drop whichever of the two arrives second.
        # Glued onto the narration it would match neither, and be heard twice.
        for b in blocks:
            if (isinstance(b, dict) and b.get("type") == "tool_use"
                    and b.get("name") == "AskUserQuestion"):
                self._say(voice_lib.question_speech(b.get("input"),
                                                    state.get("maxChars", 4000)),
                          "question", state, path)

    def _say(self, speech, what, state, path):
        """Queue one utterance from one session, if it is worth saying at all."""
        if not speech:
            return
        # Checked before the dedupe, not after: a muted session should not be
        # able to use up the record of what was said and silence another
        # session that happens to say the same thing.
        if path in self.muted:
            # Say so in the log. Silence with no trace is indistinguishable
            # from the voice having broken again, and that is the one failure
            # this project keeps having to re-learn.
            return log(f"watcher: muted <{self.labels.get(path)}> {speech[:40]}...")
        # Dedupe on the words themselves, before any session name is added, so
        # the same message is not said twice just because it was announced.
        if voice_lib.already_spoken(speech):
            return
        try:
            voice, kwargs = voice_lib.resolve(state.get("voice"), state.get("source"), state)
        except LookupError as exc:
            return log(f"watcher: {exc}")

        # Two projects talking through one voice are impossible to tell apart,
        # so say which one -- but only when it actually changes. Announcing
        # every line would be worse than the confusion it is fixing.
        #
        # The project, never the conversation's title. 'qwen voices' says where
        # you are in two words, while a generated title is a whole sentence, and
        # two conversations open on the same project are still that one project:
        # switching between them is not news and should pass without a word.
        # Anything other than 'off' means the project, so a stale setting from
        # an older config cannot quietly bring the titles back.
        label = self.labels.get(path)
        project = _tidy_label(self.projects.get(path))
        announced = ""
        if (state.get("sessionLabel", "project") != "off" and project
                and self.last_source is not None and self.last_source != project):
            announced = f"{project}. "
        self.last_source = project or self.last_source

        pieces = voice_lib.chunks(announced + speech)
        if pieces:
            job = Job(pieces, voice["id"], kwargs, text=speech, session=label,
                      project=self.projects.get(path))
            self.speaker.submit(job)                                # queued, never barging
            log(f"watcher: {what} [{voice['id']}] {len(pieces)} chunk(s) "
                f"{'<' + announced.strip() + '> ' if announced else ''}{speech[:40]}...")


_VOICES = {"when": 0.0, "rows": []}


def _voice_list(state, ttl=15.0):
    """The catalogue, rebuilt occasionally rather than on demand.

    The panel asks twice a second and a hundred voices is a hundred directory
    reads; nobody adds a voice that fast.
    """
    now = time.monotonic()
    if now - _VOICES["when"] > ttl or not _VOICES["rows"]:
        try:
            _VOICES["rows"] = [{"id": v["id"], "name": v["name"],
                                "culture": v["culture"], "sex": v["sex"]}
                               for v in voice_lib.catalog(state)]
        except OSError:
            _VOICES["rows"] = []
        _VOICES["when"] = now
    return _VOICES["rows"]


class Handler(BaseHTTPRequestHandler):
    speaker = None
    watcher = None
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

        if route == "/state":
            # Everything the panel draws, in one round trip. It owns no state
            # of its own; this is the whole of what it knows.
            state = voice_lib.load_state()
            return self._reply(200, {
                **sp.status(), **sp.snapshot(),
                "watching": Handler.watching,
                "sessions": Handler.watcher.sessions() if Handler.watcher else [],
                "voices": _voice_list(state),
                "voice": state.get("voice"),
                "source": state.get("source"),
                "volume": win_volume.clamp(state.get("volume", 1.0)),
                "enabled": bool(state.get("enabled")),
            })

        if route == "/stop":
            sp.cancel()
            return self._reply(200, {"stopped": True})

        if route == "/enabled":
            # The master switch, same as 'voice on' and 'voice off': the setting
            # itself, and silence now if it is going off. The watcher re-reads
            # the config every sweep, so it takes effect within the second.
            on = bool(payload.get("on"))
            voice_lib.patch_state(enabled=on)
            if not on:
                sp.cancel()
            log(f"voice turned {'on' if on else 'off'}")
            return self._reply(200, {"enabled": on})

        if route == "/volume":
            level = payload.get("level")
            if not isinstance(level, (int, float)):
                return self._reply(400, {"error": "no level"})
            # Applied first, remembered second: the point of a slider is the
            # sentence you can already hear getting quieter as you drag it.
            level = set_volume(level)
            voice_lib.patch_state(volume=level)
            return self._reply(200, {"volume": level})

        if route == "/skip":
            return self._reply(200, {"skipped": sp.skip_current()})

        if route == "/play":
            job = sp.repeat_current()
            return self._reply(200, {"playing": job.as_dict() if job else None})

        if route == "/replay-id":
            jid = payload.get("id")
            job = sp.replay(jid) if isinstance(jid, int) else None
            if job is None:
                return self._reply(404, {"error": f"nothing kept for id {jid}"})
            log(f"replay {jid}: {job.text[:60]}...")
            return self._reply(200, {"playing": job.as_dict()})

        if route == "/mute-session":
            if not Handler.watcher:
                return self._reply(409, {"error": "not following sessions"})
            path = payload.get("path") or ""
            if not path:
                return self._reply(400, {"error": "no path"})
            muted = bool(payload.get("muted"))
            Handler.watcher.set_muted(path, muted)
            return self._reply(200, {"path": path, "muted": muted})

        if route == "/set-voice":
            try:
                voice, announced = voice_lib.set_voice(payload.get("voice"))
            except LookupError as exc:
                return self._reply(404, {"error": str(exc)})
            log(f"voice set to {voice['id']}")
            return self._reply(200, {"voice": voice["id"], "name": voice["name"],
                                     "announced": announced})

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
            sp.submit(Job(pieces, voice["id"], kwargs, text=text,
                          session=payload.get("session")), barge=True)
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
        Handler.watcher = TranscriptWatcher(Handler.speaker)
        Handler.watcher.start()
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
