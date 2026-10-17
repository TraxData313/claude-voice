"""
Keeps one speech model warm and speaks whatever is POSTed to it.

Loading a model takes the better part of a minute, which is fine once and
unbearable per sentence -- so the engine lives here, in a long-running process,
behind a tiny HTTP API on localhost. docs/api.md is the whole of it, written
for a program that wants to talk through it:

    POST /speak         {"text": "(laugh) ...", "voice": "abby", "mood": "sad"}
    POST /capabilities  what the next line can do: moods, sounds, engines
    POST /stop          stop talking now, drop anything queued
    POST /pause         hold it where it is, keeping the place; no argument toggles
    POST /health        {"ready": true, "speaking": false, ...}
    POST /quit          shut the process down

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

Two threads do the work. The engine thread owns the engine and never lets
anyone else touch it -- for Qwen a JNIEnv pointer belongs to the thread that
made the JVM, so calling generate() from an HTTP worker would be undefined
behaviour.
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
from qwen_engine import ERROR_BLOCKED_BY_POLICY, SAMPLE_RATE, BlockedByPolicy, write_wav

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


def rss_mb():
    """This process's working set, or None where it cannot be asked.

    Only used to write down what a switch actually cost. The engines are swapped
    so that two models are never resident at once, and a number in the log is
    the difference between that being true and being believed.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        # The argtypes are not optional. GetCurrentProcess returns the
        # pseudo-handle -1, and left to guess, ctypes declares the result an
        # int and then refuses to pass it where a HANDLE belongs -- which
        # failed silently here and printed no memory at all.
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        info = ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        info.restype = wintypes.BOOL

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        if not info(kernel32.GetCurrentProcess(),
                    ctypes.byref(counters), counters.cb):
            return None
        return counters.WorkingSetSize / (1024 * 1024)
    except Exception:                                   # noqa: BLE001
        return None


def build_engine(state, progress=None):
    """Whichever engine the config asks for, loaded and ready to speak.

    All three are held to the same three calls -- load_models,
    synthesize_streaming, synthesize -- so everything below this line is
    written once and does not know which one it got. The difference lives in
    what voice_lib.resolve puts in a job's kwargs, and that is handed through
    without being read. What each can be asked for beyond the words is
    voice_lib.ENGINE_CAN.

    The voice is warmed here rather than on the first answer. Pocket TTS pays
    about two seconds the first time it meets a voice, while it fetches that
    speaker's state, and paying it during "engine ready" is invisible where
    paying it in front of the first sentence is not.
    """
    which = voice_lib.engine_of(state)
    if which == "pocket":
        import pocket_engine

        # The voice decides which of the six models to load, so ask what the
        # current one needs before loading anything. Getting this from the
        # config instead would load English and then immediately swap it for
        # French the moment Estelle said a word.
        warm = {}
        try:
            _, warm = voice_lib.resolve(state.get("voice"),
                                        state.get("source"), state)
        except LookupError:
            pass
        eng = pocket_engine.Engine(
            language=warm.get("pocket_language") or voice_lib.engine_language(state),
            log=log)
        eng.load_models()
        try:
            eng.voice_state(warm.get("pocket_voice") or state.get("voice"),
                            warm.get("pocket_language"))
        except Exception as exc:
            # Not fatal. A voice that cannot be fetched is the next answer's
            # problem, and it says so there with the text in hand; refusing to
            # start over it would take the whole engine down for one bad name.
            log(f"could not warm the voice: {exc}")
        return which, eng

    if which == "breeze":
        import breeze_engine

        # A process of its own, in an environment of its own -- see the top of
        # breeze_engine.py for why. Its output goes to a log beside this one,
        # because a model that fails to come up says why there and nowhere else.
        eng = breeze_engine.Engine(
            state.get("breezePython"), state.get("breezeDir"), state.get("breezeModel"),
            fast=state.get("breezeFast", True), log=log,
            log_path=voice_lib._rolled(os.path.join(voice_lib.LOG_DIR, "breeze-server.log")),
            progress=progress)
        eng.load_models()
        # One short line, thrown away. The first request after a start pays
        # for whatever CUDA sets up lazily -- measured 0.52 s to the first
        # sound cold against 0.20 s warm -- and paying it here is invisible.
        if progress:
            progress("saying a first word", 0.97)
        try:
            _, warm = voice_lib.resolve(state.get("voice"), state.get("source"), state)
            eng.synthesize("Hi.", **warm)
        except Exception as exc:
            log(f"could not warm the voice: {exc}")
        return which, eng

    from qwen_engine import Engine

    eng = Engine(state["studioDir"], verbose=True)
    eng.load_models(state["modelDir"], state["talker"])
    return which, eng


# Where to go when Windows will not let Qwen load, best first. Pocket runs on
# the CPU from a pip package whose DLLs (torch's) carry reputation SAC accepts;
# Breeze is a GPU and a separate eleven-gigabyte install, so it is only ever
# the answer on a machine that already has it.
FALLBACK_ORDER = ("pocket", "breeze")


def fallback_engine(failed, state):
    """The engine to switch to when `failed` cannot load at all, or None."""
    for name in FALLBACK_ORDER:
        if name != failed and _engine_ready(name, state):
            return name
    return None


def _blocked_by_policy(exc):
    """Whether an engine failed because Windows code integrity refused a DLL --
    Smart App Control, or an organisation's WDAC policy. Not something a retry
    or a restart changes."""
    if isinstance(exc, BlockedByPolicy):
        return True
    if getattr(exc, "winerror", None) in ERROR_BLOCKED_BY_POLICY:
        return True
    return "Application Control policy" in str(exc)


def _sentence(exc):
    """An exception as the start of a sentence for the panel: the detail in
    brackets stays in the log."""
    text = str(exc).split(" (")[0].strip() or type(exc).__name__
    return text[0].upper() + text[1:]


