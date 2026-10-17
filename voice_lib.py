"""
Shared bits: config, the voice catalogue, turning a markdown answer into
something worth hearing, and talking to the speech server.

Imported by speak_server.py (the engine host), speak_hook.py (the hooks)
and voice_cli.py (the switch).
"""

import difflib
import json
import math
import os
import re
import textwrap
import time

# realpath, not abspath: a short name (SOMEON~1) in how this file was reached
# would otherwise be written into hooks and the registry, and compared later
# against the same folder spelt in full.
ROOT = os.path.dirname(os.path.realpath(__file__))


def _install_record():
    """install.json beside this folder, when installer.py put it here.

    An installed copy is <program>/app/ with the record one level up; its
    settings, logs and the voices you make live in a data folder of their own
    (%LOCALAPPDATA%/claude-voice), so an update can replace this folder whole.
    A checkout -- or any copy not laid out that way -- keeps everything beside
    the code, as it always has. See installer.py.
    """
    if os.path.basename(ROOT).lower() != "app":
        return {}
    try:
        with open(os.path.join(os.path.dirname(ROOT), "install.json"), encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) and doc.get("layout") == 1 else {}


INSTALL = _install_record()
# What the registry key, the Run value and the notes folder are called. Always
# "claude-voice" but for a throwaway install a test makes, which must not touch
# the real one's.
PRODUCT = INSTALL.get("product") or "claude-voice"
NOTES_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), PRODUCT)
DATA_DIR = (INSTALL.get("data") or NOTES_DIR) if INSTALL else ROOT
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")


def _unlogged(msg):
    pass


# Whoever imports this and owns a log should point this at it. Nothing in here
# can log on its own, and a cut that nobody records is precisely why a summary
# stopping halfway looks like the engine breaking rather than a limit doing its
# job. The server sets this to its own log; the hook sets it to its trace.
notify = _unlogged

# The playback modes, offered in this order: shortest wait first. The number
# is the lead each one wants banked before the first word, in seconds, and
# None means all of it. The engine reads the numbers, the CLI and the panel
# print the words -- one list, so a mode cannot exist in one of them and not
# in another. See the "playback" default below for what any of it is for.
PLAYBACK_MODES = [
    ("auto", "auto", "works the wait out itself. Best if it breaks up."),
    ("instant", 2.5, "starts the moment there is anything to say."),
    ("buffered", 15.0, "banks about fifteen seconds, then starts."),
    ("whole", None, "makes all of it first, then never breaks up."),
]


# How many recent messages an automatic lead is worked out from, how
# pessimistic to be about them, and how much margin on top.
#
# A low percentile rather than the median, because the number exists to survive
# the bad message and the bad message is the one that gets heard.
#
# Swept against 41 real messages from a machine running at 0.6-0.9x realtime,
# scored walk-forward -- each message judged using only the ones before it, and
# against a simulation of the actual player rather than against the sum. These
# values leave 38 of the 41 without a gap, for 398 seconds of waiting in total;
# banking the whole of every message covers all 41 and costs 892. The three it
# misses are the first three a brand-new machine ever says, before there is
# anything to learn from -- and only ever once, since the last twenty are read
# back off the trace at startup. Raising the safety further bought nothing.
LEARN_FROM = 20
RATE_PERCENTILE = 0.2
LEAD_SAFETY = 1.5

# How much audio goes into one playable piece, once past the lead. The player
# takes whole files, so this is also the granularity of the whole business:
# at every seam it must wait for the *entire* next piece, not for the next
# second of it. That is why it appears in the sum below.
#
# This was briefly 6, on a sum that costed a seam at the 0.2s the player waits
# past the end of a clip. That sum was wrong and the ear caught it: a seam is
# the 0.2s *plus* getting the next file playing, and it lands between two
# ordinary words rather than at a full stop -- the cut is chosen for being
# quiet, and an ordinary gap between words is quiet. Stretching one of those
# is heard as a stumble in the middle of a phrase, and halving the piece size
# doubled how often it happened. Back to 12 until the real cost is measured;
# Speaker._play_loop now times every seam and says so in the log.
PIECE_SECONDS = 12.0

# What one seam is worth, in seconds of waiting avoided. Eight says: do not
# hand me a stumble to save me less than eight seconds.
#
# Weighted this heavily for a reason worth writing down, because it is not
# about the pause being unpleasant. A silence between messages is *deliberate*
# -- gapSeconds, so you can hear that a new line has started rather than the
# same one continuing. A seam in the middle of a line is silence of much the
# same length, in a gap between two ordinary words, and the two are not
# tellable apart. So a seam does not merely interrupt a sentence: it makes you
# lose your place in it, and wonder whether you missed the start of something
# new. Waiting longer at the front costs attention once. A seam costs it every
# time, and costs more of it.
#
# A judgement rather than a measurement, and the only number here that is.
# Speaker times every seam now, so it can become one.
SEAM_COST = 8.0


