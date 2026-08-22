"""
Keeps one Qwen talker model warm and speaks whatever is POSTed to it.

Loading the model takes the better part of a minute, which is fine once and
unbearable per sentence -- so the engine lives here, in a long-running process,
behind a tiny HTTP API on localhost:

    POST /speak   {"text": "...", "voice": "abby", "source": "embedding"}
    POST /stop    stop talking now, drop anything queued
    POST /pause   hold it where it is, keeping the place; no argument toggles
    POST /health  {"ready": true, "speaking": false, ...}
    POST /quit    shut the process down

The panel (panel.py) drives the rest, and owns no state of its own -- it draws
whatever /state last said and turns every click into one of these:

    POST /state         now playing, queue, history, sessions, voices
    POST /enabled       {"on": false} -- the master switch, as 'voice off' does
    POST /pause         {"on": true} -- hold everything, losing nothing
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
import array
import collections
import ctypes
import itertools
import json
import os
import queue
import sys
import tempfile
import threading
import time
import wave
import winsound
from ctypes import wintypes
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


# Around 1500 messages before the oldest is dropped, at roughly 300 bytes each.
# Enough to see a bad afternoon in with room to spare, and small enough that
# nobody has to think about it.
TRACE_BYTES = 500_000


def _trace(mode, lead, text, curve):
    """One line per message: when each piece of audio arrived, and how much.

    That curve is the only machine-dependent thing in the whole underrun
    question, and it is enough to work out afterwards -- exactly, not by feel --
    what lead this machine would have needed. Which beats picking a threshold
    now and discovering next month that it was wrong. 'playback report' reads
    it back.

    Lengths and timings only. Nothing of what was said is written here, and
    this file never leaves the machine.
    """
    if not curve:
        return
    try:
        os.makedirs(voice_lib.LOG_DIR, exist_ok=True)
        path = os.path.join(voice_lib.LOG_DIR, "playback-trace.jsonl")
        row = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
               "lead": lead, "chars": len(text),
               "expected": round(voice_lib.expected_seconds(text), 2),
               "audio": curve[-1][1], "curve": curve}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        if os.path.getsize(path) > TRACE_BYTES:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines[len(lines) // 2:])
    except (OSError, ValueError) as exc:
        # A trace nobody can write is not a reason to stop talking.
        log(f"could not write the playback trace: {exc}")


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
                 "when", "cancelled", "stalled")
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
        # How many times the player ran dry part way through this message. Not
        # used to decide anything yet -- it is here so that "it breaks up on my
        # work laptop" can be a measurement instead of an impression.
        self.stalled = 0

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
        # Held, not off. The model stays loaded, the watcher goes on queueing,
        # and the piece in the air is cut where it got to so it can be picked
        # up from there. Set means playing: an engine that came back from a
        # restart already silent would read as a fault rather than as a
        # setting, so this lives here and dies with the process.
        self.resume = threading.Event()
        self.resume.set()
        self.spoken = 0
        self.underruns = 0            # times the player ran dry, all messages
        # Measured seams: how long the silence between two pieces of one
        # message really lasts. Costed at 0.2s once, on the reasoning that
        # TAIL was all of it; halving the piece size on that sum made the
        # voice stumble twice as often, so it is measured now.
        self.seams = collections.deque(maxlen=200)
        self._said_mode = None        # last playback mode written to the log
        # (rate, shrink) for the last few messages: how fast this machine
        # made speech, and how much of expected_seconds turned out real.
        self.recent = collections.deque(maxlen=self.LEARN_FROM)
        self._seed_learning()
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

    @property
    def paused(self):
        return not self.resume.is_set()

    def pause(self, on=None):
        """Hold what is being said, or pick it up where it stopped.

        Not the master switch and not a skip: nothing is turned off and nothing
        is thrown away. The engine keeps its model, the watcher goes on
        queueing, and the piece in the air is cut at the sample it reached so
        the same words can carry on from there.

        Whatever is waiting stays waiting, which is the point -- and if that is
        not what you want when you come back, the skip-everything button beside
        it empties the queue. "Quiet now" and "I never want to hear that" are
        two different wishes, and one button could only ever grant one of them.

        No argument toggles, because a toggle is the only thing a media key can
        say.
        """
        want = (not self.paused) if on is None else bool(on)
        if want == self.paused:
            return want
        if want:
            self.resume.clear()
        else:
            self.resume.set()
        log("paused" if want else "resumed")
        return want

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
        # Asking to hear something is asking to hear it, so it lifts a hold
        # rather than queueing up behind one.
        self.pause(False)
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
        self.pause(False)        # and "now" is not "once you press play"
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
            "paused": self.paused,
            "queued": self.jobs.qsize() + self.play_q.qsize(),
            "spoken": self.spoken,
            "underruns": self.underruns,
            "seam": round(self.seam_typical(), 3),
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

    # How far back a resume starts from the moment the pause landed. A pause
    # almost never falls on a word boundary, and coming back on the exact
    # sample means coming back mid-syllable, which is heard as a fault rather
    # than as a continuation. A fifth of a second gives the word its start
    # back. It is also why holding and resuming the same spot over and over
    # walks slowly backwards, which is the right direction to be wrong in.
    REWIND = 0.2

    def _tail(self, path, offset):
        """A copy of a chunk from `offset` seconds in. None if nothing is left.

        winsound plays a file from its beginning and has no notion of a
        position, so resuming where a pause landed means handing it a shorter
        file. The audio is mono 16-bit PCM at a rate the header carries --
        write_wav made it -- so this is a seek and a copy: nothing is decoded,
        and nothing here has to agree with the engine about anything.
        """
        dest = os.path.join(CACHE_DIR, f"held-{os.getpid()}-{time.time_ns()}.wav")
        try:
            with wave.open(path, "rb") as src:
                rate = src.getframerate()
                start = min(int(offset * rate), src.getnframes())
                left = src.getnframes() - start
                if left <= 0:
                    return None
                src.setpos(start)
                frames = src.readframes(left)
                with wave.open(dest, "wb") as out:
                    out.setnchannels(src.getnchannels())
                    out.setsampwidth(src.getsampwidth())
                    out.setframerate(rate)
                    out.writeframes(frames)
        except (OSError, wave.Error) as exc:
            log(f"could not cut the held piece: {exc}")
            _unlink(dest)
            return None
        return dest

    def _play(self, job, path):
        """Play one chunk, and come back the instant it is cancelled.

        Asynchronously, and deliberately so. A synchronous PlaySound cannot be
        interrupted at all: a purge from another thread does not cut it, it
        queues up behind it and returns once the clip has finished of its own
        accord -- which quietly made 'stop' mean 'stop after this sentence',
        measurably so on a long one. Played async, the same purge cuts within a
        twentieth of a second, and the wav's own header says how long to wait
        for one that nobody cuts.

        A pause is that same purge with the place kept. `at` is how far into
        this chunk the sound had got when it landed, and what plays on the way
        back is a copy of the rest -- cut while the hold is still on, so that
        pressing play is a PlaySound and not a file write.

        Returns the moment the audio ended, or 0.0 if it never got there. Not
        a bare yes: a chunk that was held for five minutes and then finished
        did finish, but `began` plus its length is five minutes in the past,
        and the seam meter reading that as a six-second gap between pieces is
        exactly what it looked like the first time this was tried.
        """
        at, ended = 0.0, 0.0
        piece, cut = path, False      # what is playing, and whether it is ours
        while True:
            if not self.resume.is_set():
                self.speaking = False
                # Waited in slices rather than in one go: skipping a line while
                # it is held has to take the line away now, not whenever play
                # is next pressed -- which might be an hour, or never.
                while not self.resume.is_set() and not job.cancelled:
                    self.resume.wait(0.1)
                self.speaking = True
            if job.cancelled:
                break
            winsound.PlaySound(piece, winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
            length = _wav_seconds(piece)
            began = time.monotonic()
            end = began + length + self.TAIL
            while time.monotonic() < end:
                if job.cancelled:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    break
                if not self.resume.is_set():
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    # Where the audio got to, not where the clock did: the last
                    # loop of a chunk is waiting out TAIL as well, and counting
                    # that would resume a fifth of a second past the end.
                    at = max(0.0, at + min(time.monotonic() - began, length)
                             - self.REWIND)
                    break
                time.sleep(0.05)
            else:
                ended = began + length     # when this chunk's audio ran out
                break
            if job.cancelled:
                break
            if cut:
                _unlink(piece)
            piece, cut = self._tail(path, at), True
            if piece is None:
                break             # the pause landed on the last few samples
        if cut and piece:
            _unlink(piece)
        return ended

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
            # Read the config again here rather than trusting the snapshot this
            # process started with. Playback mode is the one setting somebody
            # changes *because* the voice is breaking up, and "restart the
            # engine first" means waiting a minute to find out whether it
            # helped. One small JSON read per message costs nothing beside a
            # generation.
            live = self._live()
            try:
                mode = self._mode(live)
                if live.get("streaming", True) and self._stream(eng, job, mode):
                    continue
                self._chunked(eng, job)
            finally:
                with self.lock:
                    if self.current is job:
                        self.current = None

    def _live(self):
        """The config as it is now, not as it was when this process started.

        Only for the few settings a running engine can honour without reloading
        anything -- the model paths in here are already loaded and changing them
        means a restart whatever this returns. A read that fails falls back to
        the startup snapshot: a config caught half-written is not a reason to
        stop talking.
        """
        try:
            return voice_lib.load_state()
        except Exception:
            return self.state

    def _mode(self, live):
        """Which playback mode to use, said in the log whenever it changes.

        Logged on change rather than per message -- it is the answer to "is it
        actually using the setting I picked", and a typo in config.json shows up
        here as the only place that would ever mention it.
        """
        want = str(live.get("playback") or "instant").strip().lower()
        mode = want if want in self.PLAY_LEAD else "instant"
        if want != self._said_mode:
            self._said_mode = want
            log(f"playback: {mode}" if mode == want else
                f"playback: {mode} (there is no mode called '{want}')")
        return mode

    def _learn(self, expected, audio, wall):
        """Keep what one message managed: its rate, and how long it really ran.

        Both are needed. The rate says how far behind the speaker synthesis is;
        the second says how much of expected_seconds() to believe, which on the
        machine this was written on overstates by about a third. A lead worked
        out from an overstated length is simply a longer wait than necessary.
        """
        if wall > 0 and audio > 0 and expected > 0:
            self.recent.append((audio / wall, audio / expected))

    def _seed_learning(self):
        """Read the last few messages back off the trace at startup.

        Otherwise every restart begins by learning the machine again, and it
        learns it from the hiccup it was meant to prevent.
        """
        try:
            path = os.path.join(voice_lib.LOG_DIR, "playback-trace.jsonl")
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()[-self.LEARN_FROM:]
        except OSError:
            return
        for line in lines:
            try:
                row = json.loads(line)
                self._learn(row["expected"], row["audio"],
                            row["curve"][-1][0] / 1000.0)
            except (ValueError, KeyError, IndexError, TypeError):
                continue
        if self.recent:
            log(f"playback: {len(self.recent)} past messages remembered")

    def _auto_lead(self, text):
        """The lead voice_lib.auto_lead works out, said in the log.

        The sum itself lives there so that 'playback report' can run the very
        rule the engine runs over the traces, rather than a second copy of it
        that drifts.
        """
        lead, rate, seconds = voice_lib.auto_lead(
            voice_lib.expected_seconds(text), self.recent, self.FIRST_SECONDS)
        if rate is not None and lead > self.FIRST_SECONDS:
            log(f"auto: banking {lead:.0f}s of about {seconds:.0f}s "
                f"(this machine at {rate:.2f}x realtime)")
        return lead

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

    # How much audio to gather per playable piece once past the lead. Large,
    # because every piece is a seam and a chance for the next one to be late.
    #
    # Getting from the lead up to here the size doubles rather than jumping.
    # It used to jump, and the arithmetic of that never worked: a 2.5-second
    # first piece buys 2.5 seconds in which to make a twelve-second second
    # piece, and twelve seconds of audio needs three to make even at four
    # times realtime. So there was a gap about two seconds into every message,
    # on a machine with nothing else to do. Doubling means each piece pays for
    # the next one rather than for one five times its size, and it costs
    # nothing -- the first word still arrives on the first 2.5 seconds.
    UNIT_SECONDS = voice_lib.PIECE_SECONDS

    # How much audio each playback mode wants banked before the first word --
    # the lead that a stall in synthesis is spent from instead of being heard.
    # None means all of it: nothing plays until the generation has finished.
    #
    # 'instant' is small because it is the only thing standing between the
    # answer arriving and the first word being heard. 'buffered' is fifteen
    # seconds because that covers the whole ramp above in one go, so the early
    # small pieces -- the ones with the least time to spare -- are never the
    # ones the player is waiting on.
    PLAY_LEAD = {name: lead for name, lead, _ in voice_lib.PLAYBACK_MODES}
    FIRST_SECONDS = PLAY_LEAD["instant"]

    LEARN_FROM = voice_lib.LEARN_FROM

    # Past this a seam is worth a line in the log rather than just a number.
    #
    # This was 0.40, which is above every seam this ever measures: the number
    # is the handover alone -- from the end of one file to the start of the
    # next -- and that is TAIL plus a little, so the line could never fire. It
    # sat above its own subject while the room was hearing gaps of up to 0.88s,
    # because the rest of those gaps was silence inside the audio, which this
    # does not see and now does not need to: quiet_span takes it out at the
    # cut. So the threshold moves down to just above an ordinary handover,
    # where it can actually report a machine falling behind.
    SEAM_WORTH_SAYING = 0.30

    def seam_typical(self):
        """The middle seam, which is the one to design against."""
        if not self.seams:
            return 0.0
        ordered = sorted(self.seams)
        return ordered[len(ordered) // 2]

    def _stream(self, eng, job, mode="instant"):
        """Speak the whole job as ONE generation, played as it is made.

        This is the fix for the voice changing between sentences. Several
        generations are several subtly different speakers, because the model
        rolls its prosody afresh each time; one generation keeps two seconds of
        what it has already said as context for the next piece, so the voice is
        continuous by construction. It is also quicker to the first word --
        measured 812 ms against 1967 ms for the first of two chunks.

        Playback still has to be cut somewhere, since winsound plays a file at a
        time. The cuts are placed in silence, which is why they are not heard.

        `mode` decides only *when* the first piece is handed over, never how
        the audio is made -- it is one generation either way, and turning that
        off would bring the changing voice back. What it buys is a lead: audio
        already written that the player can spend while synthesis is stalled,
        which is the whole of what goes wrong on a busy machine. 'whole' takes
        that to its end and hands over one file, after which a gap is not
        unlikely but impossible.

        Returns True if it spoke. False means nothing was played and the caller
        should fall back -- never True-ish half measures, or the fallback would
        say the first half of the answer twice.
        """
        text = " ".join(job.chunks).strip()
        if not text:
            return True
        expect = voice_lib.expected_seconds(text)
        lead = self.PLAY_LEAD.get(mode, self.FIRST_SECONDS)
        if lead == "auto":
            lead = self._auto_lead(text)
        # An array rather than a list, and that is not housekeeping. ctypes
        # hands back real Python floats -- twenty-four bytes of object each,
        # plus the pointer -- so a five-minute answer held whole would be a
        # quarter of a gigabyte, on the machine that was short of room to begin
        # with. As 'f' it is four bytes a sample and every line below reads it
        # unchanged.
        buf, spoken = array.array("f"), [0.0]
        head = [False]                # has the opening dead air been dropped yet
        unit = [self.FIRST_SECONDS if lead is None else lead]
        # When each piece arrived and how much audio existed by then. This
        # curve is the only machine-dependent thing in the whole question,
        # and from it the lead this machine actually needed can be worked
        # out exactly, afterwards -- see 'playback report'.
        began, made, curve = time.monotonic(), [0], []

        def on_piece(samples, _chunk):
            buf.extend(samples)
            made[0] += len(samples)
            curve.append((int((time.monotonic() - began) * 1000),
                          round(made[0] / SAMPLE_RATE, 3)))
            if job.cancelled:
                return False
            # Dead air before the first word is latency and nothing else, and
            # the engine leaves a different amount of it every time. Drop it
            # once, at the top, for every mode -- 'whole' included, since it
            # waits long enough already.
            if not head[0]:
                drop = voice_lib.trim_head(buf, SAMPLE_RATE)
                head[0] = drop < len(buf)
                if drop:
                    del buf[:drop]
            # 'whole': hand over nothing until the generation has finished, so
            # there is no next piece to be late and no gap to hear. The flush
            # below plays it as a single file.
            if lead is None:
                return True
            if len(buf) / SAMPLE_RATE < unit[0]:
                return True
            # The handover is silence the player supplies whether we want it or
            # not, so it is spent as part of the pause rather than added to it.
            cut, resume = voice_lib.quiet_span(buf, SAMPLE_RATE,
                                               budget=self.TAIL)
            if not resume:
                # No breath to cut on yet. Gathering more is right: it moves the
                # seam to the next natural pause rather than into a word. Only
                # a genuine run-on gets cut regardless, and then only so that
                # one endless sentence cannot hold up playback for ever.
                if len(buf) / SAMPLE_RATE < unit[0] * 2.5:
                    return True
                cut = resume = len(buf)
            # cut can be 0 with resume past it: the buffer opens on a pause we
            # are dropping whole. Nothing to play, but the silence still goes.
            if cut:
                if not self._emit(job, buf[:cut]):
                    return False
                spoken[0] += cut / SAMPLE_RATE
                unit[0] = min(self.UNIT_SECONDS, unit[0] * 2)
            del buf[:resume]
            return True

        try:
            try:
                eng.synthesize_streaming(text, on_piece,
                                         max_seconds=voice_lib.ceiling_seconds(text),
                                         **job.kwargs)
            except Exception as exc:
                if spoken[0] > 0:
                    # Already speaking, so there is no going back to the old
                    # road without repeating what was heard. Keep what we have.
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
                log(f"  {verdict}: {spoken[0]:.1f}s of audio for {len(text)} "
                    f"characters, where {expect:.1f}s is honest")
            return True
        finally:
            if curve:
                self._learn(expect, curve[-1][1], curve[-1][0] / 1000.0)
            _trace(mode, lead, text, curve)

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
        # When the audio of the previous piece actually ran out -- not when
        # _play came back, which is TAIL later. The difference between that
        # and the next piece starting is the seam, and it is the thing heard
        # as a stumble mid-phrase: the cut is placed in a gap between words,
        # which is quiet but is not a full stop, and stretching one of those
        # sounds wrong in a way that stretching a full stop does not.
        last, audio_ended = None, 0.0
        while True:
            # Held before the queue is touched, not after: a piece taken out of
            # it and kept in here would vanish from the panel, which counts
            # what is waiting by looking in the queue.
            self.resume.wait()
            job, path, keep = self.play_q.get()
            # A beat between one message and the next. The seam is useful --
            # it is how you hear that a new line has started rather than the
            # same one continuing -- so make it deliberate instead of leaving
            # it to whatever gap the synthesiser happens to leave.
            if last is not None and job is not last:
                time.sleep(self.state.get("gapSeconds", 0.45))
            last_played, last = last, job
            played = False
            try:
                if not job.cancelled:
                    with self.lock:
                        self.playing = job
                    self.speaking = True
                    if job is last_played and audio_ended:
                        seam = time.monotonic() - audio_ended
                        self.seams.append(seam)
                        if seam > self.SEAM_WORTH_SAYING:
                            log(f"seam of {seam:.2f}s between pieces "
                                f"({len(self.seams)} timed, "
                                f"{self.seam_typical():.2f}s typical)")
                    # When the audio ran out, straight from the player: a
                    # chunk that was held part way through ends long after
                    # began-plus-its-length says, and a seam measured from
                    # there is the length of the pause, not of a gap.
                    audio_ended = self._play(job, path)
                    self.spoken += 1
                    played = True
                    # Did the player just run out of things to play while this
                    # message was still being made? Then the next word is late
                    # and the gap is being heard right now. Recorded rather
                    # than guessed at, because "it breaks up under load" and
                    # "this machine needs a longer lead" are the same sentence
                    # only if somebody counted.
                    if (not job.cancelled and self.play_q.empty()
                            and self.current is job):
                        self.underruns += 1
                        if job.stalled == 0:
                            log("playback ran dry mid-message -- synthesis is "
                                "behind the speaker. A longer lead would cover "
                                "it: set playback to buffered, or whole.")
                        job.stalled += 1
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


# The play/pause key: on a keyboard's media row, on the button on a pair of
# headphones, on the little remote halfway down a cable. All three send this
# one virtual key, which is what makes it worth answering -- it is the button
# already under your thumb at the moment you want her to stop.
VK_MEDIA_PLAY_PAUSE = 0xB3
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000         # a held-down key is one press, not forty
HOTKEY_ID = 0xC1A0            # ours alone, and only within this thread


class MediaKey(threading.Thread):
    """Answer the play/pause key, but only while there is something to answer.

    RegisterHotKey takes a key away from every other program on the machine for
    as long as it is held, and play/pause is not ours to keep: it belongs to
    whatever is playing. So it is taken while she is speaking, holding a queue,
    or paused -- and handed straight back the moment she is idle, which is when
    it belongs to Spotify again. Pressing it with nothing to say therefore does
    what it always did, and nothing in here needs to know what that was.

    The register and the message loop have to be the same thread: Windows
    delivers WM_HOTKEY to the thread that asked for the key and to no other, so
    a hotkey registered from a thread that never reads its own queue is a key
    taken away from the machine and then answered by nobody.

    What this cannot promise: a keyboard whose media keys are handled by its
    own driver -- some Logitech and Corsair software does this -- never lets
    the key reach Windows at all, and there is nothing on this side to be done
    about that. The panel button is the same action either way.
    """

    POLL = 0.25
    # The setting is a file read and this loop runs four times a second.
    SETTING_EVERY = 8

    def __init__(self, speaker):
        super().__init__(name="mediakey", daemon=True)
        self.speaker = speaker
        self.held = False           # do we currently own the key
        self.allowed = True
        self.ticks = 0
        self.complained = False

    def _wanted(self):
        """Is there anything here for the key to do?"""
        if self.ticks % self.SETTING_EVERY == 0:
            self.allowed = bool(self.speaker._live().get("mediaKey", True))
        self.ticks += 1
        sp = self.speaker
        # Being made counts as much as being played. There are three or four
        # seconds between a message arriving and its first word, and asking
        # only about `speaking` left the key with Spotify for all of them --
        # so the first press of a new answer went to the wrong program.
        #
        # Paused counts too, and counts most: that is the state whose whole
        # purpose is the press that ends it.
        return bool(self.allowed and (sp.paused or sp.speaking
                                      or sp.playing or sp.current
                                      or sp.jobs.qsize() or sp.play_q.qsize()))

    def run(self):
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        while True:
            want = self._wanted()
            if want != self.held:
                if want:
                    self.held = bool(user32.RegisterHotKey(
                        None, HOTKEY_ID, MOD_NOREPEAT, VK_MEDIA_PLAY_PAUSE))
                    if self.held:
                        self.complained = False
                    elif not self.complained:
                        # Once per spell of failing, not once every quarter
                        # second: this is a thing to notice, not a thing to
                        # drown the log in.
                        self.complained = True
                        log("media key: play/pause is held by something else, "
                            "so it stays with it")
                else:
                    user32.UnregisterHotKey(None, HOTKEY_ID)
                    self.held = False
            # Peek rather than Get: this thread has to come back to the question
            # above, and GetMessage would sit on the key until one arrived --
            # which, on an idle machine, is never.
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.speaker.pause()
            time.sleep(self.POLL)


def _tidy_label(label, words=6):
    """Short enough to hear as a name rather than a sentence."""
    if not label:
        return None
    label = label.replace("_", " ").replace("-", " ").strip()
    parts = label.split()
    if len(parts) > words:
        parts = parts[:words]
    return " ".join(parts).rstrip(".,:;")


class _LastSpeaker:
    """Who was heard last -- one record, for every path into the speakers.

    A single voice says the lot: the sessions the watcher follows, and anything
    posted to /speak by the panel or the CLI. So the note of which name was
    last announced has to be single too. Kept per-path it only ever compared
    sessions against sessions, and stepping from a session to a line typed by
    hand and back passed without a word -- the panel's column said whose it was
    and the room could not tell.
    """

    def __init__(self):
        self.lock = threading.Lock()      # the watcher thread and HTTP workers
        self.name = None

    def prefix(self, project, state):
        """The name to say in front of this line, or "" to just say the line.

        Only when it actually changes: announcing every line would be worse
        than the confusion it is fixing.

        The project, never the conversation's title. 'qwen voices' says where
        you are in two words, while a generated title is a whole sentence, and
        two conversations open on the same project are still that one project:
        switching between them is not news and should pass without a word.
        Anything other than 'off' means the project, so a stale setting from an
        older config cannot quietly bring the titles back.

        The first line after a restart is never announced -- there is nothing
        for it to be a change from. A line belonging to no project at all
        leaves the record standing rather than clearing it, so an anonymous
        aside between two lines of one project does not make the second
        announce itself again.
        """
        project = _tidy_label(project)
        with self.lock:
            announced = ""
            if (state.get("sessionLabel", "project") != "off" and project
                    and self.name is not None and self.name != project):
                announced = f"{project}. "
            # Followed even with labelling off, so turning it back on says the
            # next *change* instead of naming whoever happens to speak first.
            self.name = project or self.name
            return announced


_last_speaker = _LastSpeaker()


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


# How a transcript says that nobody is sitting in front of it. Claude Code
# stamps every user entry with the entrypoint that wrote it: an interactive
# session says 'cli' or 'claude-desktop', while a run started headless --
# `claude -p`, or anything driving the SDK -- says 'sdk-cli'. Read off real
# transcripts against Claude Code 2.1.229; if a future version spells it
# differently the worst that happens is those runs speak again.
HEADLESS_ENTRYPOINTS = ("sdk-cli",)


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
        self.headless = {}      # transcript -> was it started with nobody there
        self.hushed = set()     # headless ones already said to be skipped
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
        state = voice_lib.load_state()
        out = []
        for path, _size, mtime in rows:
            if len(out) >= limit:
                break
            label = self.labels[path] if path in self.labels else self._ensure_label(path)
            # A row you cannot usefully tick: it is silent for a reason of its
            # own, and offering to un-mute it would be a lie.
            if self._unattended(path, state):
                continue
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
            if self._unattended(path, state):
                continue
            for line in chunk.splitlines():
                self._consider(line, state, path)
        self._save_offsets()

    def _unattended(self, path, state):
        """A run with nobody in front of it, which we have been told to skip.

        The offset has already moved on by the time this is asked, so turning
        the setting on later starts speaking the *next* thing such a run says
        rather than reciting everything it said while unheard.
        """
        if state.get("watchHeadless", False) or not self.headless.get(path):
            return False
        # Once per session rather than once per sweep: silence with no trace
        # reads as the voice having broken again, which is the one failure this
        # project keeps re-learning -- but a line every 0.7 seconds is its own
        # kind of nothing.
        if path not in self.hushed:
            self.hushed.add(path)
            log(f"watcher: headless run <{self.labels.get(path)}>, staying quiet")
        return True

    def _ensure_label(self, path):
        """What to call this session out loud, read once from the transcript.

        A title the user set wins over the one Claude generated; failing both,
        the project folder. Titles arrive as their own entries and can appear
        before we start following a file, so the whole file is scanned once.
        """
        if path in self.labels:
            return self.labels[path]
        custom = ai = cwd = entrypoint = None
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    cwd = cwd or e.get("cwd")
                    entrypoint = entrypoint or e.get("entrypoint")
                    if e.get("type") == "custom-title":
                        custom = e.get("customTitle") or custom
                    elif e.get("type") == "ai-title":
                        ai = e.get("aiTitle") or ai
        except OSError:
            pass
        # Which project this session is in. Two sessions can carry near-enough
        # the same title in different repos, and then the title alone tells you
        # nothing about which one is talking.
        # Decided here because this is the one place the whole file is read,
        # and the answer never changes for a given session.
        self.headless[path] = entrypoint in HEADLESS_ENTRYPOINTS
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
        # 'narrate' covers the short lines said while work is going on, and the
        # watcher is handed no events to tell those from a finished answer --
        # but the shape of the message says it plainly. Text sitting alongside
        # tool calls is a line said before doing something; text on its own ends
        # the turn. That is the same cut the hook makes between PreToolUse and
        # Stop, and without it 'narrate off' silenced only the hook and left the
        # watcher to say the very same line -- a switch that looked broken
        # because it did nothing, rather than because it did the wrong thing.
        mid_work = any(isinstance(b, dict) and b.get("type") == "tool_use"
                       for b in blocks)
        if text.strip():
            if mid_work and not state.get("narrate", True):
                log(f"watcher: narration off, not saying {text.strip()[:40]}...")
            else:
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

        # Two projects talking through one voice are impossible to tell
        # apart, so say which one. Whether it is a change from the last one is
        # not the watcher's to judge -- see _LastSpeaker, which the API path
        # asks as well.
        label = self.labels.get(path)
        announced = _last_speaker.prefix(self.projects.get(path), state)

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

        if route == "/pause":
            # Not the master switch. Nothing is turned off, nothing is dropped,
            # and the engine keeps its model -- so this is not written to the
            # config either. Sent with no argument it toggles, which is all a
            # media key is able to say.
            want = payload.get("on")
            held = sp.pause(None if want is None else bool(want))
            return self._reply(200, {"paused": held})

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
            # Out of the same speakers as everything else, so it wears the same
            # name in front when the speaker has changed. Judged against the
            # setting as it stands now, not as it stood at startup: the watcher
            # reads it fresh every sweep, and one shared record cannot honour
            # two different answers to whether labelling is on.
            announced = _last_speaker.prefix(payload.get("project"), sp._live())
            if announced:
                # Chunked with the name attached, exactly as the watcher does
                # it, so the name rides the first piece and no other.
                pieces = voice_lib.chunks(announced + text) or pieces
            # An explicit request through the API is the user asking for this
            # now, so it takes the floor -- unless it says otherwise. Text
            # typed into the panel asks to be queued instead: it is a line to
            # add to what is waiting, not a reason to throw the rest away.
            #
            # The project is passed through because the panel draws a column of
            # them, and something typed by hand belongs to no folder; saying so
            # is better than an empty cell nobody can account for.
            sp.submit(Job(pieces, voice["id"], kwargs, text=text,
                          session=payload.get("session"),
                          project=payload.get("project")),
                      barge=not payload.get("queue"))
            log(f"speak [{voice['id']}] {len(pieces)} chunk(s): "
                f"{'<' + announced.strip() + '> ' if announced else ''}{text[:60]}...")
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

    # A pull brings in a new speaking-notes.md and installs nothing, so the note
    # a session reads at startup would go on teaching last month's rules until
    # somebody ran setup.ps1 by hand. An update restarts the engine anyway, so
    # this is the one place that reliably runs after a pull and before the next
    # session begins. Costs two small reads when there is nothing to do.
    if voice_lib.sync_notes(state=state):
        log("speaking notes in CLAUDE.md were out of date; refreshed")

    Handler.speaker = Speaker(state)
    # Started whatever the setting says: it reads it itself, every couple of
    # seconds, so turning it on does not need the engine restarting.
    MediaKey(Handler.speaker).start()
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