class Job:
    """One utterance: the chunks it was split into, and where it came from.

    The metadata is not decoration. The panel draws its queue and its history
    out of these fields, and saying a line again needs to know both its words
    and whose voice said them.
    """

    __slots__ = ("id", "chunks", "voice", "kwargs", "text", "session", "project",
                 "when", "cancelled", "stalled", "instruction", "mood", "engine",
                 "source")
    _ids = itertools.count(1)

    def __init__(self, chunks, voice, kwargs, text=None, session=None, project=None,
                 instruction=None, mood=None, engine=None, source=None):
        self.id = next(Job._ids)
        self.chunks = list(chunks)
        self.voice = voice
        self.kwargs = kwargs
        # Which engine the kwargs were resolved for, and from which source. The
        # engine loaded when this is spoken can differ -- Qwen picked, Windows
        # refuses it, Pocket loads instead -- and Qwen's kwargs mean nothing to
        # Pocket, which then speaks in its own default: Alba, a man. That is how
        # a game's wife came out as a man after every switch (2026-10-17). The
        # engine thread re-resolves the voice when the two disagree.
        self.engine = engine
        self.source = source
        # How it is to be delivered, in words -- "sound daring and brave". Not
        # in kwargs, because kwargs identify a voice and are handed through
        # unread; this one only means anything to an engine that takes it.
        self.instruction = (instruction or "").strip() or None
        # The name the instruction was expanded from, when it came by name --
        # "whisper" -- which is what the panel's narrow column can show.
        self.mood = mood or None
        # What was said, without the session name SessionBook may have prefixed
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

    def again(self, chunks=None):
        """This line as a new job, said again from its start: the same words,
        voice and delivery, under an id of its own."""
        return Job(chunks or self.chunks, self.voice, self.kwargs, self.text,
                   self.session, self.project, self.instruction, self.mood,
                   self.engine, self.source)

    def as_dict(self):
        return {
            "id": self.id,
            "text": self.text[:200],
            "session": self.session,
            "project": self.project,
            "voice": self.voice,
            "instruction": self.instruction,
            "mood": self.mood,
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
        # What is being loaded right now, and how far along: None when nothing
        # is. A Breeze start is anywhere from thirty seconds to three minutes,
        # and a line sitting under a play mark for that long with no word on
        # why reads as a hang (2026-09-30).
        self.loading = None
        # A plain sentence for the panel when the engine had to do something
        # nobody asked for -- today only "Windows blocked Qwen, so this is
        # Pocket". None otherwise. Cleared by the next start that needed no help.
        self.notice = None
        # Set when an engine is picked while none is running, so the engine
        # thread loads it at once instead of waiting for something to be said.
        self.nudge = threading.Event()
        self.speaking = False
        # Which engine is actually loaded, as against which one the config asks
        # for. They differ for as long as it takes the next message to arrive,
        # and the panel draws the difference rather than pretending a switch
        # that has not happened yet did.
        self.engine_name = None
        # Held, not off. The model stays loaded, the hooks go on queueing,
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
        the previous line mid-word is just losing it. So a session's lines queue,
        and only a deliberate 'say this now' (replay, say) interrupts.

        **Nothing barges past a line that is being held.** Cutting the current
        line short is only ever worth it to reach a newer one sooner -- and
        while she is paused nobody is hearing either of them, so the cut buys
        no time at all and costs the sentence you stopped half way through on
        purpose. Worse, cancel() empties the queue as well, so one answer
        arriving took the held line and everything waiting behind it.

        Held means held: it queues, and play works through it in order. The one
        thing that still takes the floor is replay -- clicking a line in the
        history is asking to hear that line now, which is a different sentence
        from anything arriving by itself.

        **Taking the floor never throws away anybody else's line.** It used to
        call cancel(), which is stop: whatever was in the air and whatever was
        waiting, all of it gone. With one hook speaking one answer, that was a
        newer answer replacing an older one. With one voice speaking for every
        session and for Abby's room it is something else. On 2026-09-24 her
        reply arrived four seconds after a session's TL;DR had started being
        made, and took it with it; not a word of that summary was heard. So a
        line that takes the floor still cuts off what is in the air, and still
        replaces whatever its own speaker had said or had waiting. But anyone
        else's line it cuts off is said again straight after it, and anyone
        else's that were waiting keep their places.
        """
        if barge and not self.paused:
            self._take_floor(job)
        else:
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
        is thrown away. The engine keeps its model, the hooks go on
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
        again = job.again()
        # In place of that one line and nothing else: whatever was waiting
        # behind it is still worth hearing once it has been said again.
        self._take_floor(again, replaces=lambda line: line is job)
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
        again = src.again()
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
            "engineLoaded": self.engine_name,
            "loading": self._loading_now(),
            "notice": getattr(self, "notice", None),
            "pid": os.getpid(),
        }

    def _loading_now(self):
        load = self.loading
        if not load:
            return None
        return {"engine": load["engine"], "stage": load["stage"],
                "fraction": load["fraction"],
                "seconds": round(time.monotonic() - load["began"])}

    def _build(self, state):
        """build_engine, with self.loading kept up to date while it runs."""
        want = voice_lib.engine_of(state)
        self.loading = {"engine": want, "stage": "loading the model", "fraction": None,
                        "began": time.monotonic()}

        def progress(stage, fraction=None):
            if self.loading is not None:
                self.loading = {**self.loading, "stage": stage, "fraction": fraction}

        try:
            return build_engine(state, progress)
        finally:
            self.loading = None

    def _start(self, state):
        """_build, and when Windows refuses Qwen, the next engine that works.

        Smart App Control blocking qwen3_tts.dll is not a fault that goes away
        on the next message: it is the same answer every time, until somebody
        turns SAC off (which needs a Windows reinstall to undo). Sitting in an
        error state over it left the voice silent with "java exception during
        CallLongMethod" as the only explanation (2026-10-17). So the switch is
        made, written to the config so the panel and the next start agree with
        it, and said in a sentence -- and picking Qwen again simply tries again.
        """
        try:
            got = self._build(state)
        except Exception as exc:
            if not _blocked_by_policy(exc):
                raise
            want = voice_lib.engine_of(state)
            to = fallback_engine(want, state)
            if to is None:
                self.notice = f"{_sentence(exc)}. No other engine is installed to switch to."
                raise
            log(f"{exc}; switching to {to}")
            try:
                voice_lib.set_engine(to)
                state = self._live()
            except Exception as why:              # a config we cannot write: still speak
                log(f"  could not write the switch to the config: {why}")
                state = {**state, "engine": to}
            got = self._build(state)
            self.notice = f"{_sentence(exc)}; switched to {voice_lib.engine_name(to)}."
            log(f"  {self.notice}")
            return got
        self.notice = None
        return got

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

    def _take_floor(self, job, replaces=None):
        """Put `job` first, cutting off whatever is in the air to get it there.

        The lines it replaces, in the air or waiting, are dropped -- by default
        every line of its own speaker's, since a newer answer replaces an older
        one. Anyone else's line that is cut off comes back straight after it,
        from its start: winsound cannot carry on from where a clip stopped, and
        a generation cut part way leaves nothing to carry on from.

        Both locks are held together, so the engine thread cannot take the next
        line out of the queue between the cut and the new order. Nothing else
        takes them the other way round.
        """
        mine = _tidy_label(job.project)
        if replaces is None:
            def replaces(line):
                return _tidy_label(line.project) == mine
        named = self._live().get("sessionLabel", "project") != "off"
        with self.lock:
            cut = []
            for other in (self.playing, self.current):
                if other is not None and not other.cancelled and other not in cut:
                    other.cancelled = True
                    cut.append(other)
            again = [self._said_again(other, mine, named) for other in cut
                     if not replaces(other)]
            with self.jobs.mutex:
                waiting = [w for w in self.jobs.queue if not replaces(w)]
                self.jobs.queue.clear()
                self.jobs.queue.extend([job, *again, *waiting])
                self.jobs.not_empty.notify()
        self._drain(self.play_q)
        winsound.PlaySound(None, winsound.SND_PURGE)
        # Whoever is last in the queue now is who will have been heard last,
        # and that is no longer the line that just arrived.
        _last_speaker.follow(([job] + again + waiting)[-1].project)
        for other in again:
            log(f"cut off by {mine or 'a line of no project'}, and said again "
                f"after it: {other.text[:40]}...")

    @staticmethod
    def _said_again(job, after, named):
        """A line of someone else's that was cut off, to be said again.

        Chunked afresh from its words so that it can say whose it is: the line
        now in front of it belongs to somebody else, and without its name it
        would be heard as theirs carrying on. Not after a line of no project,
        whose name the room is never told either (see _LastSpeaker), and not
        when names are turned off.
        """
        name = _tidy_label(job.project)
        if named and name and after and name != after:
            return job.again(voice_lib.chunks(f"{name}. {job.text}"))
        return job.again()

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
        eng = None
        try:
            self.engine_name, eng = self._start(self.state)
            log(f"engine ready ({self.engine_name})")
        except Exception as exc:
            # The thread carries on without an engine rather than ending here.
            # It used to return, and then nothing short of a restart brought
            # the voice back -- not even picking an engine that works, which is
            # what anybody does next (an app left naming a Breeze whose install
            # was stopped halfway, 2026-09-25). With no engine in hand, the
            # next message, or the next pick, tries whatever the config names.
            self.error = exc
            self.engine_name = voice_lib.engine_of(self.state)
            log(f"engine failed to start: {exc}")
        self.ready.set()

        while True:
            try:
                job = self.jobs.get(timeout=1.0)
            except queue.Empty:
                # Idle. Nothing to do unless an engine was picked while none
                # was running (see /set-engine): then it loads now, and the
                # voice is back before anything has to be said to find out.
                if not self.nudge.is_set():
                    continue
                self.nudge.clear()
                job = None
            if job is not None:
                with self.lock:
                    self.current = job
            # Changing engine is a config edit like any other, and it takes
            # effect here -- on the next thing said, not on a restart. Loading
            # the other model costs seconds, so doing it when a message arrives
            # rather than when the dropdown moves means the wait lands where
            # somebody is already waiting for speech.
            want = voice_lib.engine_of(self._live())
            if want != self.engine_name or eng is None:
                if eng is None:
                    log(f"starting engine: {want}")
                else:
                    log(f"switching engine: {self.engine_name} -> {want}")
                # Closed before the new one is built, never after. Only one
                # model is ever resident: whichever engine is not speaking is
                # not loaded, and the two numbers in this log say so rather
                # than asking to be believed.
                #
                # What a switch cannot give back is Qwen's JVM and its CUDA
                # context. Those are created once per process and stay for its
                # life -- the model weights go, the runtime around them does
                # not -- so a run that has used Qwen keeps a floor under it.
                before = rss_mb()
                had = eng is not None
                try:
                    if had:
                        eng.close()
                except Exception as exc:
                    log(f"the old engine did not close cleanly: {exc}")
                eng = None
                freed = rss_mb()
                if had and before and freed:
                    log(f"  unloaded {self.engine_name}: "
                        f"{before:.0f} MB -> {freed:.0f} MB")
                try:
                    self.engine_name, eng = self._start(self._live())
                    self.error = None
                    now = rss_mb()
                    log(f"engine ready ({self.engine_name})"
                        + (f", {now:.0f} MB resident" if now else ""))
                except Exception as exc:
                    # Nothing can be spoken now, and the job in hand is lost.
                    # Say so once and keep the thread alive: putting the old
                    # name back means the next message tries the swap again,
                    # which is what somebody fixing their config wants.
                    self.error = exc
                    log(f"{want} failed to start: {exc}")
                    with self.lock:
                        self.current = None
                    continue
            if job is None:
                continue
            # Read the config again here rather than trusting the snapshot this
            # process started with. Playback mode is the one setting somebody
            # changes *because* the voice is breaking up, and "restart the
            # engine first" means waiting a minute to find out whether it
            # helped. One small JSON read per message costs nothing beside a
            # generation.
            live = self._live()
            self._fit_voice(job, live)
            try:
                mode = self._mode(live)
                if live.get("streaming", True) and self._stream(eng, job, mode):
                    continue
                self._chunked(eng, job)
            finally:
                with self.lock:
                    if self.current is job:
                        self.current = None

    def _fit_voice(self, job, live):
        """Re-resolve the job's voice when it was resolved for another engine.

        Same voice id, kwargs for the engine actually loaded. A voice that
        engine does not have keeps the old kwargs -- the engine then speaks in
        its default, as before, and the log says why.
        """
        if not job.engine or job.engine == self.engine_name:
            return
        try:
            _, job.kwargs = voice_lib.resolve(job.voice, job.source,
                                              {**live, "engine": self.engine_name})
            log(f"  [{job.voice}] was meant for {job.engine}; "
                f"re-cast for {self.engine_name}")
        except LookupError as exc:
            log(f"  [{job.voice}] was meant for {job.engine}, and "
                f"{self.engine_name} cannot find it: {exc}")
        job.engine = self.engine_name

    def _kwargs(self, job):
        """What the loaded engine is called with, mood included if it takes one.

        Qwen and Breeze do. Pocket's delivery is the voice and nothing else, and
        handing it a keyword it never declared would raise on the sentence
        rather than ignore it -- so the decision is made here, once, against
        the engine that is actually loaded rather than the one the config
        names.
        """
        if job.instruction and self.engine_name in INSTRUCTED_ENGINES:
            return {**job.kwargs, "instruction": job.instruction}
        return job.kwargs

    def _slack(self, job):
        """How much longer than plain this job may run before it is called a
        derail: more when it carries a mood the loaded engine performs, since a
        mood is often *meant* to slow the reading down."""
        if job.instruction and self.engine_name in INSTRUCTED_ENGINES:
            return voice_lib.INSTRUCTED_SLACK
        return 1.0

    def _performed(self, text):
        """The text as the loaded engine should get it: sounds it makes in its
        own spelling, sounds it cannot make taken out rather than read aloud.
        Against the engine loaded now, like the mood -- the job may have been
        queued before a swap."""
        return voice_lib.perform(text, self.engine_name)

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
        text = self._performed(" ".join(job.chunks).strip())
        if not text:
            return True
        expect = voice_lib.expected_seconds(text)
        slack = self._slack(job)
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
            # as_floats, because array.extend() walks anything but another 'f'
            # array one Python float at a time -- Pocket hands over numpy.
            buf.extend(voice_lib.as_floats(samples))
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
                                         max_seconds=voice_lib.ceiling_seconds(text, slack),
                                         **self._kwargs(job))
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

            verdict = voice_lib.audio_verdict(text, spoken[0], slack)
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
            chunk = self._performed(chunk)
            if not chunk:
                continue                  # nothing but a sound this engine cannot make
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
        slack = self._slack(job)
        best = None
        for attempt in (1, 2):
            samples = eng.synthesize(chunk, **self._kwargs(job))
            seconds = len(samples) / SAMPLE_RATE
            verdict = voice_lib.audio_verdict(chunk, seconds, slack)
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
        ceiling = int(voice_lib.ceiling_seconds(chunk, slack) * SAMPLE_RATE)
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

    A single voice says the lot: the sessions that speak through hooks, and anything
    posted to /speak by the panel or the CLI. So the note of which name was
    last announced has to be single too. Kept per-path it only ever compared
    sessions against sessions, and stepping from a session to a line typed by
    hand and back passed without a word -- the panel's column said whose it was
    and the room could not tell.
    """

    def __init__(self):
        self.lock = threading.Lock()      # HTTP workers, all of them
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

    def follow(self, project):
        """Take the record from the queue's new last line, after a line that
        took the floor put itself first instead of joining the end."""
        project = _tidy_label(project)
        with self.lock:
            self.name = project or self.name


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


# How a session says that nobody is sitting in front of it. Claude Code puts
# its entrypoint in the hook's environment: an interactive session says 'cli'
# or 'claude-desktop', while a run started headless -- `claude -p`, or anything
# driving the SDK -- says 'sdk-cli'. The hook passes it on; see voice_lib.
HEADLESS_ENTRYPOINTS = voice_lib.HEADLESS_ENTRYPOINTS


class SessionBook:
    """The sessions that have spoken to us through their hooks, and what they say.

    This is what replaced the transcript watcher. That thread read every
    Claude Code transcript on the machine every 0.7 s and parsed a file format
    nobody had promised to keep; now Claude Code calls us (speak_hook.py ->
    POST /hook) with what it has, and nothing here opens a file of Claude's.

    A session is keyed by its transcript path, because that is what a hook is
    given, what the panel mutes by, and what mutedSessions in the config has
    always held. A Codex task, which has no such path, is "codex:<thread>".
    Kept in memory: after a restart a session reappears at its next prompt.
    """

    FRESH_SECONDS = 15 * 60      # the panel lists sessions heard from lately

    def __init__(self, speaker):
        self.speaker = speaker
        self.lock = threading.Lock()
        self.rows = {}           # key -> {label, project, when, headless}
        # Sessions the panel has silenced. Kept in config too, so muting one
        # and restarting the engine does not un-mute it behind your back.
        self.muted = set(voice_lib.load_state().get("mutedSessions") or [])

    @staticmethod
    def key_of(body):
        return body.get("transcript_path") or (
            f"session:{body['session_id']}" if body.get("session_id") else None)

    def note(self, body):
        """Remember the session a hook call came from. Returns its key."""
        key = self.key_of(body)
        if not key:
            return None
        cwd = body.get("cwd")
        project = os.path.basename(os.path.normpath(cwd)) if cwd else None
        with self.lock:
            row = self.rows.get(key)
            if row is None:
                row = self.rows[key] = {"label": None, "project": project,
                                        "headless": False}
            row["when"] = time.time()
            row["project"] = project or row["project"]
            row["headless"] = body.get("entrypoint") in HEADLESS_ENTRYPOINTS
            # Named by what it was first asked: a title is Claude Code's to
            # give and no hook is handed one, but the first prompt says what
            # the session is about in the user's own words.
            if not row["label"]:
                prompt = (body.get("prompt") or "").strip()
                if body.get("client") == "codex" and not prompt:
                    prompt = "Codex " + (project or "task")
                row["label"] = _tidy_label(prompt.splitlines()[0] if prompt else None)
        return key

    def sessions(self, limit=10, state=None):
        """Who has spoken lately, newest first, and who is muted. Never the
        unattended ones: offering to un-mute a run that is silent for a reason
        of its own would be a lie."""
        state = state if state is not None else voice_lib.load_state()
        cutoff = time.time() - self.FRESH_SECONDS
        with self.lock:
            rows = sorted(self.rows.items(), key=lambda kv: kv[1]["when"], reverse=True)
        out = []
        for key, row in rows:
            if len(out) >= limit or row["when"] < cutoff:
                break
            if self._unattended(row, state):
                continue
            out.append({
                "path": key,
                "label": row["label"] or row["project"] or f"session {os.path.basename(key)[:8]}",
                "project": row["project"],
                "muted": key in self.muted,
                "when": time.strftime("%H:%M", time.localtime(row["when"])),
            })
        return out

    def set_muted(self, key, muted):
        """Silence one session, or let it speak again.

        Edited from the config as it is now, under its lock, rather than from
        self.muted: a hook adopting an older copy of the list at the same
        moment would otherwise have this write drop the change."""
        with voice_lib._ConfigLock():
            state = voice_lib.load_state()
            fresh = set(state.get("mutedSessions") or [])
            if muted:
                fresh.add(key)
            else:
                fresh.discard(key)
            state["mutedSessions"] = sorted(fresh)
            voice_lib.save_state(state)
        with self.lock:
            self.muted = fresh
        row = self.rows.get(key) or {}
        log(f"sessions: {'muted' if muted else 'unmuted'} "
            f"{row.get('label') or os.path.basename(key)}")

    @staticmethod
    def _unattended(row, state):
        return bool(row.get("headless")) and not state.get("watchHeadless", False)

    def hook(self, body, state):
        """One hook call: what, if anything, to say for it. Returns the reply.

        Every decision the old hook and the old watcher each made for
        themselves, made once, here.
        """
        event = body.get("event")
        # Adopt the config's list each call, so editing it by hand works too.
        muted = set(state.get("mutedSessions") or [])
        with self.lock:
            self.muted = muted
        key = self.note(body)
        row = self.rows.get(key) or {}
        if event in ("SessionStart", "UserPromptSubmit"):
            return {"noted": key is not None}
        if not state.get("enabled"):
            return {"why": "the voice is off"}
        if self._unattended(row, state):
            return {"why": "headless run"}
        # Checked before the dedupe, not after: a muted session must not use up
        # the record of what was said and silence another that says the same.
        # Normalised, because a path typed into the config by hand may differ.
        if key and any(os.path.normcase(os.path.normpath(m)) ==
                       os.path.normcase(os.path.normpath(key)) for m in muted):
            log(f"sessions: muted <{row.get('label')}>")
            return {"why": "this session is muted"}

        if event == "Notification":
            # Above the narration switch: a session stopped and waiting on you
            # is not chatter. Its own switch is 'alerts'.
            if not state.get("alerts", True):
                return {"why": "alerts are off"}
            speech = voice_lib.notification_speech(
                body.get("message"), "", state.get("narrateMaxChars", 240))
            if not speech:
                return {"why": "nothing speakable"}
            if not voice_lib.notification_due(speech):
                return {"why": "just said that"}
            return self.say(speech, "notification", state, key, dedupe=False)

        if event == "PreToolUse" and body.get("tool_name") == "AskUserQuestion":
            # A question halts the turn until it is answered, so somebody with
            # the chatter off needs it more than ever, not less.
            speech = voice_lib.question_speech(body.get("tool_input"),
                                               state.get("maxChars", 4000))
            return self.say(speech, "question", state, key)

        text = body.get("text") or ""
        if not text.strip():
            return {"why": "no text"}
        if event == "PreToolUse":
            if not state.get("narrate", True):
                return {"why": "narration is off"}
            speech, what = voice_lib.speech_for(text, state)
            return self.say(speech, what, state, key)
        if event == "Stop":
            speech, what = voice_lib.speech_for(text, state)
            if what != "summary":
                # A finished answer with no TL;DR is short enough to hear in full.
                speech, what = voice_lib.clean_text(text, state.get("maxChars", 600)), \
                    "whole answer"
            return self.say(speech, what, state, key)
        return {"why": f"nothing to do for {event}"}

    def say(self, speech, what, state, key, dedupe=True):
        """Queue one utterance from one session, if it is worth saying at all."""
        if not speech:
            return {"why": "nothing speakable"}
        # Dedupe on the words themselves, before any session name is added, so
        # the same message is not said twice just because it was announced.
        if dedupe and voice_lib.already_spoken(speech):
            return {"why": f"{what} already spoken"}
        # A session has no field to put a mood in, so it writes one into the
        # line: "(whisper) I found it." Taken out here whatever the engine, and
        # sent beside the words as the whole instruction that name stands for --
        # which the engine thread drops again if what is loaded takes none.
        mood, speech = voice_lib.direction(speech)
        if not speech:
            return {"why": "only a direction"}
        mood, instruction = voice_lib.mood_instruction(mood)
        try:
            voice, kwargs = voice_lib.resolve(state.get("voice"), state.get("source"), state)
        except LookupError as exc:
            log(f"sessions: {exc}")
            return {"why": str(exc)}
        row = self.rows.get(key) or {}
        # Two projects talking through one voice are impossible to tell apart,
        # so say which one -- when it is a change; see _LastSpeaker.
        announced = _last_speaker.prefix(row.get("project"), state)
        # After the project name, before the words: the name says who is
        # speaking and the notice says what they are about to leave out.
        speech, dropped = voice_lib.speakable(speech, state=state)
        if dropped:
            log(f"sessions: {dropped}")
        pieces = voice_lib.chunks(announced + speech)
        if not pieces:
            return {"why": "nothing speakable"}
        job = Job(pieces, voice["id"], kwargs, text=speech, session=row.get("label"),
                  project=row.get("project"), instruction=instruction, mood=mood,
                  engine=voice_lib.engine_of(state), source=state.get("source"))
        self.speaker.submit(job)                                    # queued, never barging
        log(f"sessions: {what} [{voice['id']}] {len(pieces)} chunk(s) "
            f"{'(' + mood + ') ' if mood else ''}"
            f"{'<' + announced.strip() + '> ' if announced else ''}{speech[:40]}...")
        return {"said": what, "queued": len(pieces)}

    def catch_up(self, state):
        """Say what hooks left in the spool while the engine was down."""
        for body in voice_lib.take_spool(state.get("catchupSeconds", 300)):
            try:
                self.hook(body, voice_lib.load_state())
            except Exception as exc:
                log(f"sessions: catching up failed: {exc}")


_VOICES = {"when": 0.0, "rows": [], "engine": None}


def _pocket_ready():
    """Whether the panel may offer the other engine at all.

    Answered once. It is an import-machinery lookup rather than an import, so
    it costs nothing much, but the panel asks twice a second and the answer
    cannot change without the process being restarted anyway.
    """
    if _POCKET["known"] is None:
        import pocket_engine

        _POCKET["known"] = pocket_engine.available()
    return _POCKET["known"]


_POCKET = {"known": None}

# Engines that take a mood alongside the words. Qwen has a field for it in its
# own parameter block and Breeze an instruction beside the text; Pocket has
# nothing of the kind, and its delivery is whatever the voice does. Read off
# voice_lib.ENGINE_CAN, which the panel and the API read as well.
INSTRUCTED_ENGINES = tuple(e for e in voice_lib.ENGINES
                           if voice_lib.engine_can(e, "instruction"))
MAX_INSTRUCTION = 200


def _takes_instruction(state=None):
    """Whether a mood sent with the next line would be used.

    The configured engine rather than the loaded one, because the engine
    thread swaps before it speaks: whatever the config names now is what the
    next sentence comes out of. Reading `engine_name` here would answer about
    the previous line and leave the first one after a swap moodless for no
    reason anybody could see.
    """
    try:
        state = state if state is not None else voice_lib.load_state()
        return voice_lib.engine_of(state) in INSTRUCTED_ENGINES
    except Exception:
        return False


def _events_performed(state=None):
    """The sounds the next line could make, if written into it -- the same
    question as _takes_instruction, asked of the same configured engine."""
    try:
        state = state if state is not None else voice_lib.load_state()
        return list(voice_lib.engine_can(voice_lib.engine_of(state), "events"))
    except Exception:
        return []


# --------------------------------------------------------------------------
# keeping the polled routes cheap
# --------------------------------------------------------------------------
#
# The panel asks for /state twice a second and the game for /health every few,
# with a two- and three-second timeout. Each /state used to read config.json
# three times, walk every Claude transcript folder, and read version.json --
# while the engine thread held the GIL in per-sample loops -- and 31 replies in
# one log were still being written when the caller had given up. The loops
# went to numpy; what is left here is making the routes themselves cost almost
# nothing when nothing has changed.

_CONFIG = {"hit": None}


def _config_now():
    """The config, re-read only when the file has changed since last time.

    Keyed on the file's modification time and size: every writer replaces the
    file whole (voice_lib.save_state), so a change always moves at least one.
    Read-only -- the dict is shared between threads, so nothing may edit it.
    """
    path = voice_lib.CONFIG_PATH
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    hit = _CONFIG["hit"]
    if key is not None and hit is not None and hit[0] == key:
        return hit[1]
    state = voice_lib.load_state()
    _CONFIG["hit"] = (key, state)
    return state


# What this process is running, read once. version.json changes on an update,
# but the code in memory does not until a restart, and the version a caller
# wants is the one that is answering.
_VERSION = {"v": None}


def _own_version():
    if _VERSION["v"] is None:
        _VERSION["v"] = voice_lib.own_version()
    return _VERSION["v"]


# The whole /state reply, kept for a moment. Two panels and a game polling
# together then cost one build between them. Anything that changes what it
# would say -- a button, a config write -- throws it away (see do_POST), so
# the only staleness left is the speaker's own progress, by a quarter second.
STATE_TTL = 0.25
_STATE = {"hit": None}

# Who is talking. Cheap now (a dict in memory), but still cached: every hook
# call is a non-read-only route and throws it away, so a new session shows at
# once and the panel's twice-a-second polls in between cost nothing.
SESSIONS_TTL = 2.0
_SESSIONS = {"hit": None}

# Routes that only read. Any other route may change what /state says.
READ_ONLY_ROUTES = frozenset(("/health", "/state", "/voices", "/storage",
                              "/capabilities"))


def _sessions(book, state):
    if book is None:
        return []
    now = time.monotonic()
    hit = _SESSIONS["hit"]
    if hit is not None and now - hit[0] < SESSIONS_TTL:
        return hit[1]
    rows = book.sessions(state=state)
    _SESSIONS["hit"] = (now, rows)
    return rows


def _forget_polled():
    """Something changed: the next /state is built fresh."""
    _STATE["hit"] = None
    _SESSIONS["hit"] = None


_SIZES = {}


def _folder_bytes(path):
    """How much a folder holds, walked at most once in ten minutes."""
    now = time.time()
    hit = _SIZES.get(path)
    if hit and now - hit[1] < 600:
        return hit[0]
    total = 0
    for here, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(here, name))
            except OSError:
                pass
    _SIZES[path] = (total, now)
    return total