def auto_lead(expected, recent, floor):
    """How much audio to bank before speaking, from what a machine manages.

    Not a heuristic -- the arithmetic is forced, and it has two halves.

    At a rate R the audio arrives R times as fast as it is heard, so E seconds
    of speech take E/R to make and E to say. Start at once and the difference
    comes out as gaps in the middle; wait that long first and there are none,
    and the message finishes at the same moment either way.

    The second half is the one that cost a day. **The player takes whole files.**
    It cannot begin a piece until all of it is written, so at every seam it is
    waiting for a whole piece to be made rather than for the next second of one.
    Leaving that out is not a small error: it makes the lead pay for exactly one
    seam and no more, which is why the first version of this stalled once in the
    middle of nearly every message while the sums insisted it was fine.

    Requiring no stall at the last seam gives L >= E(1-R) + R*PIECE, and that is
    what this returns. Banking the whole message instead waits E/R -- the same
    silence plus the entire message over again.

    `recent` is (rate, shrink) per past message -- how fast it was made, and how
    much of expected_seconds() turned out to be real, since that overstates by
    about a third and a lead built on an overstated length is a longer wait than
    anyone needed. Returns (lead, rate, seconds); rate is None when nothing has
    been learned yet and the caller is getting the ordinary first piece back.
    """
    if len(recent) < 3:
        return floor, None, expected
    rates = sorted(r for r, _ in recent)
    shrinks = sorted(s for _, s in recent)
    rate = rates[min(len(rates) - 1, int(len(rates) * RATE_PERCENTILE))]
    seconds = expected * shrinks[len(shrinks) // 2]
    if rate >= 1.0:
        return floor, rate, seconds       # it keeps up; nothing to wait for
    # The safety margin belongs on the rate term and nowhere else: that is the
    # half being estimated. The piece term is exactly known, and multiplying it
    # too made a flat fifteen-second floor that quietly turned every message
    # under about forty seconds into 'whole' without ever saying so.
    lead = seconds * (1.0 - rate) * LEAD_SAFETY + rate * PIECE_SECONDS
    # Never more than the message itself -- past that it is simply 'whole' --
    # and never less than the ordinary first piece.
    lead = max(floor, min(seconds, lead))

    # And now the question the clamp above was answering by accident: would
    # banking the lot simply be better? Starting early buys back the waiting,
    # and costs a seam every PIECE_SECONDS. On a short message that is a poor
    # trade -- a few seconds saved for a stumble -- and on a long one it is an
    # obvious one. Deciding it out loud beats falling into it.
    saved = (seconds - lead) / rate
    seams = math.ceil((seconds - lead) / PIECE_SECONDS)
    if saved <= seams * SEAM_COST:
        return seconds, rate, seconds
    return lead, rate, seconds


DEFAULTS = {
    # --- machine-specific, written by install.ps1 -------------------------
    # Where setup.ps1 puts it. Not Program Files, which needs rights nothing here
    # asks for, and not Downloads, which Windows empties after 30 days. config.json
    # is machine-specific and not committed, so this is what a lost one falls back to.
    "studioDir": os.path.expandvars(r"%LOCALAPPDATA%\Programs\qwen-tts-studio"),
    "modelDir": os.path.expanduser(r"~\.qwen-tts-studio\models"),
    "talker": "qwen-talker-1.7b-base-Q8_0.gguf",   # d2048; the 0.6b model gives d1024
    # Voices shipped with this repo. Anything here is public.
    "voicesDir": "voices",
    # Extra folders searched as well -- for voices you may keep but not publish.
    # Same <sex>\<culture>\<id> layout. Never written to, never committed.
    "extraVoicesDirs": [],
    # Breeze TTS 2 lives in an environment of its own -- the CUDA torch it needs
    # is nothing this tool's Python has or should have -- so these name that
    # environment's python.exe, its breeze-tts code and its weights. Empty means
    # not installed: the engine is then offered with an installer in front of
    # it, never downloaded because an update arrived. 'voice_cli.py install
    # breeze' and the panel's installer write them. See docs/engines.md.
    "breezePython": "",
    "breezeDir": "",
    "breezeModel": "",
    # The three decode stages that make it faster than speech. Off starts in
    # twelve seconds instead of about thirty and needs 7.4 GB rather than a
    # 13 GB peak -- and speaks at about half realtime, so only for a card that
    # cannot hold them. See breeze_engine.FAST_FLAGS.
    "breezeFast": True,
    # Qwen on PyTorch, in an environment of its own like Breeze: its python.exe
    # and the Qwen3-TTS Base weights. Where both are there, this is how the
    # Qwen engine runs -- Studio's unsigned DLLs are refused by Smart App
    # Control, these files are not. Written by 'voice_cli.py install qwen' and
    # the panel's installer; empty means not installed. See qwen_server.py.
    "qwenPython": "",
    "qwenModel": "",
    # The code predictor replayed as a CUDA graph: 1.7x realtime instead of
    # 0.6x. Off only to rule it out when something sounds wrong.
    "qwenGraph": True,

    # --- runtime state, written by voice_cli ------------------------------
    "enabled": False,
    # Whether the play/pause key -- on a keyboard's media row, on a pair of
    # headphones -- works the pause button. It is only taken while she has
    # something to pause or resume; the rest of the time the key is left to
    # whatever else is playing, because that is whose key it is.
    "mediaKey": True,
    "voice": "abby",
    "source": "embedding",     # or 'icl': closer clone, larger files
    # Speak an answer as ONE generation, played as it is made, instead of a
    # generation per sentence-group joined at playback. Several generations are
    # several subtly different speakers -- the model rolls its prosody afresh
    # for each -- and that is what is heard as the voice changing mid-answer.
    # It is also quicker to the first word. Off falls back to the old road,
    # which is worth having if a Studio build ever ships without the streaming
    # entry points. See docs/engine-notes.md.
    "streaming": True,
    # How much audio to have in hand before the first word is played.
    #
    # What breaks up on a busy machine is not synthesis, it is the handover:
    # the player takes one file at a time, and if the next one is not finished
    # by the time the current one ends, that gap is heard mid-sentence. It is a
    # buffer underrun, exactly as a video stalls. Synthesis normally runs about
    # four times realtime and the question never comes up; put a heavy Windows
    # job beside it and it falls towards realtime, and then it does.
    #
    #   instant   play the moment there is anything to play. Fastest to the
    #             first word -- 0.8s -- and the one that stalls.
    #   buffered  bank a lead first, so a stall in synthesis is spent from the
    #             buffer instead of being heard. Costs seconds, not gaps.
    #   whole     synthesise the entire message, then play it as a single file.
    #             A gap becomes impossible rather than unlikely; you wait out
    #             the whole synthesis before the first word.
    #   auto      work the lead out per message from what this machine has been
    #             managing lately. On a machine that keeps up it is instant; on
    #             one that does not it waits the difference and no more, which
    #             is a good deal less than whole waits.
    #
    # The seconds behind each are Speaker.PLAY_LEAD. Re-read per message, so
    # changing it needs no restart.
    #
    # auto is the default because on a machine that keeps up it *is* instant --
    # it returns the same 2.5s and nothing is different -- and on one that does
    # not, instant is simply wrong: measured over fifteen real messages on a
    # machine at 0.77x realtime, instant left eight of them with an audible gap.
    "playback": "auto",
    # The ceiling on a TL;DR. This was 600 -- about five sentences -- and it was
    # the wrong kind of limit: it cut at a full stop, so the summary *sounded*
    # finished and simply was not, and nothing said it had happened. A listener
    # who finds an answer long can stop it with a keypress; one who is never
    # told the end was dropped cannot do anything at all. So it now matches the
    # full-answer ceiling and exists only to stop a runaway.
    "maxChars": 4000,
    # How loud, 0.0 to 1.0. This is Windows' own per-app volume -- the slider
    # the mixer keeps under our name -- so it is applied to the engine process
    # rather than mixed into the audio, and takes effect mid-sentence.
    "volume": 1.0,
    # The port the engine listens on, and every local client finds it here.
    # When another program holds it, the engine takes the next free one and
    # writes that here instead, keeping the one it wanted in portHome so it
    # moves back as soon as that is free again (speak_server.bind_port).
    "port": 8765,
    "portHome": None,
    "autostart": True,
    # Speak the short lines said mid-work, not just the final answer. The Stop
    # hook only fires when a turn ends, so this rides PreToolUse instead.
    "narrate": True,
    # Where those mid-work lines come from. No Claude Code hook carries them:
    # Stop is handed the final answer (last_assistant_message), PreToolUse only
    # the tool and its input. The one way to hear them is for the PreToolUse
    # hook to read the end of the session's transcript -- the file Claude Code
    # names in that same hook call, read once, at that moment. Off, because
    # the line Anton drew (2026-10-17) was "hooks only, no scraping": with this
    # off a session hears its answers and alerts and nothing in between. See
    # speak_hook.py.
    "narrateFromTranscript": False,
    # Only a label boundary now: below this a message reads as a passing remark,
    # above it as a spoken answer. Both are read in full.
    "narrateMaxChars": 240,
    # Say something when Claude Code stops and waits on you -- a permission
    # prompt, a question left unanswered too long. Deliberately separate from
    # 'narrate': narration is chatter about work in progress and turning it off
    # is a taste, while these are the moments the session is halted and nothing
    # more happens until somebody looks. Whoever has the chatter off wants this
    # one more than ever, not less. Hook-only -- a notification never reaches a
    # transcript, so the watcher cannot see it. See notification_speech.
    "alerts": True,
    # Speak a thinking block when it is the only thing a response said.
    # Claude Code renders those on screen like any other line, and on Fable 5.1
    # they *are* the narration -- so with this off, roughly half of what you can
    # read goes unsaid. See thinking_speech for which ones qualify and why.
    "speakThinking": True,
    # The ceiling on a message with no TL;DR. Roughly five minutes of speech.
    "fullMaxChars": 4000,
    # Speak the runs nobody is sitting in front of. A headless run -- `claude
    # -p`, an SDK call, an errand one of your own programs sends off -- runs
    # the same hooks as every other session. But its report is addressed to the
    # program that asked for it, not to the room, and hearing one read out in
    # full is how this was found. Off, so they pass in silence; on if you would
    # rather hear yours come home. (The name is older than the hooks-only design.)
    "watchHeadless": False,
    # Pause between one message and the next, so the seam is audible.
    "gapSeconds": 0.45,

    # Name the project when the project changes, and only then. 'off' says
    # nothing at all; anything else names it. Two conversations open on the
    # same project are still that project, and switching between them passes
    # without a word.
    "sessionLabel": "project",
    # An answer that finds the engine down is kept (see spool) and said once it
    # is up -- but not if it is older than this. Catching up after a crash or a
    # cold start, not after a night.
    "catchupSeconds": 300,

    # --- looking for a newer version --------------------------------------
    # Off, and it stays off until somebody says otherwise. Everything else here
    # runs on your machine and tells nobody about it, and a checker that phoned
    # home by default would put an asterisk on that -- which would cost more
    # trust than the convenience is worth. 'voice_cli.py update' works whatever
    # this says, because typing it is the asking. See update_check.py.
    "updateCheck": False,
    # How stale the last look may be before another is made, in days. A hobby
    # project does not release often enough to be worth asking daily.
    "updateCheckDays": 7,

    # --- the panel --------------------------------------------------------
    # How many past utterances keep their audio, so replaying one is instant
    # and costs no synthesis at all.
    "historyKeep": 40,
    # Sessions the panel has muted: a Claude session by its transcript path, as
    # its hooks name it, a Codex task as "codex:<thread id>".
    "mutedSessions": [],
    # Where the panel last sat, written when it closes.
    "panelGeometry": "",
    # Whether it floats over everything else. Its own tick box writes this.
    # Off by default since 1.14: a window that sits on top of everything is a
    # choice to make, not a thing to be handed -- and over a game running in
    # full screen it is simply in the way.
    "panelTopmost": False,
    # Dark colours in the panel. Its own tick box writes this too.
    "panelDark": False,
    "hideGameVoices": False,     # the panel's list leaves out voices a game added
    # Load the engine and turn the voice on as the panel opens -- 'auto start
    # engine', in the panel's settings dialog. Off here, and it stays off until
    # somebody ticks it: opening a window should not quietly take three and a
    # half gigabytes. It is only ever about the panel; the hook's own 'autostart'
    # above is a different question, about an engine that is asked to speak
    # while none is running.
    "panelAutostart": False,
    # How big the speaker's portrait is drawn, in pixels.
    "panelFace": 128,
}


# The last config each path was read as in full, so a read that lands on a
# file another process is half way through replacing has something better to
# fall back on than the defaults. See load_state.
_LAST_GOOD = {}


def _read_config(path):
    """The file's settings, None when it is not there, or raise if unreadable."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    return json.loads(raw) if raw.strip() else {}


def load_state():
    """The config, merged over the defaults.

    Five processes read and write this file -- panel, engine, hook, CLI and the
    game's -- and falling back to the defaults on any failure meant a read
    landing mid-write came back as a *different config*: the default engine,
    the default voice, the voice off. The panel drew that for half a second
    (the dropdown, the portrait and the switch all flicked), and a patch_state
    that read it wrote the defaults back over everyone's settings.

    Writes are atomic now (save_state), so a torn file should not be seen at
    all. A sharing violation can still be, on Windows, for the instant a
    replace is under way: so a failed read is tried again briefly, and if it
    still fails the last good copy is used. Only a file that is genuinely
    missing means "the defaults".
    """
    import time

    path = CONFIG_PATH
    state = dict(DEFAULTS)
    for attempt in range(5):
        try:
            got = _read_config(path)
        except (OSError, ValueError):
            if attempt < 4:
                time.sleep(0.02 * (attempt + 1))
                continue
            got = _LAST_GOOD.get(path)
            if got is None:
                return state
            state.update(got)
            return state
        if got is None:
            _LAST_GOOD.pop(path, None)
            return state
        if isinstance(got, dict):
            _LAST_GOOD[path] = dict(got)
            state.update(got)
        return state
    return state


def save_state(state):
    """Write down only what differs from the defaults.

    Saving the whole merged dict looks harmless and is not: every default gets
    frozen into the file the first time anything is saved, so improving a
    default later never reaches anyone who has already run the tool. That is
    exactly how 'sessionLabel' went on announcing the session title after the
    default had moved to the project name.

    Written beside the file and swapped in with os.replace, the way write_where
    already did it, so nobody can ever read half of it. The swap is retried for
    a moment because Windows refuses it while another process has the old file
    open for reading.
    """
    import time

    lean = {k: v for k, v in state.items() if k not in DEFAULTS or DEFAULTS[k] != v}
    tmp = f"{CONFIG_PATH}.{os.getpid()}.new"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(lean, fh, indent=2)
        fh.write("\n")
    for attempt in range(10):
        try:
            os.replace(tmp, CONFIG_PATH)
            break
        except PermissionError:
            if attempt == 9:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.02 * (attempt + 1))
    _LAST_GOOD[CONFIG_PATH] = dict(lean)


class _ConfigLock:
    """One read-edit-write at a time, across processes.

    Without it two patch_state calls that overlap -- the panel ticking a box
    while the engine records a voice change -- each read the old file and the
    second write undoes the first. A byte-range lock on a file beside the
    config, which Windows drops by itself if the holder dies, so a crash can
    never leave it stuck. If it cannot be had within a couple of seconds the
    write goes ahead anyway: a rare lost update is better than a hang.
    """

    def __enter__(self):
        import time

        self.fh = None
        try:
            self.fh = open(CONFIG_PATH + ".lock", "a+b")
        except OSError:
            return self
        deadline = time.monotonic() + 2.0
        while True:
            try:
                _lock_byte(self.fh, True)
                return self
            except OSError:
                if time.monotonic() > deadline:
                    self.fh.close()
                    self.fh = None
                    return self
                time.sleep(0.01)

    def __exit__(self, *_exc):
        if self.fh is not None:
            try:
                _lock_byte(self.fh, False)
            except OSError:
                pass
            self.fh.close()
        return False


def _lock_byte(fh, take):
    """Take or drop a non-blocking lock on the first byte of an open file."""
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.lockf(fh, (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)


def patch_state(**changes):
    """Change a few keys without writing back a stale copy of the rest.

    Four processes now hold a config in memory -- the engine loaded one when it
    started, the panel polls, the CLI and the hook read one per run -- and
    saving any of those wholesale would quietly undo whatever the others
    changed since. So re-read, edit, write, under a lock so two of those cannot
    interleave.
    """
    with _ConfigLock():
        state = load_state()
        state.update(changes)
        save_state(state)
    return state


# --------------------------------------------------------------------------
# which engine is speaking
# --------------------------------------------------------------------------

# Two roads to sound, and they do not share a voice between them. Qwen's voices
# are folders in voices/ holding an embedding this repo made; Pocket's are
# names the model fetches states for. So the engine decides what the catalogue
# even contains, which is why every caller of catalog() gets it from here
# rather than assuming.
# Pocket since 2026-10-17. It runs on the CPU from a pip package, needs no
# graphics card, and its DLLs (torch's) are signed -- where Qwen's
# qwen3_tts.dll is not, and Windows 11's Smart App Control, on by default on a
# clean install, refuses to load it. Qwen is still one pick away, and the
# engine falls back to Pocket on its own when Windows blocks it (speak_server).
# Only a config that names no engine at all is affected: install.ps1 has
# always written the engine it installed.
DEFAULT_ENGINE = "pocket"
ENGINES = ("qwen", "pocket", "breeze")

# What each engine is, in the few facts somebody choosing between them actually
# needs, plus where to read the rest. It lives here rather than in the panel
# because the panel's tooltip and the CLI's 'engine' both say it, and two
# copies of a description drift apart the first time one of them is edited.
#
# 'blurb' is one paragraph written to be wrapped by whoever shows it, so it
# carries no line breaks of its own.
ENGINE_INFO = {
    # The memory in a label is the most the card was seen holding for it on
    # 2026-09-24 -- what has to be free, which is the question somebody picking
    # from a dropdown is really asking. For Breeze that is its start, which
    # peaks at 13.1 GB and settles at about 9; for Qwen it is 3.3 GB.
    "qwen": {
        "label": "Qwen — GPU 5 GB",
        "blurb": (
            "The original, and the better voice. Runs on an NVIDIA graphics "
            "card with about 5 GB free, through PyTorch -- which works with "
            "Windows' Smart App Control on -- as a download of about 7 GB. "
            "Reads Cyrillic, and every voice in the voices folder was cloned "
            "on it. (Qwen-TTS Studio still works where Smart App Control is off.)"
        ),
        "url": "https://github.com/QwenLM/Qwen3-TTS",
    },
    "pocket": {
        "label": "Pocket TTS — CPU",
        "blurb": (
            "Small and quick. Runs on two CPU cores with no Studio and no "
            "graphics card, and reaches the first word in about a fifth of the "
            "time. Cannot read Cyrillic at all -- those letters are dropped and "
            "you are told so out loud."
        ),
        "url": "https://kyutai.org/blog/2026-01-13-pocket-tts/",
    },
    "breeze": {
        "label": "Breeze 2 — GPU 13 GB",
        "blurb": (
            "The one that laughs. Performs (laugh) and (sigh) written into the "
            "text, and follows a mood given beside the words -- a whisper "
            "whispers. English and Chinese only. A separate download of about "
            "eleven gigabytes; it needs 13 GB free on the graphics card to "
            "start and holds about 9 after, so a 16 GB card. The weights are "
            "for non-commercial use."
        ),
        "url": "https://github.com/breezeblue-ai/breeze-tts",
    },
}

# What each engine can be asked for beyond the words, in one table: the server
# decides from it what to pass through, the panel whether to draw a mood box,
# and the API what a caller is told it may send. Three copies of this would
# disagree the first time an engine learned something new.
#
#   instruction   takes a mood in words beside the text, and performs it
#   events        sounds it makes when one is written into the text, "(laugh)"
ENGINE_CAN = {
    "qwen": {"instruction": True, "events": ()},
    "pocket": {"instruction": False, "events": ()},
    "breeze": {"instruction": True,
               "events": ("laugh", "sigh", "cough", "clears throat")},
}


def engine_can(name, what, state=None):
    """What an engine can do: engine_can("breeze", "events"). Never raises --
    an unknown engine can do nothing, which is the safe answer.

    Qwen's mood depends on how it runs: Studio has a field for one, the Base
    model qwen-tts runs has no instruction input at all. Saying a mood is taken
    when it is dropped would have sessions writing directions nobody hears.
    """
    if name == "qwen" and what == "instruction":
        try:
            return qwen_backend(state) == "studio"
        except Exception:                                   # noqa: BLE001
            return True
    return ENGINE_CAN.get(name, {}).get(what, () if what == "events" else False)


def qwen_backend(state=None):
    """How the Qwen engine runs here: "torch" where Qwen on PyTorch is
    installed, else "studio". Existence checks only -- a dropdown asks this."""
    import qwen_torch_engine

    state = state if state is not None else load_state()
    if qwen_torch_engine.available(state.get("qwenPython"), state.get("qwenModel")):
        return "torch"
    return "studio"


def studio_usable(state=None):
    """Could Qwen-TTS Studio load here: is it installed, and is Smart App
    Control -- which refuses its unsigned DLLs -- off. Read from the registry,
    never by loading a DLL, which is itself a refusal and a Windows toast."""
    import qwen_engine

    state = state if state is not None else load_state()
    studio = os.path.expandvars(state.get("studioDir") or "")
    if not studio or not os.path.isfile(os.path.join(studio, "qwen3_tts.dll")):
        return False
    return qwen_engine.smart_app_control() not in ("on", "evaluation")


def engine_ready(name, state=None):
    """Could this engine start right now, without installing anything?

    Qwen is ready where Qwen on PyTorch is installed, or where Studio is and
    Smart App Control will let it load. Pocket is a pip package and Breeze a
    separate download. All of it is asked about in a dropdown's worth of time,
    so no answer imports a model or loads a DLL.
    """
    if name == "pocket":
        import pocket_engine

        return pocket_engine.available()
    if name == "breeze":
        import breeze_engine

        state = state if state is not None else load_state()
        return breeze_engine.available(state.get("breezePython"), state.get("breezeDir"),
                                       state.get("breezeModel"))
    if name == "qwen":
        state = state if state is not None else load_state()
        return qwen_backend(state) == "torch" or studio_usable(state)
    return name in ENGINES


def engine_info(name):
    """Label, blurb and url for an engine. Never raises -- an unknown name gets
    a usable stub, because this is only ever used to describe something and a
    missing description should not stop a dropdown from being drawn."""
    return ENGINE_INFO.get(name, {"label": name, "blurb": "", "url": ""})


def engine_name(name):
    """What an engine is called in a sentence: its label without the memory."""
    return engine_info(name)["label"].split(" — ")[0]


def old_default_engine(raw):
    """"qwen" for a config written before 2.0 by someone who never chose an
    engine, else None.

    Qwen was the default then, and a config is saved lean -- an engine never
    changed is not in the file. So when DEFAULT_ENGINE moved to Pocket, such a
    config would have switched engine silently. `raw` is the file as written
    (not merged over the defaults); Qwen only when its Studio is really there.
    """
    if not isinstance(raw, dict) or "engine" in raw:
        return None
    studio = raw.get("studioDir") or DEFAULTS.get("studioDir")
    if studio and os.path.isfile(os.path.join(os.path.expandvars(studio), "runtime", "bin", "server", "jvm.dll")):
        return "qwen"
    return None


def engine_of(state=None):
    """Which engine the config asks for, sanity-checked.

    An unknown name falls back rather than raising. This is read on the path to
    speaking, and a typo in the config should cost the wrong engine at worst,
    never silence with no explanation.
    """
    state = state if state is not None else load_state()
    want = (state.get("engine") or DEFAULT_ENGINE).strip().lower()
    return want if want in ENGINES else DEFAULT_ENGINE


def engine_language(state=None):
    """The language Pocket TTS should load. Means nothing to the other engine."""
    import pocket_engine

    state = state if state is not None else load_state()
    want = (state.get("pocketLanguage") or pocket_engine.DEFAULT_LANGUAGE).strip().lower()
    return want if want in pocket_engine.LANGUAGES else pocket_engine.DEFAULT_LANGUAGE


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def add_voice_root(path):
    """Also look for voices in this folder, from now on. Returns the list.

    For a program that carries voices of its own -- a game with a people's
    worth of them -- and wants them spoken here without copying anything. The
    folder is read where it lies and never written to. Asking twice is
    harmless, and a folder that does not exist is refused rather than
    remembered, because a path that is wrong today is almost always a typo.
    """
    path = os.path.abspath(os.path.expandvars(str(path or "").strip()))
    if not path or not os.path.isdir(path):
        raise LookupError(f"no such folder: {path}")
    state = load_state()
    have = list(state.get("extraVoicesDirs") or [])
    if not any(_same_path(path, h) for h in have):
        have.append(path)
        patch_state(extraVoicesDirs=have)
    return have


def remove_voice_root(path):
    """Stop looking in a folder added with add_voice_root. Returns the list."""
    state = load_state()
    have = [h for h in (state.get("extraVoicesDirs") or []) if not _same_path(path, h)]
    patch_state(extraVoicesDirs=have)
    return have


def own_version():
    """This copy's version, from version.json. Empty if it cannot be read."""
    try:
        with open(os.path.join(ROOT, "version.json"), encoding="utf-8-sig") as fh:
            return json.load(fh).get("version") or ""
    except (OSError, ValueError):
        return ""


# Where another program finds this one when it is not running. A game that
# wants to start the voice cannot ask a server that is not up yet where it
# lives, so each start leaves the answer somewhere fixed: beside nothing of
# ours, in the user's own application data, where the setup program writes it
# too. Only paths and a version -- nothing of what was said.
WHERE_PATH = os.path.join(NOTES_DIR, "where.json")


def write_where(state=None, **extra):
    """Leave the note saying where this copy is and which Python runs it.

    `port` is the port the engine actually listens on, which is not always the
    one asked for: see speak_server.bind_port. `portHome`, when present, is the
    one it wanted and could not have. Anything in `extra` (an `error`) is added.
    """
    import sys

    state = state or load_state()
    doc = {"root": ROOT, "python": sys.executable or "", "pythonw": _python(),
           "port": state.get("port", 8765), "version": own_version()}
    if state.get("portHome") and state.get("portHome") != doc["port"]:
        doc["portHome"] = state["portHome"]
    doc.update({k: v for k, v in extra.items() if v is not None})
    try:
        os.makedirs(os.path.dirname(WHERE_PATH), exist_ok=True)
        tmp = WHERE_PATH + ".new"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, WHERE_PATH)
    except OSError:
        pass            # a note nobody may read is not worth failing a start over
    return doc