def _storage(state):
    """The folders behind each engine, with their sizes. See the /storage route."""
    def entry(dirs):
        dirs = [d for d in dirs if d and os.path.isdir(d)]
        # Each folder's own size beside the total, so the panel's menu can say
        # that Qwen's model is 2.2 GB of the 3 without walking the disk again.
        sizes = [_folder_bytes(d) for d in dirs]
        return {"dirs": dirs, "bytes": sum(sizes), "sizes": sizes}

    engines = {}
    engines["qwen"] = entry([state.get("studioDir"), state.get("modelDir")])
    breeze_code = state.get("breezeDir") or ""
    engines["breeze"] = entry([os.path.dirname(breeze_code)] if breeze_code else [])
    pocket = []
    try:
        import importlib.util
        spec = importlib.util.find_spec("pocket_tts")
        if spec and spec.origin:
            pocket.append(os.path.dirname(spec.origin))
    except (ImportError, ValueError):
        pass
    hub = os.path.join(os.environ.get("HF_HOME") or os.path.expanduser(r"~\.cache\huggingface"), "hub")
    if os.path.isdir(hub):
        pocket += [os.path.join(hub, d) for d in os.listdir(hub) if d.startswith("models--kyutai--pocket")]
    engines["pocket"] = entry(pocket)
    for name in engines:
        engines[name]["installed"] = bool(voice_lib.engine_ready(name, state))
    app = entry([voice_lib.ROOT])
    return {"app": app, "engines": engines, "engine": voice_lib.engine_of(state)}