# The registration: one fixed place in the user's registry that says where
# claude-voice lives and how to start it, whether or not it has ever run.
# where.json could not be that -- only a running engine writes it, so a fresh
# install, a moved folder or an old copy was invisible to a game until the
# engine had been started by hand at least once (complaint 2, October 2026).
#
# Written by the installer (Kind "installed") and by `voice_cli register` for
# a developer's own checkout (Kind "dev", opt-in, so a git clone is never
# announced to games by accident). The engine refreshes Port, PortHome and
# Version on every start, but only when the key already names THIS folder: a
# second copy started from somewhere else must not steal it. The contract is
# docs/discovery.md; bump REG_CONTRACT when a value changes meaning.
REG_KEY = "Software\\" + PRODUCT
REG_CONTRACT = 1


def _winreg():
    try:
        import winreg
        return winreg
    except ImportError:         # not Windows: there is no registry to be found in
        return None


def launcher_command(python=None):
    """The exact command line that starts this copy's engine with no window.

    The first token is the executable, always quoted, so a reader splits it
    off without having to parse the rest. For now Python and voice_cli.py; a
    packaged build will name its own exe here instead.
    """
    exe = python or _python()
    return f'"{exe}" "{os.path.join(ROOT, "voice_cli.py")}" start'


def registration(key=None):
    """What the registry key says, as a dict of its values, or None."""
    reg = _winreg()
    if reg is None:
        return None
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, key or REG_KEY) as k:
            out, i = {}, 0
            while True:
                try:
                    name, value, _kind = reg.EnumValue(k, i)
                except OSError:
                    return out
                out[name] = value
                i += 1
    except OSError:
        return None


def registered_here(key=None):
    """True when the key exists and names this folder."""
    found = registration(key)
    return bool(found and found.get("InstallDir")
                and _same_path(found["InstallDir"], ROOT))


def _write_values(values, key):
    reg = _winreg()
    if reg is None:
        raise OSError("there is no Windows registry here")
    with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, key, 0, reg.KEY_SET_VALUE) as k:
        for name, value in values.items():
            if value is None:
                try:
                    reg.DeleteValue(k, name)
                except OSError:
                    pass
            elif isinstance(value, int):
                reg.SetValueEx(k, name, 0, reg.REG_DWORD, value)
            else:
                reg.SetValueEx(k, name, 0, reg.REG_SZ, str(value))


def _port_values(state):
    port = int(state.get("port") or 8765)
    return {"Port": port, "PortHome": int(state.get("portHome") or port)}


def register(kind=None, python=None, state=None, key=None):
    """Write the key for this copy, replacing whatever copy it named before.

    `kind` is "installed" or "dev"; left out, a folder with .git is "dev".
    Returns the values written. Raises OSError when it cannot be written.
    """
    kind = kind or ("dev" if os.path.exists(os.path.join(ROOT, ".git")) else "installed")
    if kind not in ("installed", "dev"):
        raise ValueError(f"kind must be installed or dev, not {kind!r}")
    state = state or load_state()
    values = {"Contract": REG_CONTRACT, "InstallDir": ROOT, "Kind": kind,
              "Version": own_version(), "Launcher": launcher_command(python),
              **_port_values(state), "Updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _write_values(values, key or REG_KEY)
    return values


def refresh_registration(state, key=None):
    """On an engine start: the port it got, and the version, if the key is ours.

    Never creates the key and never touches another copy's: True only when it
    named this folder and was brought up to date.
    """
    if not registered_here(key):
        return False
    try:
        _write_values({**_port_values(state), "Version": own_version(),
                       "Updated": time.strftime("%Y-%m-%dT%H:%M:%S")}, key or REG_KEY)
        return True
    except OSError:
        return False


def unregister(force=False, key=None):
    """Remove the key. Returns "removed", "absent" or "other" (it names
    another folder, and `force` was not given -- that copy's, not ours)."""
    found = registration(key)
    if found is None:
        return "absent"
    if not force and not registered_here(key):
        return "other"
    reg = _winreg()
    try:
        reg.DeleteKey(reg.HKEY_CURRENT_USER, key or REG_KEY)
    except FileNotFoundError:
        return "absent"
    return "removed"


def own_voices_dir(state=None):
    """Where a voice made on this machine goes. In an installed copy that is the
    data folder, which an update never touches; in a checkout, its own voices folder."""
    if INSTALL:
        return os.path.join(DATA_DIR, "voices")
    return voice_roots(state)[0]


def voice_roots(state=None):
    state = state or load_state()
    roots = [state.get("voicesDir") or "voices"]
    if INSTALL:
        roots.append(os.path.join(DATA_DIR, "voices"))
    roots += list(state.get("extraVoicesDirs") or [])
    out = []
    for r in roots:
        r = r if os.path.isabs(r) else os.path.join(ROOT, r)
        if os.path.isdir(r) and r not in out:
            out.append(r)
    return out


def is_added_voice(voice, state=None):
    """A voice from a folder that is not this app's own: one of extraVoicesDirs,
    where a game such as Immersive AI registers its people. The panel can hide
    these from its list (hideGameVoices); nothing that speaks with them -- the
    game above all -- is ever affected."""
    roots = voice_roots(state)
    if not roots:
        return False
    state = state or load_state()
    added = {os.path.normcase(os.path.abspath(r)) for r in (state.get("extraVoicesDirs") or [])}
    own = {os.path.normcase(os.path.abspath(r)) for r in roots} - added
    return os.path.normcase(os.path.abspath(voice.get("root") or roots[0])) not in own


# --------------------------------------------------------------------------
# voice catalogue
# --------------------------------------------------------------------------

# What a cloned Pocket TTS voice is called inside a voice folder. A baked
# speaker state rather than an embedding: the model turns a wav into one of
# these once, and loading it afterwards is instant.
POCKET_VOICE = "pocket.safetensors"

# What Breeze TTS 2 clones from: a clean clip of the voice and, beside it under
# the same name, a .txt of exactly what it says. Breeze takes no baked state --
# it learns the voice from the clip on every request -- and a transcript that
# drifts from the audio teaches it a wrong alignment, so the words live in a
# file of their own rather than being retyped anywhere.
#
# breeze-reference.wav is the one that ships: for Breeze the clip *is* the
# voice, so it is committed where Pocket's working render is not. The render
# make_pocket_voice.py leaves behind is second on the list because it is
# exactly such a clip, and the script now keeps its words beside it -- so a
# voice carried across to Pocket speaks on Breeze too, on this machine, without
# another step.
BREEZE_REFERENCES = ("breeze-reference.wav", "pocket-reference.wav")


def _breeze_reference(d):
    """(clip, transcript) for a voice folder, or (None, "") if it has none."""
    for name in BREEZE_REFERENCES:
        wav = os.path.join(d, name)
        txt = os.path.splitext(wav)[0] + ".txt"
        if os.path.exists(wav) and os.path.exists(txt):
            try:
                with open(txt, encoding="utf-8-sig") as fh:
                    words = fh.read().strip()
            except OSError:
                continue
            if words:
                return wav, words
    return None, ""


def _is_voice_dir(d):
    return (os.path.exists(os.path.join(d, "embedding.json"))
            or os.path.exists(os.path.join(d, "icl-prompt.json"))
            or os.path.exists(os.path.join(d, POCKET_VOICE))
            or _breeze_reference(d)[0] is not None)


# What voice.json's own "Gender" number means. It is the Immersive AI mod's
# field, where every one of these folders was first made: 0 said nothing, 1 a
# woman, 2 a man.
_GENDERS = {1: "female", 2: "male"}


def _read_voice(d, vid, sex, culture, root):
    name, persona, style, pocket_lang = vid, "", "", ""
    try:
        with open(os.path.join(d, "voice.json"), encoding="utf-8-sig") as fh:
            doc = json.load(fh)
        name = doc.get("Name") or vid
        # A flat folder -- a voice sitting directly in a root, the shape the
        # mod's own shelf keeps -- has no rung to say either of these, so the
        # file says them instead. A rung that did say them still wins: it is
        # what the person filing the voice chose.
        if sex is None:
            sex = _GENDERS.get(doc.get("Gender"), "other")
        if culture is None:
            culture = (doc.get("Culture") or "other").strip().lower() or "other"
        # How this voice behaves, not just how it sounds. Being addressed by
        # name is natural once a voice has one, so the manner should match it.
        persona = doc.get("Persona") or ""
        # Narrating or talking. Optional, and shown in the dropdown beside the
        # sex -- the two together are most of what you want to know about a
        # voice before you pick it.
        style = doc.get("Style") or ""
        # Which Pocket TTS model this voice was baked against. A speaker state
        # belongs to exactly one of the six, and handing it to another gives
        # nonsense rather than an error -- so a carried-across voice records
        # its own, and make_pocket_voice.py writes it.
        pocket_lang = doc.get("PocketLanguage") or ""
    except (OSError, ValueError):
        pass
    sex = sex or "other"
    culture = culture or "other"
    emb = os.path.join(d, "embedding.json")
    icl = os.path.join(d, "icl-prompt.json")
    pkt = os.path.join(d, POCKET_VOICE)
    clip, words = _breeze_reference(d)
    return {
        "id": vid, "name": name, "sex": sex, "culture": culture,
        "persona": persona, "style": style, "dir": d, "root": root,
        "tag": "" if culture in ("other", "english") else culture.title(),
        "pocketLanguage": pocket_lang,
        "embedding": emb if os.path.exists(emb) else None,
        "icl": icl if os.path.exists(icl) else None,
        "pocket": pkt if os.path.exists(pkt) else None,
        "breeze": clip,
        "breezeText": words,
    }


def catalog(state=None, engine=None):
    """Every voice the chosen engine can speak in, as flat dicts.

    Pocket TTS keeps no voices on disk -- they are names the model fetches a
    precomputed state for -- so on that engine this is a table rather than a
    directory walk, and returns the same keys so that nothing downstream has to
    ask which engine it is looking at.

    For Qwen, three layouts are accepted, because a handful of personal voices
    and a whole generated library want different shapes:

        <root>\\<sex>\\<id>              what this repo ships
        <root>\\<sex>\\<culture>\\<id>   grouped -- for larger collections
        <root>\\<id>                    flat, sex and people read from voice.json

    The third is the Immersive AI mod's own shelf, which the game registers
    through POST /voice-roots so that a voice a player made for the game is
    heard here too. A name starting with an underscore at the top of a root is
    housekeeping (the mod keeps _cache and _seeded.json there) and is skipped.

    Earlier roots win, so a bundled voice shadows a same-named local one.
    """
    state = state or load_state()
    which = engine or engine_of(state)
    pocket = which == "pocket"
    out, seen = [], set()
    for root in voice_roots(state):
        for sex in sorted(os.listdir(root)):
            sex_dir = os.path.join(root, sex)
            if not os.path.isdir(sex_dir) or sex.startswith("_"):
                continue
            if _is_voice_dir(sex_dir):                  # flat: the rung is the voice
                if sex.lower() not in seen:
                    seen.add(sex.lower())
                    out.append(_read_voice(sex_dir, sex, None, None, root))
                continue
            for entry in sorted(os.listdir(sex_dir)):
                d = os.path.join(sex_dir, entry)
                if not os.path.isdir(d):
                    continue
                if _is_voice_dir(d):
                    if entry.lower() not in seen:
                        seen.add(entry.lower())
                        out.append(_read_voice(d, entry, sex, "other", root))
                    continue
                for vid in sorted(os.listdir(d)):       # entry was a culture
                    sub = os.path.join(d, vid)
                    if not os.path.isdir(sub) or not _is_voice_dir(sub):
                        continue
                    if vid.lower() in seen:
                        continue
                    seen.add(vid.lower())
                    out.append(_read_voice(sub, vid, sex, entry, root))
    if pocket:
        # Only the local voices actually cloned into this engine -- a folder
        # with an embedding in it means nothing here -- and then the model's
        # own catalogue behind them. Cloned first, so Abby is at the top of the
        # dropdown where she belongs, and so a name collision resolves in
        # favour of the one on disk.
        import pocket_engine

        # A voice with a clip of itself is one too: Pocket clones from a wav
        # as readily as it loads a baked state -- a few seconds the first time
        # in a session, then held. That is what lets a library of a hundred
        # voices speak here without a hundred 15 MB files beside them.
        #
        # Only where it CAN clone, though: that takes weights Kyutai gate behind
        # a Hugging Face sign-in, and without them a clip-only voice is silence.
        # pocket_engine writes down which it is the first time the model loads.
        clips = bool(state.get("pocketCloning"))
        cloned = [v for v in out if v["pocket"] or (clips and v["breeze"])]
        have = {v["id"].lower() for v in cloned}
        return cloned + [v for v in pocket_engine.catalog(engine_language(state))
                         if v["id"].lower() not in have]
    if which == "breeze":
        # Only the voices with a clip Breeze can learn from and the words said
        # in it. Breeze ships no voices of its own; every one of these is a
        # voice already here, heard through a different model.
        return [v for v in out if v["breeze"]]
    return out


def resolve(voice_id, source="embedding", state=None):
    """Find a voice by id (exact first, then substring). Returns (voice, kwargs).

    The kwargs are whatever the chosen engine's synthesize wants, and the two
    engines want different things -- a path to an embedding on disk, or a name
    the model knows. Deciding that here is the point: the server hands these
    straight through without looking, so an engine added later needs no change
    above this line.
    """
    state = state if state is not None else load_state()
    engine = engine_of(state)
    voices = catalog(state, engine)
    if not voices:
        raise LookupError({
            "qwen": "no voices found. Add one with: python voice_cli.py clone "
                    "<sample.wav> --name <name>",
            "pocket": "pocket-tts has no voices to offer. Is it installed? "
                      "pip install pocket-tts",
            "breeze": "no voice has a clip Breeze can clone from. It needs "
                      "breeze-reference.wav and a breeze-reference.txt of its exact "
                      "words in the voice's folder; make_pocket_voice.py leaves a "
                      "pair it can use too.",
        }.get(engine, "no voices found"))

    needle = (voice_id or "").strip().lower()
    hit = next((v for v in voices if v["id"].lower() == needle), None)
    if hit is None:
        matches = [v for v in voices if needle and needle in v["id"].lower()]
        if len(matches) == 1:
            hit = matches[0]
        elif len(matches) > 1:
            raise LookupError(
                f"'{voice_id}' matches {len(matches)} voices: "
                + ", ".join(m["id"] for m in matches[:8])
                + ("..." if len(matches) > 8 else ""))
        else:
            raise LookupError(f"no voice matching '{voice_id}'")

    if engine == "pocket":
        # The language travels with the voice. Estelle needs the French model
        # and Abby needs the English one, and which is loaded is decided by
        # whoever was picked -- not by a config key set hours earlier.
        return hit, {"pocket_voice": hit.get("pocket") or (state.get("pocketCloning") and hit.get("breeze")) or hit["id"],
                     "pocket_language": (hit.get("pocketLanguage")
                                         or engine_language(state))}

    if engine == "breeze":
        # The clip and its words, every time: Breeze keeps no state for a
        # voice, so what identifies one to it is what it learns her from.
        return hit, {"breeze_ref": hit["breeze"], "breeze_ref_text": hit["breezeText"]}

    path = hit.get("icl" if source == "icl" else "embedding") or hit["embedding"] or hit["icl"]
    if path is None:
        raise LookupError(f"voice '{hit['id']}' has no embedding or ICL prompt")
    key = "icl_prompt_path" if path.endswith("icl-prompt.json") else "embedding_path"
    return hit, {key: path}


# --------------------------------------------------------------------------
# the line a voice says when you ask to hear her
# --------------------------------------------------------------------------

# Here, in the code, and not in a file beside it. There was a file for a day and
# it was the wrong idea twice over: nobody but whoever hacks on this ever wants
# to change these, and anyone who wants to hear arbitrary words already has the
# plus button in the panel, which reads anything you type. What a file added was
# a second place for the same sentence to live, with the file quietly winning --
# so an edit made here did nothing at all, and looked like the button being
# broken.
#
# The words themselves are Abby's and Max's from docs/samples, which is where
# most people hear this project first. That is the whole point of the button:
# not a curiosity but a *reference*. One line you have heard fifty times, so
# that pressing it answers "does she still sound right, is the engine alive,
# has it wandered off" in five seconds, against a memory rather than against
# nothing. They were taken back off those wavs with Windows' own speech
# recognition, since nothing wrote them down at the time.
#
# Written to be performed, not merely spoken. The model reads punctuation as
# performance: the first version of these ended in full stops and came out
# bored, which is a strange thing to hear from a voice whose whole character is
# that she is pleased to be here. So they are short clauses, an exclamation to
# open on, and a question to land on -- a rising ending is the single cheapest
# way to sound like somebody enjoying themselves.
#
# It also rolls its prosody afresh on every generation, so two presses are never
# quite identical. One flat reading is the model having an off roll, not the
# line: press it again before rewriting anything.
TEST_LINES = {
    "default": "Hii! I'm {name}! I read your answers out loud, right here, "
               "on your own machine. Pretty neat, right?",
    "abby": "Hii! I'm Abby! I read your answers out loud, right here, on your "
            "own machine. No cloud, no waiting. Pretty neat, right?",
    "max": "Hey! I'm Max! Come on, let's get through this list. "
           "One more, and then we're done!",
    "neya": "Hi. I'm Neya. I'll read you every answer, right here, on your "
            "own machine. Take your time. I'm right here with you.",
}


def test_line(voice_id, state=None):
    """What this voice says when you ask to hear her.

    A voice with no line of its own gets the default, with its name and its
    persona put into it -- so a voice cloned this morning has something to say
    without anybody writing it a line first.

    The placeholders are replaced rather than formatted, because a line with a
    stray brace in it is a line to say and not a KeyError.
    """
    voice = next((v for v in catalog(state)
                  if v["id"].lower() == (voice_id or "").strip().lower()), None)
    said = TEST_LINES.get((voice_id or "").strip().lower()) if voice_id else None
    if not said:
        said = TEST_LINES["default"]
    name = (voice or {}).get("name") or voice_id or "nobody"
    # Personas end in a full stop already; the default line puts one after
    # {persona} only when there is nothing there to punctuate.
    persona = ((voice or {}).get("persona") or "").strip()
    return " ".join(str(said).replace("{name}", str(name))
                    .replace("{persona}", persona).split())


# --------------------------------------------------------------------------
# markdown -> speech
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_TABLE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_RULE = re.compile(r"^\s*([-*_]\s*){3,}$", re.M)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.M)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.M)
_NUMBER = re.compile(r"^\s*\d+[.)]\s+", re.M)
_EMPH = re.compile(r"(\*\*|\*|__|_|~~)")