def _capabilities(state, loaded):
    """Everything a program speaking through this engine needs to know first.

    One answer rather than several, because a caller like an assistant decides
    once how it is going to write its lines -- whether to put a laugh in, which
    moods to offer itself -- and should not have to piece that together from
    three endpoints. See docs/api.md.
    """
    engine = voice_lib.engine_of(state)
    return {
        "engine": engine,
        "engineLoaded": loaded,
        "voice": state.get("voice"),
        "instruction": engine in INSTRUCTED_ENGINES,
        "maxInstruction": MAX_INSTRUCTION,
        "events": list(voice_lib.engine_can(engine, "events")),
        "eventSyntax": "(laugh)",
        "moods": voice_lib.MOODS if engine in INSTRUCTED_ENGINES else {},
        "moodsHeard": list(voice_lib.MOODS_HEARD),
        "engines": {
            name: {"label": voice_lib.engine_info(name)["label"],
                   "installed": _engine_ready(name, state),
                   "instruction": bool(voice_lib.engine_can(name, "instruction")),
                   "events": list(voice_lib.engine_can(name, "events"))}
            for name in voice_lib.ENGINES},
    }


def _delivery(payload, written=None):
    """(instruction, mood, unknown) for a /speak request.

    An instruction in the caller's own words wins, being the most specific --
    unless all it says is a mood. "whisper this line" is the mood whisper, and
    gets that mood's whole sentence rather than three words that would barely
    move it. Then a mood by name in its own field -- "sad"
    becomes the whole sentence that was measured to work -- and last a mood
    written into the text, which is all a caller with no field of its own can
    do. `unknown` is a name that was asked for and is no mood, so the reply can
    say so rather than leave the caller believing it was heard that way.
    """
    instruction = (payload.get("instruction") or "").strip()[:MAX_INSTRUCTION]
    asked = (payload.get("mood") or "").strip()
    named, preset = voice_lib.mood_instruction(asked)
    unknown = asked if asked and not preset and not instruction else None
    if instruction:
        named, preset = voice_lib.mood_instruction(instruction)
        return (preset, named, unknown) if preset else (instruction, None, unknown)
    if not preset:
        named, preset = voice_lib.mood_instruction(written)
    return preset, (named if preset else None), unknown