# Typography a terminal shows happily and a speech model does not. Anything not
# listed and not a letter/digit (emoji, box drawing, check marks) is dropped
# rather than handed to the model as an unpronounceable token.
_SPOKEN = {
    # A comma *and a space*. Without the space, a dash with no room around it --
    # "steps one and two only—pairs" -- came out as "only,pairs", two words run
    # into one, which the model reads as one unpronounceable token. A dash that
    # did have spaces was always fine, so this went unnoticed: the tidy-up below
    # strips the space *before* a comma but cannot invent the one after it.
    "—": ", ", "–": ", ", "―": ", ",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": ".", " ": " ", "​": "",
    "×": " times ", "°": " degrees ", "±": " plus or minus ",
    "→": " to ", "←": " from ", "⇒": " gives ",
    "≤": " at most ", "≥": " at least ", "≠": " not equal to ",
    "•": " ", "·": " ", "✓": " ", "✗": " ",
}
_KEEP_PUNCT = set(" \n\t.,;:!?'\"()-/%$&+=@#")

# An en dash between two numbers is a range, and a comma there is not merely
# untidy -- it changes the number. "10–20 items" read as "10,20 items" is a
# different quantity in most of the world. Only the en dash, which is what a
# range is actually written with: an em dash between digits is more often an
# aside, and a plain hyphen is a date as often as it is a span.
_RANGE = re.compile(r"(?<=\d)\s*–\s*(?=\d)")


# Is there anything in this worth saying out loud? A letter or a digit in any
# script, not only a Latin one -- asking for [A-Za-z0-9] threw away every line
# of Russian, Greek or Chinese as if it had been punctuation, silently.
_SPEAKABLE = re.compile(r"[^\W_]")


def _normalize(text):
    import unicodedata

    out = []
    for ch in text:
        if ch in _SPOKEN:
            out.append(_SPOKEN[ch])
        elif ch.isascii() or unicodedata.category(ch)[0] in "LN":
            out.append(ch)
        elif ch in _KEEP_PUNCT:
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)


def _speakable_code(match):
    """Keep short identifiers, drop paths and anything that reads like line noise."""
    body = match.group(1).strip()
    if not body or len(body) > 24 or "/" in body or "\\" in body:
        return " "
    return " " + body.replace("_", " ").replace("-", " ") + " "


def clean_text(md, max_chars=600):
    """Strip the markdown a terminal renders but an ear does not want."""
    if not md:
        return ""
    t = _normalize(_RANGE.sub(" to ", md))
    t = _FENCE.sub(" ", t)
    t = _TABLE.sub(" ", t)
    t = _RULE.sub(" ", t)
    t = _LINK.sub(r"\1", t)
    t = _URL.sub(" ", t)
    t = _INLINE.sub(_speakable_code, t)
    t = _HEADING.sub("", t)
    t = _QUOTE.sub("", t)
    t = _BULLET.sub("", t)
    t = _NUMBER.sub("", t)
    # Before the emphasis goes, or "*laughs*" is left as the word laughs.
    t = _STARRED.sub(lambda m: f"({_event_name(m)})", t)
    t = _EMPH.sub("", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()

    # Headings and list items carry no full stop of their own, so give them one:
    # otherwise they run into the next sentence and the chunker cannot breathe.
    lines = []
    for ln in t.split("\n"):
        ln = ln.strip()
        if not _SPEAKABLE.search(ln):
            continue
        lines.append(ln if ln[-1] in ".!?:;," else ln + ".")
    t = " ".join(lines)

    t = re.sub(r"\s+([.,;:!?])", r"\1", t)      # 'reads .' <- a path we dropped
    t = re.sub(r"([.,;:!?])\1+", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()

    return truncate(t, max_chars)


# Matches '## TL;DR', '**TLDR:**', 'TL;DR -' and friends, at a line start.
_TLDR = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?(?:\*\*|__)?\s*TL\s*[;:.]?\s*DR\s*(?:\*\*|__)?\s*[:\-—]?\s*",
    re.I | re.M)
_NEXT_HEADING = re.compile(r"^\s{0,3}#{1,6}\s", re.M)


def extract_summary(md):
    """The TL;DR block, if the answer has one -- that alone is worth hearing.

    An answer full of paths, flags and identifiers is written for eyes that can
    skip around. The ear gets the summary; the detail stays on screen.
    """
    if not md:
        return ""
    m = _TLDR.search(md)
    if not m:
        return ""
    rest = md[m.end():]
    nxt = _NEXT_HEADING.search(rest)      # a later section is not part of the summary
    if nxt:
        rest = rest[:nxt.start()]
    return rest.strip()


def truncate(text, max_chars):
    """Cut at a sentence boundary rather than mid-word.

    Cutting on a full stop is what makes this dangerous rather than merely
    annoying: the intonation falls, the listener hears a finished thought, and
    nothing about it sounds like a limit. So say so when it happens. The
    ceiling is a runaway guard now, and a summary that trips it is worth
    knowing about.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    m = list(re.finditer(r"[.!?](\s|$)", cut))
    kept = (cut[:m[-1].end()].strip() if m and m[-1].end() > max_chars * 0.4
            else cut.rsplit(" ", 1)[0].strip() + "...")
    notify(f"cut {len(text) - len(kept)} characters off a {len(text)}-character "
           f"message at the {max_chars} ceiling -- the end was not spoken")
    return kept


# --------------------------------------------------------------------------
# sounds written into the text, and moods given beside it
# --------------------------------------------------------------------------
#
# Two ways to ask for more than the words, and they are different in kind. A
# sound is *in* the line, at the place it happens: "So I told it to stop, and
# (laugh) it just kept going." A mood is *about* the line -- how all of it is
# said -- and rides beside it as an instruction. Breeze does both; Qwen takes a
# mood and has never made a sound; Pocket does neither.
#
# The spelling a sound is written in is forgiving, because the one writing it is
# usually a language model with habits of its own: "(laughs)", "[giggles]" and
# "*chuckling*" all mean the same thing, and arrive as Breeze's own "(laugh)".
# Written anywhere that cannot perform it, a sound is taken out rather than
# read, since an engine asked to say "(laugh)" says the word laugh -- which is
# the one outcome worse than silence.

EVENT_NAMES = ("laugh", "sigh", "cough", "clears throat")

_EVENT_ALIASES = {
    "laugh": ("laugh", "laughs", "laughing", "laughter", "chuckle", "chuckles",
              "chuckling", "giggle", "giggles", "giggling"),
    "sigh": ("sigh", "sighs", "sighing"),
    "cough": ("cough", "coughs", "coughing"),
    "clears throat": ("clears throat", "clear throat", "clearing throat",
                      "clears her throat", "clears his throat", "clears their throat",
                      "clearing her throat", "clearing his throat", "clears my throat",
                      "ahem"),
}
_EVENT_OF = {alias: name for name, aliases in _EVENT_ALIASES.items() for alias in aliases}
_ALIAS = "|".join(sorted((re.escape(a).replace(r"\ ", r"\s+") for a in _EVENT_OF),
                         key=len, reverse=True))
# In a matching pair of (), [] or ** -- never bare, because "I laugh at that"
# is a sentence and not a stage direction. A couple of words either side are
# allowed, since "(laughs softly)" and "(a nervous laugh)" are how these get
# written; the words themselves are dropped, because Breeze has one laugh and
# no adverbs for it.
_INNER = rf"(?:[a-z]+\s+){{0,2}}({_ALIAS})(?:\s+[a-z]+){{0,2}}"
_EVENT = re.compile(rf"\(\s*{_INNER}\s*\)|\[\s*{_INNER}\s*\]|\*\s*{_INNER}\s*\*", re.I)

# The same sounds between single asterisks, the way chat writes them, in text
# on its way from markdown to speech -- where the emphasis is stripped next, and
# "*laughs*" would otherwise reach the engine as the word laughs. Stricter than
# the brackets, because in markdown an asterisk is mostly plain emphasis: only a
# span holding nothing but the sound, with at most one -ly word after it, and
# only where a stage direction stands -- before a capital, a bracket, a stop or
# the end of the line. "*Laugh* tracks are gone" stays a sentence.
_STARRED = re.compile(
    rf"(?<![\w*])\*\s*((?i:{_ALIAS}))(?:\s+(?i:[a-z]+ly))?\s*\*(?![\w*])"
    rf"(?=[ \t]*(?:$|[A-Z\"'(\[.,;:!?]))", re.M)


def _event_name(match):
    said = next(g for g in match.groups() if g)
    return _EVENT_OF[re.sub(r"\s+", " ", said.lower())]


def events_in(text):
    """The sounds written into a line, in Breeze's names, in order."""
    return [_event_name(m) for m in _EVENT.finditer(text or "")]


def perform(text, engine):
    """The line as this engine should be handed it.

    Sounds it makes are rewritten in its own spelling; sounds it does not make
    are taken out, with the spacing and punctuation around them tidied so that
    nothing reads as a stumble. An engine that makes none gets none -- which
    leaves text without any tags in it exactly as it came.
    """
    if not text or not _EVENT.search(text):
        return text
    can = engine_can(engine, "events")

    def swap(match):
        name = _event_name(match)
        return f" ({name}) " if name in can else " "

    out = _EVENT.sub(swap, text)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,;:])(?=[,.;:!?])", "", out)     # "stop, , like" once the tag goes
    return re.sub(r"\s{2,}", " ", out).strip(" ,;")


# Moods by name, each expanded to a whole instruction. Named because a caller
# should not have to know the lesson in docs/engines.md -- that one adjective
# does not steer anything, and a verb with two or three things about the
# delivery does. Written in that shape, all of them.
#
# Three were heard on Breeze in Abby's voice before they were written here --
# sad, excited and whisper, on 2026-09-24, and sad is word for word the one
# Qwen was measured with. The rest follow the same pattern and have not been
# listened to yet; the reply to a line says which mood it went out with.
MOODS = {
    "happy": "Speak brightly and happily, warm and smiling, with a lift at the "
             "end of each phrase.",
    "excited": "Speak quickly and excitedly, bright and breathless, full of delight.",
    "playful": "Speak playfully and teasingly, light and bouncy, as if holding back "
               "a grin.",
    "calm": "Speak calmly and gently, unhurried and soft, as if settling someone down.",
    "tender": "Speak softly and tenderly, warm and close, full of affection.",
    "sad": "Speak slowly and sadly, quiet and downcast, with long pauses.",
    "tired": "Speak slowly and wearily, low and heavy, as if at the end of a long day.",
    "serious": "Speak slowly with a restrained, serious tone.",
    "whisper": "Whisper quietly and secretively, as if leaning in to share a secret.",
    "surprised": "Speak with sudden surprise, quick and rising, as if caught off guard.",
    "angry": "Speak sharply and angrily, clipped and forceful, barely holding it in.",
}
MOODS_HEARD = ("sad", "excited", "whisper")

# A few spellings a caller is likely to reach for, so they land on a mood
# rather than on "no mood called that". The adverbs are for stage directions,
# which is how a session writes one: "(softly) Goodnight."
_MOOD_ALIASES = {"whispering": "whisper", "whispered": "whisper", "whispers": "whisper",
                 "sadly": "sad", "joyful": "happy", "cheerful": "happy",
                 "happily": "happy", "excitedly": "excited", "gentle": "calm",
                 "gently": "calm", "calmly": "calm", "soft": "tender",
                 "softly": "tender", "tenderly": "tender", "weary": "tired",
                 "wearily": "tired", "mad": "angry", "angrily": "angry",
                 "shocked": "surprised", "teasing": "playful", "teasingly": "playful",
                 "playfully": "playful"}

# And a slip of the keys. A mood typed into the panel by hand came out as
# "wisper", which is no mood, and the line went out as though none had been
# asked for, with nothing anywhere to say why. So one slip is forgiven, but only
# in words of five letters or more: in a short one a letter makes a different
# word, and "wary" is not "weary", nor "tire" "tired". Every spelling of every
# sound was measured against these too, and none comes anywhere near a mood.
_SPELT_OUT = sorted(w for w in set(MOODS) | set(_MOOD_ALIASES) if len(w) >= 5)

# Words that point at the line, or say how much, and nothing about how it
# sounds. "whisper this line" in the panel's box asks for the mood whisper, and
# should get its whole instruction: sent as they were, its words are the weak
# form docs/engines.md measured, where one word landed inside the sampler's own
# spread and a verb with a few things about the delivery did not.
_POINTING = {"a", "an", "the", "this", "that", "it", "line", "one", "text", "please",
             "pls", "say", "read", "speak", "sound", "make", "be", "in", "with",
             "voice", "tone", "very", "really", "so", "just"}