def _engine_ready(name, state):
    """voice_lib.engine_ready, never raising: a description is being drawn."""
    try:
        return bool(voice_lib.engine_ready(name, state))
    except Exception:
        return False


def _voice_list(state, ttl=15.0):
    """The catalogue, rebuilt occasionally rather than on demand.

    The panel asks twice a second and a hundred voices is a hundred directory
    reads; nobody adds a voice that fast.
    """
    now = time.monotonic()
    engine = voice_lib.engine_of(state)
    # The engine is part of the key, not just the clock. Switching engines
    # replaces the catalogue wholesale, and waiting out a fifteen-second cache
    # would leave the dropdown offering voices the new engine cannot speak in.
    stale = (now - _VOICES["when"] > ttl or not _VOICES["rows"]
             or _VOICES["engine"] != engine)
    if stale:
        try:
            _VOICES["rows"] = [{"id": v["id"], "name": v["name"],
                                "culture": v["culture"], "sex": v["sex"],
                                "style": v.get("style") or "",
                                "tag": v.get("tag") or "",
                                "added": voice_lib.is_added_voice(v, state)}
                               for v in voice_lib.catalog(state, engine)]
        except (OSError, ImportError, LookupError):
            _VOICES["rows"] = []
        _VOICES["when"], _VOICES["engine"] = now, engine
    return _VOICES["rows"]


class Handler(BaseHTTPRequestHandler):
    speaker = None
    book = None                   # the sessions that speak through hooks
    port_note = None              # a sentence when the engine is not on the port it wanted
    server_version = "ClaudeVoice/1.0"

    def log_message(self, fmt, *args):        # the default logger spams stderr
        pass

    def _reply(self, code, payload):
        return self._reply_body(code, json.dumps(payload).encode("utf-8"))

    def _reply_body(self, code, body):
        # A caller that stopped waiting is not a fault of ours, and each one
        # used to leave a twenty-line traceback in the log (31 of them in one
        # day). The reply is simply not needed any more.
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def do_POST(self):
        """Every route, with a failure to write the config (another process
        holding it past save_state's retries) answered 500 rather than left to
        drop the connection with nothing said."""
        try:
            return self._post()
        except ConnectionError:
            return None                   # the caller has gone; nobody to answer
        except OSError as exc:
            log(f"POST {self.path}: {exc.__class__.__name__}: {exc}")
            try:
                return self._reply(500, {"error": f"could not save the settings: {exc}"})
            except OSError:
                return None

    def _post(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return self._reply(400, {"error": "bad json"})

        route = self.path.split("?")[0].rstrip("/") or "/"
        sp = Handler.speaker

        if route not in READ_ONLY_ROUTES:
            _forget_polled()

        if route == "/health":
            # Asked by everything that wants to know whether we are here, on a
            # short timeout. No disk unless the config changed, no locks beyond
            # the speaker's own.
            state = _config_now()
            return self._reply(200, {**sp.status(),
                                     "version": _own_version(),
                                     # Which copy this is: an installer stopping its own
                                     # engine must not stop a checkout's on the same port.
                                     "root": voice_lib.ROOT,
                                     "port": self.server.server_address[1],
                                     "portNote": Handler.port_note,
                                     "instruction": _takes_instruction(state),
                                     "events": _events_performed(state)})

        if route == "/voices":
            # The whole catalogue for the engine that is speaking, read fresh.
            # /state carries a short list for the panel's dropdown on a cache;
            # a program casting a hundred characters wants the real thing, with
            # each voice's sex and people, and asks rarely enough to afford it.
            state = voice_lib.load_state()
            engine = voice_lib.engine_of(state)
            try:
                rows = voice_lib.catalog(state, engine)
            except (OSError, ImportError, LookupError) as exc:
                return self._reply(200, {"engine": engine, "voices": [],
                                         "error": str(exc)})
            return self._reply(200, {
                "engine": engine,
                "roots": voice_lib.voice_roots(state),
                "voices": [{"id": v["id"], "name": v["name"], "sex": v["sex"],
                            "culture": v["culture"], "style": v.get("style") or "",
                            "tag": v.get("tag") or ""} for v in rows]})

        if route == "/storage":
            # Where each engine keeps its files on this machine, and how much room they take --
            # asked by a game's settings page so a player who downloaded twenty gigabytes can see
            # where they went and what each is for. Sizes are walked once and kept ten minutes.
            return self._reply(200, _storage(voice_lib.load_state()))

        if route == "/voice-roots":
            # A folder of someone else's voices, heard here as they lie. The
            # Immersive AI mod sends its own on every start; see
            # voice_lib.add_voice_root.
            try:
                if payload.get("add"):
                    roots = voice_lib.add_voice_root(payload["add"])
                elif payload.get("remove"):
                    roots = voice_lib.remove_voice_root(payload["remove"])
                else:
                    roots = voice_lib.load_state().get("extraVoicesDirs") or []
            except LookupError as exc:
                return self._reply(404, {"error": str(exc)})
            _VOICES["when"] = 0.0          # the dropdown should see them now
            return self._reply(200, {"extraVoicesDirs": roots})

        if route == "/panel":
            # The window, opened for someone who is not at a command line --
            # a game's "show me the voice app" button. Detached, and a second
            # request raises the one already open rather than adding another.
            voice_lib.start_panel(voice_lib.load_state())
            return self._reply(200, {"panel": True})

        if route == "/capabilities":
            return self._reply(200, _capabilities(voice_lib.load_state(), sp.engine_name))

        if route == "/state":
            # Everything the panel draws, in one round trip. It owns no state
            # of its own; this is the whole of what it knows.
            state = _config_now()
            key = _CONFIG["hit"][0] if _CONFIG["hit"] else None
            now = time.monotonic()
            hit = _STATE["hit"]
            if hit is not None and hit[1] == key and now - hit[0] < STATE_TTL:
                return self._reply_body(200, hit[2])
            body = json.dumps({
                **sp.status(), **sp.snapshot(),
                "sessions": _sessions(Handler.book, state),
                "voices": _voice_list(state),
                "voice": state.get("voice"),
                "source": state.get("source"),
                "engine": voice_lib.engine_of(state),
                "engines": list(voice_lib.ENGINES),
                "pocketReady": _pocket_ready(),
                # Offered either way -- picking it is how the installer is
                # found -- but the panel asks before it switches to it.
                "breezeReady": _engine_ready("breeze", state),
                # Whether a mood sent with a line would actually be used. The
                # config's engine and the loaded engine differ for as long as
                # a swap takes, and a caller deciding whether to offer moods
                # wants the one that is really going to speak.
                "instruction": _takes_instruction(state),
                "events": _events_performed(state),
                "volume": win_volume.clamp(state.get("volume", 1.0)),
                "enabled": bool(state.get("enabled")),
                "version": _own_version(),
                "port": self.server.server_address[1],
                "portNote": Handler.port_note,
            }).encode("utf-8")
            _STATE["hit"] = (now, key, body)
            return self._reply_body(200, body)

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
            # itself, and silence now if it is going off. Every hook call is
            # judged against the config as it is then, so it takes effect at once.
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

        if route == "/hook":
            # What a Claude Code hook (or Codex's notify) was handed, passed on
            # by speak_hook.py. Answered at once: synthesis happens on the
            # engine thread, and the hook is waiting on nothing but this reply.
            # Requests that land mid-load are kept, like /speak's.
            if not isinstance(payload, dict) or not payload.get("event"):
                return self._reply(400, {"error": "no event"})
            return self._reply(200, Handler.book.hook(payload, _config_now()))

        if route == "/mute-session":
            path = payload.get("path") or ""
            if not path:
                return self._reply(400, {"error": "no path"})
            muted = bool(payload.get("muted"))
            Handler.book.set_muted(path, muted)
            return self._reply(200, {"path": path, "muted": muted})

        if route == "/set-voice":
            try:
                voice, announced = voice_lib.set_voice(payload.get("voice"))
            except LookupError as exc:
                return self._reply(404, {"error": str(exc)})
            log(f"voice set to {voice['id']}")
            return self._reply(200, {"voice": voice["id"], "name": voice["name"],
                                     "announced": announced})

        if route == "/set-engine":
            try:
                engine, voice = voice_lib.set_engine(payload.get("engine"))
            except LookupError as exc:
                return self._reply(404, {"error": str(exc)})
            # The model is not loaded here. The engine thread picks the change
            # up when the next message arrives, so that this returns at once
            # and the dropdown does not sit frozen for the length of a load --
            # unless no engine is running at all, when waiting for a message
            # would only leave the window saying "not running" after somebody
            # picked one that works. Then the thread is woken to load it now.
            log(f"engine set to {engine}"
                + (f", voice {voice['id']}" if voice else ""))
            if sp.error is not None:
                sp.nudge.set()
            return self._reply(200, {
                "engine": engine,
                "voice": voice["id"] if voice else None,
                "name": voice["name"] if voice else None,
            })

        if route == "/quit":
            sp.cancel()
            server = self.server

            # Shut down *and close*. shutdown() only stops the accept loop; the
            # listening socket stayed open until the process itself was gone,
            # which with a model loaded can be seconds. Every poll in that gap
            # was accepted by the kernel and then never answered, so the panel
            # sat out its whole timeout instead of being refused at once.
            def stop():
                server.shutdown()
                server.server_close()

            threading.Thread(target=stop, daemon=True).start()
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
            # A mood written into the words as a stage direction, the way a
            # Claude session writes one, or somebody types one into the panel:
            # "(whisper) hello". Taken out whether or not it is used, so that it
            # is never read aloud; see _delivery for what wins.
            written, text = voice_lib.direction(text)
            if not text:
                return self._reply(400, {"error": "nothing to say, only how to say it"})
            # Against the config as it is now, not as it was when this process
            # started. It always mattered a little -- a voice added since would
            # not be found -- and with two engines it matters completely: the
            # startup snapshot names the old engine, so every voice on the new
            # one would come back as a 404 until a restart.
            live = sp._live()
            try:
                voice, kwargs = voice_lib.resolve(
                    payload.get("voice") or live.get("voice"),
                    payload.get("source") or live.get("source"), live)
            except LookupError as exc:
                return self._reply(404, {"error": str(exc)})

            # A caller that would rather hear nothing than the spoken note --
            # a game, where "I skipped forty characters" in the middle of a
            # scene is worse than silence -- asks to be refused instead, and
            # says something of its own on its own screen.
            if payload.get("unreadable") == "refuse" and not voice_lib.can_read(text, sp._live()):
                return self._reply(422, {
                    "error": "the engine speaking now cannot read this alphabet",
                    "unreadable": True, "engine": voice_lib.engine_of(sp._live())})

            # The same guard the hooks' lines get, and for the same reason: a
            # line typed into the panel in Cyrillic would otherwise be handed
            # to an engine that cannot read it.
            text, dropped = voice_lib.speakable(text, state=sp._live())
            if dropped:
                log(f"speak: {dropped}")
            pieces = voice_lib.chunks(text)
            if not pieces:
                return self._reply(400, {"error": "nothing speakable"})
            # Out of the same speakers as everything else, so it wears the same
            # name in front when the speaker has changed. Judged against the
            # setting as it stands now, not as it stood at startup: hook calls
            # read it fresh every time, and one shared record cannot honour
            # two different answers to whether labelling is on.
            announced = _last_speaker.prefix(payload.get("project"), sp._live())
            # "announce": false keeps the record and says nothing. A game has
            # a screen that already shows who is talking, and every character
            # in it would otherwise be introduced as the game's name first.
            # The record is still followed, so the next line from anywhere
            # else is named as the change it is.
            if payload.get("announce") is False:
                announced = ""
            if announced:
                # Chunked with the name attached, exactly as SessionBook.say does
                # it, so the name rides the first piece and no other.
                pieces = voice_lib.chunks(announced + text) or pieces
            # An explicit request through the API is the user asking for this
            # now, so it takes the floor -- unless it says otherwise. Text
            # typed into the panel asks to be queued instead: it is a line to
            # add to what is waiting. Taking the floor throws away only the
            # caller's own lines; anyone else's it cuts off are said after it.
            #
            # The project is passed through because the panel draws a column of
            # them, and something typed by hand belongs to no folder; saying so
            # is better than an empty cell nobody can account for.
            # How it is to be delivered, in the caller's own words. Kept as
            # sent, and dropped by the engine thread if the loaded engine has
            # no use for it -- which is why the reply says what was actually
            # done with it rather than only that it arrived.
            instruction, mood, unknown = _delivery(payload, written)
            sp.submit(Job(pieces, voice["id"], kwargs, text=text,
                          session=payload.get("session"),
                          project=payload.get("project"),
                          instruction=instruction, mood=mood,
                          engine=voice_lib.engine_of(live),
                          source=payload.get("source") or live.get("source")),
                      barge=not payload.get("queue"))
            said_how = mood or instruction
            log(f"speak [{voice['id']}] {len(pieces)} chunk(s): "
                f"{'(' + said_how + ') ' if said_how else ''}"
                f"{'<' + announced.strip() + '> ' if announced else ''}{text[:60]}...")
            # What will actually be done with what was asked for, judged against
            # the engine the next line comes out of -- so a caller learns that a
            # laugh was dropped from the reply rather than from the silence.
            can = _events_performed()
            events = voice_lib.events_in(text)
            reply = {"queued": len(pieces), "voice": voice["id"],
                     "instruction": instruction or None,
                     "instructed": bool(instruction) and _takes_instruction(),
                     "mood": mood,
                     "events": [e for e in events if e in can],
                     "eventsDropped": [e for e in events if e not in can]}
            if unknown:
                reply["warning"] = (f"no mood called '{unknown}'; spoken "
                                    + (f"as '{mood}', from the text. " if mood
                                       else "without one. ")
                                    + "Known: " + ", ".join(voice_lib.MOODS))
            return self._reply(202, reply)

        self._reply(404, {"error": f"no route {route}"})

    do_GET = do_POST


class SingleServer(ThreadingHTTPServer):
    # HTTPServer sets SO_REUSEADDR, which on Windows lets a *second* process
    # bind a port that is already in use instead of failing. Two engines would
    # mean two model loads and everything said twice, so refuse the steal.
    allow_reuse_address = False
    daemon_threads = True


# How many ports past the wanted one to try when another program holds it.
PORT_TRIES = 10


class PortsTaken(RuntimeError):
    """Neither the wanted port nor any of the next PORT_TRIES could be had."""


def bind_port(home, asked=None, make=None, probe=None, owner=None, tries=PORT_TRIES):
    """Bind the engine's port. Returns (server, port, note) or (None, port, None).

    (None, port, None) means an engine of ours already answers on `port`, and
    this one should leave. `note` is a sentence when the engine is not where it
    was asked to be, else None.

    The old answer to a taken port was "cannot bind; another engine is probably
    up", and exit. On 2026-10-15 it was FaceLab's dev server, not an engine, and
    every Claude prompt then spawned a new engine that said the same thing and
    went away (six in the log), while the game showed "port in use". So: a port
    held by something that is ours means leave; held by a stranger means say
    who, and take the next free one -- the config then names it, which is where
    the panel, the hooks and the CLI look, and where.json carries it for
    anything outside this folder. A port that takes the connection and never
    answers is only routed around when what holds it is not a Python: a
    Python there may be our own engine, stuck, and a second engine beside it
    would load a second model and say everything twice.
    """
    make = make or (lambda port: SingleServer(("127.0.0.1", port), Handler))
    probe = probe or voice_lib.port_probe
    owner = owner or voice_lib.port_owner
    order = [home] + ([asked] if asked and asked != home else [])
    order += [home + i for i in range(1, tries + 1) if home + i not in order]
    held = []
    for port in order:
        if port <= 0 or port > 65535:
            continue
        try:
            return make(port), port, (None if port == home else
                                      _moved_note(home, port, held))
        except OSError as exc:
            kind, _health = probe(port)
            if kind == "ours":
                return None, port, None
            if kind == "free":
                # Nobody listening, and still refused: Windows reserves port
                # ranges for Hyper-V and WSL ('netsh int ipv4 show
                # excludedportrange protocol=tcp'), and a reserved one fails
                # exactly like this.
                log(f"port {port} cannot be used ({exc}); Windows may have "
                    f"reserved it. Trying the next one")
                held.append((port, "a Windows reservation"))
                continue
            who = owner(port)
            if kind == "silent" and (not who or who.lower().startswith("python")):
                # Possibly us, stuck. Leaving is the safe answer.
                log(f"port {port} is held by {who or 'something'} that does not "
                    f"answer; if that is an engine of ours it is stuck -- "
                    f"'python voice_cli.py kill' ends it. Not starting a second one.")
                return None, port, None
            log(f"port {port} is in use by {who or 'another program'} ({exc}); "
                f"trying the next one")
            held.append((port, who))
    raise PortsTaken(
        f"ports {order[0]}-{order[-1]} are all in use by other programs "
        f"({', '.join(w or str(p) for p, w in held[:3])}...). "
        f"Set \"port\" in config.json to a free one.")


def _moved_note(home, port, held):
    who = next((w for p, w in held if p == home and w), "another program")
    return f"Port {home} is in use by {who}, so the engine is on {port}."


def main():
    try:
        if voice_lib.old_default_engine(voice_lib._read_config(voice_lib.CONFIG_PATH)):
            voice_lib.patch_state(engine="qwen")
            log("config: no engine named and Qwen's Studio is here -- kept on qwen, the default before 2.0")
    except (OSError, ValueError):
        pass
    state = voice_lib.load_state()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=state["port"])
    args = ap.parse_args()

    # The HTTP threads and the engine thread share one GIL. The default switch
    # interval, 5 ms, is paid by a request every time it gives the GIL up for
    # I/O and wants it back while synthesis is running -- dozens of times per
    # /state. 1 ms costs the engine nothing measurable and the requests a lot.
    sys.setswitchinterval(0.001)

    # The port this copy wants when nothing is in the way: the one it was
    # moved off, if it was moved, else the one it was asked for.
    home = state.get("portHome") or args.port
    for port in dict.fromkeys((args.port, home)):
        if voice_lib.server_alive(port):
            log(f"another engine already owns port {port}; exiting")
            return

    try:
        httpd, port, moved = bind_port(home, args.port)
    except PortsTaken as exc:
        log(f"cannot start: {exc}")
        voice_lib.write_where(state, error=str(exc))
        return
    if httpd is None:
        log(f"another engine already owns port {port}; exiting")
        return

    # Written where every client of this folder looks, before anything slow,
    # so a program looking for this copy finds it even while the model is
    # still loading. Back home again once the port is free: portHome cleared.
    if moved:
        log(moved)
    want = {"port": port, "portHome": home if port != home else None}
    if any(state.get(k) != v for k, v in want.items()):
        try:
            state = voice_lib.patch_state(**want)
        except OSError as exc:
            log(f"could not write the port into the config: {exc}")
            state = {**state, **want}
    Handler.port_note = moved

    voice_lib.write_where(state)
    # And the registry key a game reads first -- only if it already names this
    # folder (installed, or a dev checkout that ran `voice_cli register`).
    if voice_lib.refresh_registration(state):
        log(f"registration: port {port} written to HKCU\\{voice_lib.REG_KEY}")
    Handler.speaker = Speaker(state)
    # Started whatever the setting says: it reads it itself, every couple of
    # seconds, so turning it on does not need the engine restarting.
    MediaKey(Handler.speaker).start()
    # Sessions reach us only through their hooks (POST /hook). Anything a
    # hook left while we were down is said now, if it is still fresh.
    Handler.book = SessionBook(Handler.speaker)
    threading.Thread(target=Handler.book.catch_up, args=(state,), name="catch-up",
                     daemon=True).start()
    log(f"listening on http://127.0.0.1:{port} (pid {os.getpid()})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()        # refuse new connections from here on
        log("shutting down")


if __name__ == "__main__":
    main()