def _mood_word(word):
    """The mood one word names, forgiving one slip in a long word, or None."""
    key = word.strip().lower()
    key = _MOOD_ALIASES.get(key, key)
    if key in MOODS:
        return key
    if len(key) < 5:
        return None
    near = difflib.get_close_matches(key, _SPELT_OUT, n=1, cutoff=0.85)
    return _MOOD_ALIASES.get(near[0], near[0]) if near else None


def mood_name(asked):
    """The mood some words ask for, or None: "sad", "wisper", "whisper this line".

    One mood and nothing else about the sound. "Whisper it slowly" asks for
    slowly as well, which no mood here promises, so that is somebody's own
    instruction and goes to the engine as they wrote it -- as does "don't
    whisper", whose first word is no pointing word.
    """
    named = set()
    for word in re.findall(r"[a-z]+", (asked or "").lower()):
        if word in _POINTING:
            continue
        mood = _mood_word(word)
        if not mood:
            return None
        named.add(mood)
    return named.pop() if len(named) == 1 else None


def mood_instruction(mood):
    """(name, instruction) for a mood, or (None, "") for one we do not know."""
    name = mood_name(mood)
    return (name, MOODS[name]) if name else (None, "")


# A session has no field beside its words to put a mood in -- the text is all it
# has -- so it writes the mood into the text as a stage direction, the way a
# script does: "(whisper) I found it." Only a bracket holding one word, and that
# word a mood, counts, so a real aside in brackets is never taken for one, and
# only () or []: "*sad*" is emphasis far more often than it is a direction. A
# link's text in square brackets is not one either.
_MOOD_MARK = re.compile(r"\(\s*([a-z]+)\s*\)|\[\s*([a-z]+)\s*\](?!\()", re.I)


def direction(text):
    """(mood, text): the mood a line asks for, and the line without asking.

    The mood is the first one named, as the name it expands from, or None.
    Every direction comes out of the text whether it is used or not -- a mood
    is how a line is said and never part of what is said, and an engine that
    takes none should not be handed the word "whisper" to read instead.

    One per message, and the first wins: an engine performs one instruction
    across a whole generation, so a second one could only ever be ignored.
    Sounds are left where they are. They belong to the words, and the engine
    decides whether it can make them.
    """
    if not text:
        return None, text or ""
    asked = []

    def take(match):
        mood = _mood_word(next(g for g in match.groups() if g))
        if not mood:
            return match.group(0)            # an aside, left exactly as written
        asked.append(mood)
        return " "

    out = _MOOD_MARK.sub(take, text)
    if not asked:
        return None, text
    mood = asked[0]
    # A direction on a line of its own was given a full stop by clean_text, and
    # that stop is left behind: "(sad). It failed" and "Done. (sad). Next".
    out = re.sub(r"([.!?,;:])(?:\s*[.,;:])+", r"\1", out)
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return mood, re.sub(r"^[.,;:]\s*", "", out)


# --------------------------------------------------------------------------
# is what came back really speech?
# --------------------------------------------------------------------------
#
# Autoregressive TTS derails: the model misses its end-of-speech token and
# generates until it hits its own ceiling, which comes out as babbling or a
# held vowel. It happens in the middle or at the end, never at the beginning,
# so it is drift rather than bad conditioning -- and every word before it is
# good. The engine's own default ceiling is 4096 audio tokens at 80 ms each,
# which is 327 seconds of noise, and we cannot lower it from here: the Kotlin
# 'generate' wrapper we reach through JNI takes no parameter block.
#
# So the check is after the fact, on the one thing we can always measure: how
# much audio came back against how much the text justifies. The numbers below
# were measured in the sister project against the same DLL. See
# docs/engine-notes.md.

# The factors below were not guessed. Every generation in this tool's own
# speak-server.log -- a thousand of them -- was replayed against the estimate,
# and honest speech landed between 0.20x and 1.18x of it, median 0.68x. The one
# derail in that log sat at 2.21x, alone, with nothing between it and 1.18.
SAMPLE_RATE = 24000
GRACE_SECONDS = 1.5        # the breath at either end, which a short line is mostly made of
CHARS_PER_SECOND = 13.0    # measured 13-17; the low end is the generous one, and generous is safe
DERAIL_FACTOR = 1.8        # clears honest speech by half again, and still caught the real one
MIN_SECONDS = 3.2          # never call a short line a derail: "Right, done." needs room

# Far *less* audio than the text justifies is the same failure from the other
# end -- the broken-ICL road returns ok with 1920 samples, eight hundredths of
# a second. But honest short lines are quick, and a first pass at 0.35x flagged
# fifteen perfectly good ones. Only the catastrophic shape is worth catching,
# so: a tenth of an honest reading, and never more than six tenths of a second.
SWALLOW_FACTOR = 0.10
SWALLOW_FLOOR = 0.6


# A sound written into the line is time that no character accounts for. A laugh
# or a sigh ran a second or two on Breeze; counting the tag's own letters instead
# gave it half a second, which is how a laugh would end up cut off.
EVENT_SECONDS = 1.5

# How much longer a line may run when it was told how to sound. A mood slows a
# reading down -- that is often the point of it -- and Breeze's slow sad line
# ran 11.4 s where 5.7 s was honest, past the plain ceiling, so the guard would
# have stopped her mid-sentence for doing exactly what she was asked. Qwen's
# slowest measured 1.35 times its plain reading, well inside this.
INSTRUCTED_SLACK = 2.0


def expected_seconds(text):
    """How long an honest reading of this text should take."""
    text = text or ""
    events = len(_EVENT.findall(text))
    if events:
        text = _EVENT.sub(" ", text)
    chars = len(text.strip())
    if not chars and not events:
        return 0.0
    return GRACE_SECONDS + chars / CHARS_PER_SECOND + events * EVENT_SECONDS


def ceiling_seconds(text, slack=1.0):
    """The longest this text may honestly become before we stop believing it.

    `slack` is INSTRUCTED_SLACK for a line that carries a mood, and 1.0 for
    one that does not.
    """
    expected = expected_seconds(text)
    return max(expected * DERAIL_FACTOR * slack, MIN_SECONDS) if expected else 0.0


def audio_verdict(text, seconds, slack=1.0):
    """'ok', 'derail' (babbling on) or 'swallowed' (it barely said anything).

    Both failures return success from the engine -- that is the whole trap. A
    derail returns minutes of noise and a swallowed line returns eight
    hundredths of a second, and both say ok:true. Length against text is the
    only honest test either way.
    """
    expected = expected_seconds(text)
    if not expected:
        return "ok"
    if seconds > ceiling_seconds(text, slack):
        return "derail"
    if seconds < min(SWALLOW_FLOOR, expected * SWALLOW_FACTOR):
        return "swallowed"
    return "ok"


def chunks(text, first=140, target=480):
    """Sentence-ish pieces, so speech starts before the whole thing is synthesised.

    Every chunk is a separate generation, and two generations of the same voice
    are not identical -- the model rolls its own prosody afresh each time, so
    the timbre drifts across a boundary and the listener hears the speaker being
    swapped mid-answer. The sister project hit the same thing in a playtest and
    named it the same way. So boundaries are worth spending: only the *first*
    chunk is kept short, to get speech started quickly, and the rest are large
    enough that most answers are one more piece.

    `target` went 320 -> 480 when the length check landed. Bigger pieces mean
    fewer seams to hear, and the reason not to have them -- that one derail
    would take a bigger bite out of the answer -- stopped applying once a
    derailed piece is detected and re-synthesised instead of played.

    None of which is the real fix. The engine can do one generation and hand
    over its audio in pieces, which has no seam at all; see docs/engine-notes.md.
    """
    parts = re.split(r"(?<=[.!?;:])\s+", text)
    out, buf = [], ""
    limit = first
    for p in parts:
        p = p.strip()
        if not p:
            continue
        while len(p) > target * 2:                 # a run-on with no punctuation
            head, sep, rest = p[:target].rpartition(" ")
            if not sep:
                break
            out.append(head.strip())
            p = rest
            limit = target
        if not buf:
            buf = p
        elif len(buf) + len(p) + 1 <= limit:
            buf += " " + p
        else:
            out.append(buf)
            buf = p
            limit = target                         # only the opener stays short
    if buf:
        out.append(buf)
    return [c for c in out if _SPEAKABLE.search(c)]


# --------------------------------------------------------------------------
# cutting a continuous stream into things that can be played
# --------------------------------------------------------------------------
#
# A streaming generation arrives as one unbroken voice, which is the whole
# point of it. But playback is winsound, which plays a file at a time and puts
# a small gap between one and the next -- so the stream still has to be cut
# somewhere, and a cut in the middle of a word is heard as a stutter.
#
# The engine does report which slice of text each piece covers, and that looked
# like the answer until it was measured: the counter lags badly, stopping at
# byte 140 of 213 on a clip that spoke every word. Silence does not lag. There
# were eight clean gaps in twelve seconds of speech, which is more cut points
# than a five-sentence answer needs.

QUIET_WINDOW = 0.04        # 40 ms, about the shortest gap between two words
QUIET_SEARCH = 1.5         # how far back from the end to look for one
QUIET_RATIO = 0.12         # how far under the local peak counts as a gap
QUIET_FLOOR = 0.02         # and, with no peak to go by, the level silence sits under


try:
    import numpy as _np
except ImportError:                     # a Qwen-only install need not have it
    _np = None


def _loudest(samples, start, stop):
    """Peak absolute value in a block.

    max() and min() over a slice, not a loop over samples. This is the one
    inner loop of all of this, and written as a Python loop it held the GIL
    for long enough per streamed piece that the engine's own HTTP threads
    starved: /health and /state replies were still being written when the
    panel and the game had given up on them (2026-10, 31 aborted replies in
    one log). The builtins walk the slice in C.
    """
    block = samples[start:stop]
    if not len(block):
        return 0.0
    hi, lo = max(block), min(block)
    return float(hi if hi >= -lo else -lo)


def _window_peaks(samples, first, count, win):
    """Peaks of `count` back-to-back windows of `win` samples from `first`.

    One vectorised pass with numpy where it is installed; window by window
    through _loudest where it is not. Same numbers either way.
    """
    if count <= 0:
        return []
    if _np is not None:
        try:
            block = _np.asarray(samples[first:first + count * win], dtype=_np.float32)
            if block.size == count * win:
                return _np.abs(block).reshape(count, win).max(axis=1).tolist()
        except (TypeError, ValueError):
            pass
    return [_loudest(samples, first + i * win, first + (i + 1) * win)
            for i in range(count)]


def as_floats(samples):
    """Mono float samples as an array('f'), without a Python loop where possible.

    Engines hand back a numpy array (Pocket), a ctypes float array (Qwen) or an
    array already. array.extend() with anything but another 'f' array walks it
    one Python float at a time -- a minute of speech is 1.4 million of them --
    so the conversion goes through bytes instead.
    """
    import array

    if isinstance(samples, array.array) and samples.typecode == "f":
        return samples
    out = array.array("f")
    if _np is not None:
        try:
            out.frombytes(_np.ascontiguousarray(samples, dtype=_np.float32).tobytes())
            return out
        except (TypeError, ValueError):
            out = array.array("f")
    try:
        view = memoryview(samples)
        if view.format in ("f", "<f") and view.itemsize == 4:
            out.frombytes(view.cast("B").tobytes())
            return out
    except TypeError:
        pass
    out.extend(samples)
    return out


def pcm16(samples):
    """Float samples to 16-bit little-endian PCM bytes, clipping-guarded.

    The scale is exactly 1.0 for anything peaking at or under 1.0 -- a guard,
    not a normaliser; see the note under quiet_span. Truncates toward zero, as
    int() did when this was a loop.
    """
    if _np is not None:
        a = _np.asarray(samples, dtype=_np.float32)
        if a.size == 0:
            return b""
        peak = float(_np.abs(a).max())
        scale = (0.99 / peak) if peak > 1.0 else 1.0
        return (_np.clip(a.astype(_np.float64) * scale, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    import array
    import sys

    peak = max(max(samples, default=0.0), -min(samples, default=0.0))
    scale = (0.99 / peak) if peak > 1.0 else 1.0
    pcm = array.array("h", (int(max(-1.0, min(1.0, s * scale)) * 32767) for s in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def trim_head(samples, rate=24000, window=QUIET_WINDOW, floor=QUIET_FLOOR):
    """How many samples of dead air sit before the first word. Pure latency.

    The engine leaves a variable run of nothing at the top of a generation --
    measured between 0.00s and 0.86s across eight consecutive messages -- and
    none of it was asked for. On the first piece it is the delay between the
    answer arriving and the voice starting, which is the one number 'instant'
    playback exists to make small.

    Absolute rather than relative, because at the head there is no peak yet to
    be relative to.
    """
    win = max(1, int(window * rate))
    total = len(samples) // win
    # A block of windows at a time: the first word is usually within a second,
    # and measuring a whole minute of buffer to find that out would be waste.
    for first in range(0, total, 32):
        peaks = _window_peaks(samples, first * win, min(32, total - first), win)
        for i, loudest in enumerate(peaks):
            if loudest > floor:
                return (first + i) * win
    return total * win


def quiet_span(samples, rate=24000, search=QUIET_SEARCH, window=QUIET_WINDOW,
               ratio=QUIET_RATIO, budget=0.0):
    """Where to end this piece, and where the next one picks up.

    Returns (cut, resume): emit samples[:cut], throw samples[cut:resume] away,
    carry on from resume. **(0, 0) means there is no gap to cut on**, which is
    not a failure and must not be treated as one: the right answer then is to
    gather more audio and ask again, which pushes the seam to the next natural
    breath instead of putting it in the middle of a word. Speech runs out of
    breath every few seconds, so this converges quickly.

    The thrown-away part is the whole point, and it is new. A pause in speech
    is far longer than the 40ms window used to find it, so cutting at the
    window left the rest of the pause split across the seam -- some trailing
    this piece, the remainder leading the next -- and *both* halves were kept,
    with the player's handover added in between. Three silences where the
    engine made one. Measured across eight consecutive messages: a median
    heard gap of 0.42s and a worst of 0.88s, landing by preference on commas
    and full stops, because the quietest window is exactly where punctuation
    is. A comma held for 0.88s is heard as the end of a thought, which is the
    stumble this was supposed to prevent.

    So the whole silent run is found, and `budget` -- how much silence the
    handover will supply by itself -- is taken out of it. What remains stays at
    the end of the piece. The gap that is heard is then the pause the engine
    made, once. A pause shorter than the budget cannot be shortened below it
    without clipping speech, so it is left to the handover alone.
    """
    n = len(samples)
    win = max(1, int(window * rate))
    back = min(n, int(search * rate))
    if back < win * 2:
        return 0, 0

    # Every window on the grid the search uses, extended both ways to the ends
    # of the buffer, so the widening below reads the same peaks rather than
    # measuring the same samples twice. Window i starts at base + i * win.
    base = (n - back) % win
    peaks = _window_peaks(samples, base, (n - base) // win, win)
    first = (n - back - base) // win          # where the search starts
    searched = peaks[first:]

    peak = max(searched)
    best = min(searched)
    at = first + searched.index(best)         # the first quietest, as before

    if peak <= 0.0:
        return n, n                   # all silence: anywhere will do, take it all
    # The quietest window is not automatically a good cut -- in a continuous
    # stretch of speech one of them is still the quietest, and cutting there is
    # exactly the stutter this exists to avoid. Hence the ratio test.
    if best > peak * ratio:
        return 0, 0

    # Widen from that window to the whole breath it sits in, so what gets
    # dropped is the entire pause rather than 40ms out of the middle of it.
    level = peak * ratio
    lo_i, hi_i = at, at
    while lo_i - 1 >= 0 and peaks[lo_i - 1] <= level:
        lo_i -= 1
    while hi_i + 1 < len(peaks) and peaks[hi_i + 1] <= level:
        hi_i += 1
    lo, hi = base + lo_i * win, base + (hi_i + 1) * win

    keep = max(0, (hi - lo) - int(budget * rate))
    return lo + keep, hi


# On loudness, since it is the obvious next worry: the sister project had to
# take ONE gain for a whole utterance, because normalising each piece to its own
# peak makes the loudness breathe from one to the next -- the same defect as the
# voice changing, wearing different clothes. We do not have that problem and
# should not acquire it. write_wav's scale is exactly 1.0 for anything peaking
# under 1.0, so it is a clipping guard rather than a normaliser, and every piece
# of one generation comes out at the level the engine gave it. Leave it that way.


# --------------------------------------------------------------------------
# talking to the server
# --------------------------------------------------------------------------

SPOKEN_PATH = os.path.join(LOG_DIR, "spoken.json")
SPOKEN_KEEP = 24


def already_spoken(text, remember=True):
    """True if this exact text went out recently.

    Shared by the hook and the transcript watcher so that whichever sees a
    message first, it is only ever said once.
    """
    import hashlib

    digest = hashlib.sha1(text.strip().encode("utf-8", "replace")).hexdigest()
    try:
        with open(SPOKEN_PATH, encoding="utf-8") as fh:
            recent = json.load(fh)
    except (OSError, ValueError):
        recent = []
    if digest in recent:
        return True
    if remember:
        recent.append(digest)
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(SPOKEN_PATH, "w", encoding="utf-8") as fh:
                json.dump(recent[-SPOKEN_KEEP:], fh)
        except OSError:
            pass
    return False


# --------------------------------------------------------------------------
# what the chosen engine can actually read
# --------------------------------------------------------------------------

# Cyrillic, all four blocks of it. Basic and Supplement cover Russian and
# Bulgarian; the other two are historic and Slavonic letters that turn up in
# quoted scripture, which this repo's neighbouring projects are full of.
CYRILLIC = re.compile(r"[Ѐ-ԯⷠ-ⷿꙀ-ꚟ]")
# Is there anything left worth saying -- a letter or a digit in any alphabet.
HAS_WORDS = re.compile(r"[^\W\d_]|\d", re.UNICODE)

# Pocket TTS does not fail on Cyrillic, which would be easy to handle. It runs
# away: measured here, "Сега ще проверя как звучи това на български." -- three
# seconds of speech -- came back as 11.4 seconds of audio. So a Bulgarian line
# left in costs the listener eleven seconds of babbling and buries whatever
# English was around it.
#
# Hence this. The unreadable part is taken out before synthesis, and the
# listener is told it happened, because an answer that quietly lost half of
# itself is the one outcome worse than a bad accent. Both lines are written to
# be heard: no abbreviations, and the count first, since that is the part
# somebody actually wants -- "a couple of words" and "most of the answer" are
# different situations and the number is what tells them apart.
SKIPPED_SOME = ("There are {n} Cyrillic characters in this that I can't speak "
                "out, so I'll skip them, just so you know.")
SKIPPED_SOME_ONE = ("There's one Cyrillic character in this that I can't speak "
                    "out, so I'll skip it, just so you know.")
SKIPPED_ALL = ("That line is all Cyrillic, and {engine} can't read it at "
               "all. Switch to Qwen and I'll say it properly.")

# The engines that cannot read Cyrillic, as the warning names them out loud.
# Breeze reads English and Chinese and nothing else, by its own model card.
NO_CYRILLIC = {"pocket": "Pocket TTS", "breeze": "Breeze"}


def can_read(text, state=None, engine=None):
    """Whether the engine would read every word of this, dropping none."""
    engine = engine or engine_of(state)
    return engine not in NO_CYRILLIC or not text or not CYRILLIC.search(text)


def speakable(text, engine=None, state=None):
    """Text the chosen engine can read, with a spoken note about what it can't.

    Returns (speech, note). `note` is None when nothing was dropped, and a line
    for the log otherwise -- the listener is told inside `speech` itself, as a
    preface, so that the warning and the answer are one generation and the
    voice does not change between them.

    Only Pocket TTS and Breeze are limited this way. The Qwen road reads
    Cyrillic -- with a Russian accent on Bulgarian, see docs/languages.md -- so
    nothing here applies to it and nothing here should grow to.
    """
    engine = engine or engine_of(state)
    if engine not in NO_CYRILLIC or not text or not CYRILLIC.search(text):
        return text, None

    dropped = len(CYRILLIC.findall(text))
    # Whole words rather than single letters. Cutting the Cyrillic out of a
    # mixed word leaves a stump the model reads as a different word, which is
    # a worse thing to hear than the word being gone.
    kept = [w for w in text.split() if not CYRILLIC.search(w)]
    rest = " ".join(kept)
    rest = re.sub(r"\s+([,.;:!?…])", r"\1", rest)
    rest = re.sub(r"\s{2,}", " ", rest).strip(" -–—,;:")

    if not HAS_WORDS.search(rest):
        return (SKIPPED_ALL.format(engine=NO_CYRILLIC[engine]),
                f"all Cyrillic ({dropped} characters) -- nothing to say")
    preface = SKIPPED_SOME_ONE if dropped == 1 else SKIPPED_SOME.format(n=dropped)
    return f"{preface} {rest}", f"skipped {dropped} Cyrillic characters"


def speech_for(text, state):
    """What should actually be said for an assistant message, if anything.

    The TL;DR is the whole contract. Write one and that alone is spoken, which
    is what you want for an answer whose body is full of paths and flags.
    Leave it out and the message is read in full -- which covers both the short
    line of narration before a command and the deliberately spoken-word answer
    that has no business being summarised.
    """
    summary = extract_summary(text)
    if summary:
        return clean_text(summary, state.get("maxChars", 600)), "summary"
    spoken = clean_text(text, state.get("fullMaxChars", 4000))
    kind = "narration" if len(spoken) <= state.get("narrateMaxChars", 240) else "whole answer"
    return spoken, kind


# Claude Code writes the model's working-out into a thinking block, and the
# client renders it on screen with everything else. On Fable 5.1 it is not
# working-out at all: that model puts its between-tool narration there instead,
# and never beside a text block -- across every transcript on this machine, not
# one of its 354 responses carried both. So what the screen showed and what this
# tool said had quietly drifted apart, and about half of the lines written were
# never spoken. That is the bug this exists to close.
#
# Saying all of it is not the answer either. On Opus 5 the same block really is
# the scratchpad: a median of 495 characters against Fable's 214, running to
# 35,854 at the worst, in paragraphs, arguing with itself. Two tests separate
# them, and both were measured over 7,800 real blocks rather than guessed:
#
#   *The response said nothing else.* Blocks are grouped by the API response
#   they came from, not by transcript line. If a text block sits in the same
#   response then that text is the line, and the reasoning behind it is not for
#   saying. This alone excludes half of Opus 5's thinking and none of Fable
#   5.1's, which never pairs the two.
#
#   *It reads as one spoken line.* A single paragraph, short, with no code in
#   it, asking itself nothing, and free of the words somebody uses while still
#   making their mind up.
#
# Together they recover 97% of Fable 5.1's narration and let through under a
# quarter of Opus 5's thinking -- the quarter that already reads as narration.
#
# Ordering is what makes the first test cheap while tailing a file. Thinking
# always precedes text within a response (3,094 of 3,094 measured) and every
# tool call comes after both (10,361 of 10,361), so a reader going backwards
# meets the text first, and a reader going forwards can hold the thinking until
# the tool call proves nothing else was said.

# 500 rather than a rounder 250: Fable 5.1's longest measured narration was 453
# characters, and a ceiling that cuts real lines in half is worse than one that
# occasionally lets a long one through.
THINKING_MAX_CHARS = 500

# The words a mind changes course with. Narration says what is about to happen;
# deliberation is still deciding, and that belongs on the screen only.
_DELIBERATING = re.compile(
    r"""(?ix)
    (?: ^ | [.!?]["')\]]? \s+ ) \s*
    (?: wait | hmm+ | actually | ok | okay | alternatively | maybe | perhaps
      | let \s+ me \s+ think | hold \s+ on | i \s+ wonder
      | no[,.] | so[,.] | right[,.] )
    \b""")


def reads_as_narration(text):
    """Whether a thinking block is a line that was meant to be heard.

    Deliberately cheap and conservative. Every test here throws away something
    nobody would read aloud anyway, so a block that fails is not lost -- it is
    still on the screen, which is where working-out belongs.
    """
    text = (text or "").strip()
    if not text or len(text) > THINKING_MAX_CHARS:
        return False
    if "\n" in text:                 # more than one paragraph: it is reasoning
        return False
    if "`" in text:                  # code, a path, an identifier
        return False
    if "?" in text:                  # a question it is putting to itself
        return False
    return not _DELIBERATING.search(text)


def thinking_speech(text, state):
    """What to say for a thinking block that stood alone, or nothing at all.

    The caller owns the harder half of the decision -- proving the response
    said nothing else. This only judges the words.
    """
    if not state.get("speakThinking", True):
        return ""
    if not reads_as_narration(text):
        return ""
    return clean_text(text, state.get("fullMaxChars", 4000))


_RECOMMENDED = re.compile(r"\(\s*recommended\s*\)", re.I)
_ORDINALS = ("First", "Second", "Third", "Fourth", "Fifth", "Sixth")
_COUNTS = ("no", "one", "two", "three", "four", "five", "six")


def _listed(items):
    """'a, b or c' -- the way a person reads a short list out."""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " or " + items[-1]


def question_speech(tool_input, max_chars=4000):
    """What to say when a turn stops and waits for an answer.

    An AskUserQuestion turn is a lone tool_use block: no text block anywhere in
    the message, with the question, the options and their descriptions all
    inside the tool's input. Both readers here look only at text blocks, so the
    one message that stops the session dead until it is answered was the one
    message never spoken -- and from the listener's side, a session that goes
    quiet and stays quiet is indistinguishable from the voice breaking again.

    Only the question and the option labels are said. The descriptions are
    written for eyes that can compare them side by side; read out they are a
    paragraph of trade-offs each, and by the third nobody remembers the first.
    The labels are enough to know an answer is wanted and roughly what about --
    the detail is on screen, where it can be re-read as often as it takes.
    """
    rows = [q for q in ((tool_input or {}).get("questions") or [])
            if isinstance(q, dict) and (q.get("question") or "").strip()]
    if not rows:
        return ""

    n = len(rows)
    count = _COUNTS[n] if n < len(_COUNTS) else str(n)
    parts = ["A question for you." if n == 1 else f"{count.capitalize()} questions for you."]
    for i, q in enumerate(rows):
        if n > 1 and i < len(_ORDINALS):
            parts.append(_ORDINALS[i] + ".")
        parts.append(q["question"].strip())
        labels = [_RECOMMENDED.sub("recommended", o.get("label") or "").strip()
                  for o in (q.get("options") or []) if isinstance(o, dict)]
        labels = [l for l in labels if l]
        if labels:
            parts.append(("Any of: " if q.get("multiSelect") else "The options are: ")
                         + _listed(labels) + ".")
    return clean_text(" ".join(parts), max_chars)


# Claude Code's own wording, handed to the hook as `message`. Both of the ones
# it sends often speak of her in the third person -- right on screen beside her
# name, wrong in her own mouth -- so those two get answers of her own and
# everything else is repeated as it came.
_NEEDS_PERMISSION = re.compile(r"needs\s+your\s+permission", re.I)
_WAITING_FOR_YOU = re.compile(r"waiting\s+for\s+your\s+input", re.I)


def _tool_phrase(name):
    """A tool's name as somebody would say it, not as it is spelled.

    'WebFetch' read letter-perfect comes out as one mashed word, and an MCP
    tool is called mcp__Claude_Browser__navigate, which is unsayable. Keep the
    last part, and put the spaces where a reader would.
    """
    if "__" in name:
        name = name.rsplit("__", 1)[-1]
    name = name.replace("_", " ")
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def notification_speech(message, subject="", max_chars=240):
    """What to say when Claude Code raises a notification.

    These are the moments the session stops and waits: a tool asking to be
    allowed, a turn gone quiet with a question in it. They are exactly the
    moments somebody has looked away -- which is the whole point of a voice --
    and nothing else here would ever say them, because a notification is not a
    message and never reaches the transcript for the watcher to find.

    One short sentence on purpose. It is an interruption, and its job is to say
    that something wants an answer, not to explain what: the dialog is on
    screen, where it can be read as slowly as it takes.
    """
    message = (message or "").strip()
    if not subject:
        # Claude Code often names the tool itself: "Claude needs your permission
        # to use Bash". That is the only place the hook can learn it from now --
        # it used to dig the pending tool call out of the transcript.
        named = re.search(r"permission\s+to\s+use\s+(.+?)\s*\.?$", message, re.I)
        if named:
            subject = _tool_phrase(named.group(1))
    if _NEEDS_PERMISSION.search(message):
        return "I need your permission" + (" to use " + subject + "." if subject else ".")
    if _WAITING_FOR_YOU.search(message):
        return "I'm still waiting for you."
    return clean_text(message, max_chars)


NOTIFIED_PATH = os.path.join(LOG_DIR, "notified.json")


def notification_due(speech, within=10.0):
    """False if this exact announcement went out a moment ago.

    Deliberately not already_spoken: repeating is normal here. Three permission
    prompts in a row are three separate halts and each one is worth hearing, so
    this forgets after a few seconds. What it does swallow is the double-fire
    you get when the same hook is registered in a project's settings and in
    yours -- two calls, same instant, one dialog.
    """
    import time

    now = time.time()
    try:
        with open(NOTIFIED_PATH, encoding="utf-8") as fh:
            recent = json.load(fh)
    except (OSError, ValueError):
        recent = {}
    if not isinstance(recent, dict):
        recent = {}
    last = recent.get(speech)
    if isinstance(last, (int, float)) and 0 <= now - last < within:
        return False
    recent = {k: v for k, v in recent.items()
              if isinstance(v, (int, float)) and now - v < 3600}
    recent[speech] = now
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(NOTIFIED_PATH, "w", encoding="utf-8") as fh:
            json.dump(recent, fh)
    except OSError:
        pass
    return True


CLAUDE_MD = os.path.expanduser(os.path.join("~", ".claude", "CLAUDE.md"))
NOTES_PATH = os.path.join(ROOT, "speaking-notes.md")
# The markers versions before 2.0 wrapped their block in, inside the user's
# own ~/.claude/CLAUDE.md. Kept only so remove_notes can find that block and
# take it out again: nothing writes into CLAUDE.md any more.
_NOTE_OPEN = "<!-- claude-voice -->"
_NOTE_CLOSE = "<!-- /claude-voice -->"


def speaking_notes():
    """The standing rules for writing to be heard, from speaking-notes.md.

    Handed to every session by its SessionStart hook (see session_note) rather
    than spliced into the user's CLAUDE.md. That file is the user's own; this
    used to rewrite a block of it on every engine start and every voice change,
    which is the kind of reaching into somebody else's files that made the whole
    tool look like it was scraping Claude (Anton, 2026-10-17). The hook says the
    same thing, and says it again after a compaction, without touching a file.
    HTML comments are left out: they are notes for whoever edits the file.
    """
    try:
        with open(NOTES_PATH, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ""
    return re.sub(r"<!--.*?-->\s*", "", text, flags=re.S).strip()


def persona_note(voice):
    """Who the session is heard as: the voice's name and, if it has one, its
    persona.

    It says more than the name on purpose. A session told only "the voice is
    Abby" still writes as though handing finished text to a component further
    down the line; one told the answers are *said* in that voice, to somebody
    listening, has a reason to write differently. Given the situation instead
    of a rule, it can judge.

    What keeps it from going wrong is scope, not restraint, because personas
    describe a voice and not a manner. Max's reads "brave, driving, motivating",
    and a session that took that for a writing instruction would deliver a stack
    trace as a pep talk. Hence: colour what is heard, leave the body alone.

    No pronouns for the voice, deliberately -- this is generated for every voice
    in the catalogue, including ones nobody here has met. Voices with no persona
    get the first two paragraphs only: "let the character through" reads as an
    instruction to invent one when there is nothing above it to point at.
    """
    name = voice["name"]
    persona = (voice.get("persona") or "").rstrip(".")
    paras = [
        f"The voice is currently **{name}**" + (f" — {persona}." if persona else "."),
        f"Your lines are spoken out loud in that voice, so it is nice to play {name} a "
        f"little. Don't be caught out when the user calls you that — to them it is your "
        f"name, and answering to it costs nothing.",
    ]
    if persona:
        paras.append(
            f"They are heard as {name}, so let them sit in that spirit: take the manner from "
            f"the description above and follow it without overdoing it. A calm voice can be "
            f"calming. A bright one can be bright. Where it belongs is the part that is "
            f"actually heard, not the body of an answer — a character reading somebody a "
            f"stack trace helps nobody. None of this is a rule; if the user would rather "
            f"have plain Claude, that is the answer.")
    return "\n\n".join(paras)


def remove_notes(path=None):
    """Take the block older versions spliced into CLAUDE.md back out.

    Everything between the claude-voice markers goes, markers included, and
    every word around it stays exactly as it was. Nothing else in the file is
    touched; no markers, no edit. Called by the installer (once, to move an old
    install over to the hook) and by the uninstaller. Returns True if it cut.
    """
    path = path or CLAUDE_MD
    try:
        # newline="": read and written as it is, so a file with LF line ends
        # does not come back with CRLF ones (Windows' text mode) or the reverse.
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except (OSError, ValueError):          # ValueError: not UTF-8 -- not ours to rewrite
        return False
    start, end = text.find(_NOTE_OPEN), text.find(_NOTE_CLOSE)
    if start == -1 or end == -1 or end < start:
        return False
    nl = "\r\n" if "\r\n" in text else "\n"
    before = text[:start].rstrip("\r\n")
    after = text[end + len(_NOTE_CLOSE):].lstrip("\r\n")
    fresh = before + (nl + nl if before and after else "") + after
    if fresh and not fresh.endswith("\n"):
        fresh += nl
    temp = f"{path}.{os.getpid()}.new"
    try:
        with open(temp, "w", encoding="utf-8", newline="") as fh:
            fh.write(fresh)
        os.replace(temp, path)
    except OSError:
        try:
            os.remove(temp)
        except OSError:
            pass
        return False
    return True


# --------------------------------------------------------------------------
# what a session is told, and when
# --------------------------------------------------------------------------
#
# CLAUDE.md says a session is heard and in whose voice. It cannot say what the
# voice will do with a mood or a laugh, because that is the engine's to answer
# and the engine can change in the middle of a conversation -- while CLAUDE.md
# is read once, at the start. So the hook says it instead: when a session
# begins, and again at the next prompt if it has changed since.
#
# Only what will really happen. The assistant app next door settled this for
# itself first: its voice's mood field appears only while an engine that
# performs it is loaded, because a field that does nothing is worse than none
# -- it gets written into, and believed. A session told it may laugh on an
# engine that cannot is the same mistake.

SESSION_NOTE_HEAD = "claude-voice, as of now:"

# The entrypoints of runs nobody is sitting in front of: claude -p, an SDK
# caller. The watcher reads one off each transcript; a hook finds it in its own
# environment, where Claude Code puts it.
HEADLESS_ENTRYPOINTS = ("sdk-cli",)


def session_note(state=None):
    """(key, text): what a session should know about the voice right now.

    The whole of what a Claude session is told about being heard, delivered as
    hook additionalContext: at SessionStart, and at the next UserPromptSubmit
    whenever the key has moved. It used to be split -- the standing rules and
    the current voice spliced into ~/.claude/CLAUDE.md, only the engine's
    abilities here -- and the splice rewrote the user's file on every engine
    start and voice change. Now nothing is written anywhere.

    The key is the state the text describes plus a checksum of the text itself,
    so it moves whenever the words would: an engine swapped, a voice changed,
    the voice switched on, or the wording changed by an update. The abilities
    are built from ENGINE_CAN rather than from engine names, so an engine added
    later is described by what it can do without anyone writing it a paragraph.

    It encourages rather than rations. Anton, hearing the first of these:
    "dont shy playing with the mood". A session told that most lines want no
    mood took it at its word and wrote almost none.
    """
    import zlib

    state = state if state is not None else load_state()
    engine = engine_of(state)
    on = bool(state.get("enabled"))
    voice = None
    try:
        voice = resolve(state.get("voice"), state.get("source"), state)[0]
        who = voice["name"]
    except Exception:
        who = state.get("voice") or ""
    speaker = (f"speaking as {who} through {engine_name(engine)}" if who
               else f"speaking through {engine_name(engine)}")
    moods = engine_can(engine, "instruction", state)
    sounds = engine_can(engine, "events")

    if not on:
        text = (f"{SESSION_NOTE_HEAD} the voice is off, so nothing written is heard until "
                "it is switched on again. You will be told when it is.")
    else:
        if not moods and not sounds:
            text = (f"{SESSION_NOTE_HEAD} the voice is on, {speaker}, which reads the words "
                    "exactly as written, with no moods and no sounds. A direction in brackets "
                    "such as (sad) or (laugh) is taken out rather than performed, so leave "
                    "them out.")
        else:
            what = "acts as well as reads" if moods and sounds else (
                "takes a mood but makes no sounds" if moods else "makes sounds but takes no mood")
            lines = []
            if moods:
                lines.append("- A mood sets how a whole spoken message sounds. Put it in "
                             "brackets as the first thing in the TL;DR"
                             + (", or at the front of a short line between tool calls: "
                                if _lines_heard(state) else ": ")
                             + " ".join(f"({m})" for m in MOODS)
                             + ". One per message; it is never read out.")
            if sounds:
                lines.append("- A sound goes in brackets exactly where it happens: "
                             + " ".join(f"({s})" for s in sounds) + ".")
            else:
                lines.append("- Sounds such as (laugh) are taken out on this engine rather "
                             "than performed, so leave them out.")
            if moods and sounds:
                play = ("- Don't be shy with them. A laugh at something funny, a sigh at a "
                        "long failure, a whisper for a secret, delight when it finally works: "
                        "that is what this voice is for.")
            elif moods:
                play = ("- Don't be shy with it: delight when something works, calm for bad "
                        "news, a whisper for a secret.")
            else:
                play = ("- Don't be shy with them: a laugh at something funny, a sigh at a "
                        "long failure.")
            noun = "a mood or a sound" if moods and sounds else ("a mood" if moods else "a sound")
            lines.append(f"{play} The one way to spoil it is {noun} on every single line.")
            text = (f"{SESSION_NOTE_HEAD} the voice is on, {speaker}, which {what}.\n\n"
                    + "\n".join(lines))
        if _lines_heard(state):
            text += ("\n\nThe short lines you write between tool calls are spoken too, exactly "
                     "as written, with nothing on screen to explain them. Give each one a verb: "
                     "\"Now I'm writing the fix\", not \"Now the fix itself\".")
        else:
            text += ("\n\nOnly the end of a turn is heard: its `## TL;DR`, or the whole final "
                     "message when it has none. Lines between tool calls are not spoken.")
        if voice:
            text += "\n\n" + persona_note(voice)
        notes = speaking_notes()
        if notes:
            text += "\n\n" + notes

    key = f"{'on' if on else 'off'}|{engine}|{who}|{zlib.crc32(text.encode('utf-8')):08x}"
    return key, text


def _lines_heard(state):
    """Whether the lines between tool calls reach the voice at all. See
    narrateFromTranscript in DEFAULTS."""
    return bool(state.get("narrate", True) and state.get("narrateFromTranscript", False))


# --------------------------------------------------------------------------
# the hooks, as Claude Code's settings.json holds them
# --------------------------------------------------------------------------
#
# The only way into Claude Code. Each one runs speak_hook.py, which hands what
# Claude Code gave it to the engine over HTTP (POST /hook) and goes. Written,
# checked and removed from here so the installer, the uninstaller and 'status'
# cannot disagree about what "our hooks" are: any hook whose command names
# speak_hook.py.

# The events, and what each is for. Its docstring in speak_hook.py says more.
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "Notification", "PreToolUse")

# Never a bare "python speak_hook.py". Python exits with 2 when the script it is
# handed is not there, and 2 is the one exit code Claude Code reads as "block":
# every prompt refused, every tool call refused -- all because this folder was
# moved, which is exactly when nobody is thinking about hooks (2026-09-25). So
# the hook is a line of Python that runs speak_hook.py the way "python
# speak_hook.py" would -- same argv, its own folder first on the path and never
# the project's -- and exits 0 without a word when the file is gone.
HOOK_GUARD = ("import os,runpy,sys;h=sys.argv[1];os.path.isfile(h) or sys.exit(0);"
              "sys.argv=sys.argv[1:];sys.path[:]=[os.path.dirname(h)]+[p for p in sys.path if p];"
              "runpy.run_path(h,run_name='__main__')")
HOOK_SCRIPT = os.path.join(ROOT, "speak_hook.py")
CLAUDE_SETTINGS = os.path.expanduser(os.path.join("~", ".claude", "settings.json"))


def bash_word(path):
    """A path as bash reads it. Claude Code runs a hook's command through bash
    on Windows, and bash reads a backslash as an escape: C:\\Users\\x arrives as
    C:Usersx, "command not found", on every call -- which is how the hooks were
    broken for five weeks without a sound. So forward slashes, and quoted the
    way bash quotes when the path holds anything bash would read as syntax."""
    path = path.replace("\\", "/")
    if not re.search(r"[^A-Za-z0-9_./:+,=@-]", path):
        return path
    return '"' + path.replace("$", "\\$").replace("`", "\\`") + '"'


def hook_command(python=None, hook=None):
    """The one command line every one of our hooks runs."""
    return (f'{bash_word(python or _python())} -c "{HOOK_GUARD}" '
            f'{bash_word(hook or HOOK_SCRIPT)}')


def hook_entries(command, narrate_lines=False):
    """{event: [entry]} -- what install_hooks writes.

    SessionStart and UserPromptSubmit print the session note, so Claude Code has
    to wait for them; they take a few hundredths of a second, and the timeout is
    only what a hang could cost. The rest print nothing and are "async": Claude
    Code starts them and does not wait, so a slow or absent engine can never
    hold up a turn ending or a tool starting.

    PreToolUse is matched to AskUserQuestion alone -- the one tool whose input
    is worth hearing -- unless the lines between tool calls are to be read off
    the transcript, which needs it on every tool. Notification takes no matcher:
    its matcher is a regex over the notification kind, and "*" is not a valid
    one. An entry missing a field its event expects can invalidate the whole
    hooks block, silencing all of them.
    """
    def hook(timeout, background):
        h = {"type": "command", "command": command, "timeout": timeout}
        if background:
            h["async"] = True
        return h

    return {
        "SessionStart": [{"hooks": [hook(5, False)]}],
        "UserPromptSubmit": [{"hooks": [hook(5, False)]}],
        "Stop": [{"hooks": [hook(15, True)]}],
        "Notification": [{"hooks": [hook(15, True)]}],
        "PreToolUse": [{"matcher": "*" if narrate_lines else "AskUserQuestion",
                        "hooks": [hook(15, True)]}],
    }


def _ours(entry):
    return any("speak_hook.py" in ((h or {}).get("command") or "")
               for h in (entry or {}).get("hooks") or [] if isinstance(h, dict))


def _read_settings(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            settings = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(settings, dict):
        raise ValueError(f"{path} is not a JSON object; not touching it")
    return settings


def _write_settings(path, settings):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp = f"{path}.{os.getpid()}.new"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    # Retried as save_state does: Windows refuses the swap while another
    # process (Claude Code itself, reading its settings) has the file open.
    import time

    for attempt in range(10):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 9:
                try:
                    os.remove(temp)
                except OSError:
                    pass
                raise
            time.sleep(0.02 * (attempt + 1))


def _strip_ours(hooks, only=None):
    """hooks with every entry of ours taken out, and any event left empty by
    that dropped. Entries that are not ours are kept exactly as they were.
    With `only` (a speak_hook.py path), entries of other copies count as not ours."""
    mark = only.replace("\\", "/").lower() if only else None

    def theirs(entry):
        return mark and not any(mark in ((h or {}).get("command") or "").replace('"', "").lower()
                                for h in (entry or {}).get("hooks") or [] if isinstance(h, dict))

    def ours_alone(h):
        return isinstance(h, dict) and "speak_hook.py" in (h.get("command") or "") \
            and (not mark or mark in (h.get("command") or "").replace('"', "").lower())

    out = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            out[event] = entries
            continue
        kept = []
        for e in entries:
            if not _ours(e) or theirs(e):
                kept.append(e)
                continue
            # An entry somebody shares with one of their own hooks keeps theirs.
            rest = [h for h in (e.get("hooks") or []) if not ours_alone(h)]
            if rest:
                kept.append({**e, "hooks": rest})
        if kept or not entries:
            out[event] = kept
    return out


def install_hooks(path=None, command=None, narrate_lines=None):
    """Put our hooks into a Claude Code settings file. Returns the events.

    Idempotent: ours are taken out first, wherever they were, and the current
    set written, so running it twice changes nothing and an event dropped from
    HOOK_EVENTS in a later version is cleaned up. Everybody else's hooks, and
    everything else in the file, are left as they were. Written whole and moved
    into place, so Claude Code never reads half a file.
    """
    path = path or CLAUDE_SETTINGS
    if narrate_lines is None:
        narrate_lines = _lines_heard(load_state())
    settings = _read_settings(path)
    hooks = settings.get("hooks")
    hooks = _strip_ours(hooks if isinstance(hooks, dict) else {})
    for event, entries in hook_entries(command or hook_command(), narrate_lines).items():
        hooks[event] = list(hooks.get(event) or []) + entries
    settings["hooks"] = hooks
    _write_settings(path, settings)
    return list(HOOK_EVENTS)


def remove_hooks(path=None, only=None):
    """Take our hooks out of a settings file. True if there were any.

    No file, or none of ours in it, is no write at all. `only`: just the hooks
    that run that speak_hook.py -- an uninstaller takes its own copy's hooks
    and leaves a checkout's, which are somebody else's.
    """
    path = path or CLAUDE_SETTINGS
    try:
        settings = _read_settings(path)
    except (OSError, ValueError):
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    fresh = _strip_ours(hooks, only)
    if fresh == hooks:
        return False
    if fresh:
        settings["hooks"] = fresh
    else:
        settings.pop("hooks", None)
    _write_settings(path, settings)
    return True


def hook_health(paths=None, log_path=None):
    """How this tool's Claude Code hooks are wired, for 'status' to say.

    Returns {"events": [...], "files": [...], "backslashed": bool,
    "unguarded": bool, "missing": [...], "last": "YYYY-MM-DD HH:MM:SS" or None}.
    Read off the settings files and the hook's own trace; nothing is run.
    `backslashed` is the one that matters: bash takes the backslashes in
    C:\\Users\\... for escapes, so such a hook fails on every single call
    without a word. `unguarded` is the other: a bare "python speak_hook.py"
    exits 2 the day this folder moves, which Claude Code reads as "block".
    """
    paths = paths or [CLAUDE_SETTINGS, os.path.join(ROOT, ".claude", "settings.json")]
    found = {"events": [], "files": [], "backslashed": False, "unguarded": False,
             "missing": [], "last": None}
    for path in paths:
        try:
            with open(path, encoding="utf-8-sig") as fh:
                hooks = (json.load(fh) or {}).get("hooks") or {}
        except (OSError, ValueError, AttributeError):
            continue
        mine = False
        for event, entries in hooks.items() if isinstance(hooks, dict) else ():
            for entry in entries or []:
                for hook in (entry or {}).get("hooks") or []:
                    command = (hook or {}).get("command") or ""
                    if "speak_hook.py" not in command:
                        continue
                    mine = True
                    if event not in found["events"]:
                        found["events"].append(event)
                    if "\\" in command:
                        found["backslashed"] = True
                    if "run_path" not in command:
                        found["unguarded"] = True
        if mine:
            found["files"].append(path)
    if found["events"]:
        found["missing"] = [e for e in HOOK_EVENTS if e not in found["events"]]
    try:
        with open(log_path or os.path.join(LOG_DIR, "hook.log"), encoding="utf-8",
                  errors="replace") as fh:
            fired = [ln for ln in fh.readlines()[-400:] if "] fired: " in ln]
        found["last"] = fired[-1][1:20] if fired else None
    except OSError:
        pass
    return found


# --------------------------------------------------------------------------
# an answer that found nobody home
# --------------------------------------------------------------------------
#
# A Stop hook that finds the engine down starts it, but cannot wait the half
# minute a model takes to load -- it must never hold anything up. So it leaves
# the answer here, in this tool's own log folder, and the engine says whatever
# is still fresh (catchupSeconds) once it is up. This is what the transcript
# watcher's remembered offsets used to do, without reading anybody's files.

PENDING_PATH = os.path.join(LOG_DIR, "pending.jsonl")
PENDING_MAX_BYTES = 256 * 1024


def spool(body):
    """Keep one /hook body for an engine that is not up yet."""
    import time

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        try:
            if os.path.getsize(PENDING_PATH) > PENDING_MAX_BYTES:
                return False                  # nobody has started an engine in ages
        except OSError:
            pass
        with open(PENDING_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({**body, "at": time.time()}) + "\n")
        return True
    except OSError:
        return False


def take_spool(max_age):
    """Everything spooled in the last max_age seconds, oldest first, and the
    file emptied. Older entries are dropped unread."""
    import time

    taken = PENDING_PATH + f".{os.getpid()}.taking"
    try:
        os.replace(PENDING_PATH, taken)
    except OSError:
        return []
    out = []
    try:
        with open(taken, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    body = json.loads(line)
                except ValueError:
                    continue
                if isinstance(body, dict) and time.time() - (body.get("at") or 0) <= max_age:
                    out.append(body)
    except OSError:
        pass
    try:
        os.remove(taken)
    except OSError:
        pass
    return out


def set_voice(voice_id, state=None):
    """Switch voices, in the config and nowhere else.

    Both the CLI's 'set' and the panel's dropdown come through here, so the two
    cannot drift into doing different halves of the job. Sessions learn of it
    from their hooks (session_note), not from a rewritten CLAUDE.md.

    Returns (voice, announced); announced is kept for the API's reply.
    """
    state = state or load_state()
    voice, _ = resolve(voice_id, state.get("source"), state)
    # Remembered per engine as well as globally. The two engines have no voice
    # in common, so switching engine has to choose somebody -- and choosing
    # whoever you last had *there* is the only answer that does not throw away
    # a decision you already made.
    by_engine = dict(state.get("voiceByEngine") or {})
    by_engine[engine_of(state)] = voice["id"]
    patch_state(voice=voice["id"], voiceByEngine=by_engine)
    # Nothing else is written. Every open session hears about the new voice at
    # its next prompt (session_note's key carries the name), and a new one at
    # its start, so `announced` is always True now.
    return voice, True


def set_engine(name, state=None):
    """Switch engines, bringing a voice that engine actually has.

    Returns (engine, voice). The voice is the one speaking now, wherever the
    new engine has her too -- a switch changes how she is spoken, not who is
    speaking (Anton, 2026-09-25: "when I switch the engine the voice actor
    stays the same, not switch to Abby if im using Neya"). Only where it lacks
    her: whoever was speaking there last, then that engine's default. Never a
    voice the new engine does not have, which would leave the next answer
    silent with a LookupError nobody sees.
    """
    want = (name or "").strip().lower()
    if want not in ENGINES:
        raise LookupError(f"no engine called '{name}'. Try: " + ", ".join(ENGINES))
    state = state or load_state()

    if want == "pocket":
        import pocket_engine

        if not pocket_engine.available():
            raise LookupError(
                "pocket-tts is not installed. pip install pocket-tts")

    if want == "breeze" and not engine_ready("breeze", state):
        # Never fetched as a side effect. It is ten gigabytes and it needs a
        # particular kind of graphics card, so it is installed only by somebody
        # who has been told both and said yes -- the panel asks, and so does
        # this command.
        raise LookupError(
            "Breeze TTS 2 is not installed. It is a separate download of about "
            "eleven gigabytes and needs an NVIDIA card with 12 GB or more. Check "
            "this machine and install it with: python voice_cli.py install breeze")

    if want == "qwen" and not engine_ready("qwen", state):
        # Studio cannot load here (missing, or Smart App Control refuses its
        # DLLs) and Qwen on PyTorch is not installed yet. Switching anyway is
        # what used to happen: the engine then fell back to Pocket on its own,
        # and the choice looked as if it would not stick (2026-10-17).
        raise LookupError(
            "Qwen is not installed for this machine yet. It runs on an NVIDIA "
            "card through PyTorch, a download of about 7 GB that works with "
            "Smart App Control on. Install it with: python voice_cli.py install qwen")

    after = dict(state)
    after["engine"] = want
    current = state.get("voice")
    remembered = (state.get("voiceByEngine") or {}).get(want)
    rows = catalog(after, want)
    choices = [v["id"] for v in rows]
    if current in choices:
        voice_id = current
    elif remembered in choices:
        voice_id = remembered
    elif "abby" in choices:
        # Abby first on every engine that has her, Pocket included (it does
        # wherever it can clone): she is the face on the window and the voice
        # the whole thing is named after. Pocket's own default is a man's, and
        # the panel draws a man's Pocket voice with Max's face -- which is how a
        # switch to Pocket came up as "Max" (Anton, 2026-09-25: "make sure Abby
        # is opened by default on each model, not Max or someone else").
        voice_id = "abby"
    elif want == "pocket":
        # No cloning, so no Abby: a woman's voice of its own, which the panel
        # shows with Abby's face, and only then whatever Pocket itself prefers.
        import pocket_engine

        voice_id = next((v["id"] for v in rows if (v.get("sex") or "").lower().startswith("f")),
                        None) or pocket_engine.default_voice(engine_language(after))
    else:
        voice_id = choices[0] if choices else None

    patch_state(engine=want)
    if voice_id:
        voice, _ = set_voice(voice_id, load_state())
        return want, voice
    return want, None


def post(port, path, payload=None, timeout=5):
    import urllib.request

    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


def is_ours(health):
    """Whether a /health answer came from this engine rather than from some
    other program that happens to hold the port -- the same test the game
    makes (ClaudeVoiceClient.HealthAsync): our answer always carries "ready"."""
    return isinstance(health, dict) and "ready" in health


def server_alive(port, timeout=1.5):
    """Our engine's /health on `port`, or None -- None too when the port is
    held by a program that is not us (a dev server answering 404, say)."""
    try:
        health = post(port, "/health", timeout=timeout)
    except Exception:
        return None
    return health if is_ours(health) else None


def port_probe(port, timeout=2.0):
    """('free' | 'ours' | 'stranger' | 'silent', health) for a local port.

    'silent' is a port that takes the connection and then says nothing within
    the timeout -- which could be anybody, our own engine stuck in a load
    included, so the caller should not treat it as a stranger to be routed
    around without another clue (see speak_server.bind_port).
    """
    import http.client
    import socket
    import urllib.error

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            pass
    except OSError:
        return "free", None
    try:
        health = post(port, "/health", timeout=timeout)
    except (socket.timeout, TimeoutError):
        return "silent", None
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
            return "silent", None
        return "stranger", None              # HTTPError is a URLError: an answer, not ours
    except (http.client.HTTPException, ValueError, OSError):
        return "stranger", None              # not HTTP, not JSON, or hung up on us
    return ("ours", health) if is_ours(health) else ("stranger", None)


def port_owner(port):
    """'name.exe (pid N)' for whatever listens on a local TCP port, or ''.

    netstat and tasklist rather than a new dependency. Only ever asked when a
    port turns out to be taken, so the second it can cost does not matter, and
    the answer is the difference between "port in use" and "FaceLab's dev
    server has your port", which somebody can actually do something about.
    """
    import subprocess

    no_window = 0x08000000
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, timeout=5, creationflags=no_window).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    pid = None
    for line in out.splitlines():
        cols = line.split()
        if (len(cols) >= 5 and cols[0].upper() == "TCP" and cols[3].upper() == "LISTENING"
                and cols[1].rsplit(":", 1)[-1] == str(port)):
            pid = cols[4]
            break
    if not pid:
        return ""
    try:
        row = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=no_window).stdout.strip()
        name = row.split('","')[0].strip('"') if row.startswith('"') else ""
    except (OSError, subprocess.SubprocessError):
        name = ""
    return f"{name} (pid {pid})" if name else f"pid {pid}"


LOG_MAX_BYTES = 8 * 1024 * 1024


def _rolled(path, limit=LOG_MAX_BYTES):
    """Move a log aside once it gets fat, keeping exactly one previous.

    The engine writes about a kilobyte per spoken line -- timings, memory, the
    prefill graph -- which is worth having and never stops. Appending forever is
    fine for a week and silly for a year. Rolling at a start rather than mid-run
    keeps it simple: nothing is holding the handle yet, and 'read the log first'
    still finds the whole of the session you are actually debugging.
    """
    try:
        if os.path.getsize(path) < limit:
            return path
        os.replace(path, path + ".1")       # replace, so the older .1 goes quietly
    except OSError:
        pass
    return path


# A hook that finds no engine starts one -- once per SPAWN_GAP seconds, not
# once per hook: an engine that dies while starting up would otherwise get a
# fresh copy started by every prompt and every answer.
SPAWN_STAMP = os.path.join(LOG_DIR, "engine-spawn.stamp")
SPAWN_GAP = 30.0


def _claim_spawn():
    """True for the one caller allowed to start an engine now. The stamp is
    created O_EXCL, so two hooks at the same moment cannot both win."""
    import time

    try:
        if time.time() - os.path.getmtime(SPAWN_STAMP) < SPAWN_GAP:
            return False
        os.remove(SPAWN_STAMP)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        os.close(os.open(SPAWN_STAMP, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except OSError:
        return False


def start_server(state, wait=0, from_hook=False):
    """Launch the engine host detached. Returns True if it is (or came) up.

    from_hook: the caller has just failed to reach it, and Claude Code may be
    waiting on the caller -- so no probe before or after (each can cost 1.5 s
    against a hung port), and at most one start per SPAWN_GAP. Returns whether
    it started one."""
    import subprocess
    import time

    port = state["port"]
    if from_hook:
        if not _claim_spawn():
            return False
    elif server_alive(port):
        return True

    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(_rolled(os.path.join(LOG_DIR, "speak-server.log")), "a", encoding="utf-8")
    DETACHED = 0x00000008 | 0x08000000          # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen(
        [_python(), "-u", os.path.join(ROOT, "speak_server.py"), "--port", str(port)],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        creationflags=DETACHED, close_fds=True, cwd=_workdir())
    if from_hook:
        return True

    deadline = time.time() + wait
    while time.time() < deadline:
        # Read again each time: when another program holds the port, the
        # engine binds the next free one and writes that into the config.
        port = load_state().get("port") or port
        health = server_alive(port)
        if health and health.get("ready"):
            return True
        time.sleep(1.0)
    return bool(server_alive(load_state().get("port") or port))


def start_panel(state=None):
    """Open the panel window, detached from whatever opened it.

    Same shape as start_server, and for the same reason: whoever ran the
    command should get their prompt back, and the window should outlive it.
    Its stderr goes to a log because a Tk window that fails to appear leaves
    nothing on screen to read.
    """
    import subprocess

    state = state or load_state()
    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(os.path.join(LOG_DIR, "panel.log"), "a", encoding="utf-8")
    DETACHED = 0x00000008 | 0x08000000          # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen(
        [_python(), os.path.join(ROOT, "panel.py"), "--port", str(state["port"])],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        creationflags=DETACHED, close_fds=True, cwd=_workdir())
    return True


def _workdir():
    """Where a process of ours stands. Not inside an installed app folder: a
    process standing in a folder stops it being renamed, and an update swaps
    that folder for the new one (installer.swap_in)."""
    return DATA_DIR if INSTALL and os.path.isdir(DATA_DIR) else ROOT


# Opening the panel when you log in: one value under HKCU's Run key, named for
# this product. Not a shortcut in the Startup folder any more -- writing a .lnk
# took PowerShell, started hidden from a windowless process, which is exactly
# the pattern antivirus heuristics watch for -- and a registry value is one
# winreg call to write and one to take away, by the panel or the uninstaller.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu",
                           "Programs", "Startup", "Abby for Claude.lnk")


def login_command():
    return f'"{_python()}" "{os.path.join(ROOT, "panel.py")}"'


def opens_at_login():
    """Whether the panel opens at login: our Run value, or the Startup-folder
    shortcut versions before 2.0 wrote (still honoured until it is switched)."""
    reg = _winreg()
    if reg is not None:
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY) as k:
                if reg.QueryValueEx(k, PRODUCT)[0]:
                    return True
        except OSError:
            pass
    return os.path.isfile(STARTUP_LNK)


def _names_root(text):
    """Whether a command line (or a shortcut's bytes, decoded) names this copy."""
    text = (text or "").replace("/", "\\").replace('"', "").lower()
    return any(f.lower().rstrip("\\") + "\\" in text
               for f in {os.path.abspath(ROOT), os.path.realpath(ROOT)})


def _lnk_names_root(path):
    """A .lnk keeps its target as plain text inside it, UTF-16 or ANSI."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(65536)
    except OSError:
        return False
    return _names_root(raw.decode("utf-16-le", "ignore")) or _names_root(raw.decode("mbcs", "ignore"))


def open_at_login(on):
    """Switch it. Switched on, this copy's Run value replaces whatever was
    there and the old Startup-folder shortcut goes, so there is one place this
    is decided. Switched off, only what names this copy goes: a checkout's
    panel must not take away the installed copy's. Returns what it is now."""
    reg = _winreg()
    if on or _lnk_names_root(STARTUP_LNK):
        try:
            os.remove(STARTUP_LNK)
        except OSError:
            pass
    if reg is not None:
        try:
            if on:
                with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as k:
                    reg.SetValueEx(k, PRODUCT, 0, reg.REG_SZ, login_command())
            else:
                with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                 reg.KEY_SET_VALUE | reg.KEY_QUERY_VALUE) as k:
                    if _names_root(reg.QueryValueEx(k, PRODUCT)[0]):
                        reg.DeleteValue(k, PRODUCT)
        except OSError:
            pass
    return opens_at_login()


def _python():
    import sys
    exe = sys.executable or "python"
    # pythonw keeps the detached engine from flashing a console; it sits next to python.exe
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe
