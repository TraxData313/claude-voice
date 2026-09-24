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

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
LOG_DIR = os.path.join(ROOT, "logs")


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
    "port": 8765,
    "autostart": True,
    # Speak the short lines said mid-work, not just the final answer. The Stop
    # hook only fires when a turn ends, so this rides PreToolUse instead.
    "narrate": True,
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
    # Follow the session transcripts directly instead of waiting to be called.
    # Hooks need the client to run them; this needs nothing but the files.
    "watch": True,
    # Codex writes a different local transcript format under ~/.codex. Opt in:
    # existing Claude-only installations must not suddenly hear another app.
    "watchCodex": False,
    # Speak the runs nobody is sitting in front of. A headless run -- `claude
    # -p`, an SDK call, an errand one of your own programs sends off -- writes
    # a transcript like every other session, and the watcher needs no hook to
    # find it. But its report is addressed to the program that asked for it,
    # not to the room, and hearing one read out in full is how this was found.
    # Off, so they pass in silence; on if you would rather hear yours come home.
    "watchHeadless": False,
    # Pause between one message and the next, so the seam is audible.
    "gapSeconds": 0.45,

    # Name the project when the project changes, and only then. 'off' says
    # nothing at all; anything else names it. Two conversations open on the
    # same project are still that project, and switching between them passes
    # without a word.
    "sessionLabel": "project",
    # After a restart the watcher resumes where it left off, but will not read
    # anything older than this -- catching up after a crash, not after a night.
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
    # Transcripts the panel has muted, as the watcher spells their paths.
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


def load_state():
    state = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as fh:
            state.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return state


def save_state(state):
    """Write down only what differs from the defaults.

    Saving the whole merged dict looks harmless and is not: every default gets
    frozen into the file the first time anything is saved, so improving a
    default later never reaches anyone who has already run the tool. That is
    exactly how 'sessionLabel' went on announcing the session title after the
    default had moved to the project name.
    """
    lean = {k: v for k, v in state.items() if k not in DEFAULTS or DEFAULTS[k] != v}
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(lean, fh, indent=2)
        fh.write("\n")


def patch_state(**changes):
    """Change a few keys without writing back a stale copy of the rest.

    Four processes now hold a config in memory -- the engine loaded one when it
    started, the panel polls, the CLI and the hook read one per run -- and
    saving any of those wholesale would quietly undo whatever the others
    changed since. So re-read, edit, write.
    """
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
DEFAULT_ENGINE = "qwen"
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
        "label": "Qwen — GPU 3.3 GB",
        "blurb": (
            "The original, and the better voice. Needs Qwen-TTS Studio and a "
            "graphics card with about 3.3 GB free. Reads Cyrillic, and every "
            "voice in the voices folder was cloned here."
        ),
        "url": "https://github.com/Danmoreng/qwen-tts-studio",
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


def engine_can(name, what):
    """What an engine can do: engine_can("breeze", "events"). Never raises --
    an unknown engine can do nothing, which is the safe answer."""
    return ENGINE_CAN.get(name, {}).get(what, () if what == "events" else False)


def engine_ready(name, state=None):
    """Could this engine start right now, without installing anything?

    Qwen answers yes and lets its own load say otherwise -- Studio has always
    been this tool's first assumption. The other two are optional: Pocket is a
    pip package and Breeze a separate download, and both are asked about in a
    dropdown's worth of time, so neither answer imports anything.
    """
    if name == "pocket":
        import pocket_engine

        return pocket_engine.available()
    if name == "breeze":
        import breeze_engine

        state = state if state is not None else load_state()
        return breeze_engine.available(state.get("breezePython"), state.get("breezeDir"),
                                       state.get("breezeModel"))
    return name in ENGINES


def engine_info(name):
    """Label, blurb and url for an engine. Never raises -- an unknown name gets
    a usable stub, because this is only ever used to describe something and a
    missing description should not stop a dropdown from being drawn."""
    return ENGINE_INFO.get(name, {"label": name, "blurb": "", "url": ""})


def engine_name(name):
    """What an engine is called in a sentence: its label without the memory."""
    return engine_info(name)["label"].split(" — ")[0]


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
WHERE_PATH = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                          "claude-voice", "where.json")


def write_where(state=None):
    """Leave the note saying where this copy is and which Python runs it."""
    import sys

    state = state or load_state()
    doc = {"root": ROOT, "python": sys.executable or "", "pythonw": _python(),
           "port": state.get("port", 8765), "version": own_version()}
    try:
        os.makedirs(os.path.dirname(WHERE_PATH), exist_ok=True)
        tmp = WHERE_PATH + ".new"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, WHERE_PATH)
    except OSError:
        pass            # a note nobody may read is not worth failing a start over
    return doc


def voice_roots(state=None):
    state = state or load_state()
    roots = [state.get("voicesDir") or "voices"]
    roots += list(state.get("extraVoicesDirs") or [])
    out = []
    for r in roots:
        r = r if os.path.isabs(r) else os.path.join(ROOT, r)
        if os.path.isdir(r) and r not in out:
            out.append(r)
    return out


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


def _loudest(samples, start, stop):
    """Peak absolute value in a block. The one inner loop of all of this."""
    loudest = 0.0
    for s in samples[start:stop]:
        a = -s if s < 0 else s
        if a > loudest:
            loudest = a
    return loudest


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
    at = 0
    while at + win <= len(samples) and _loudest(samples, at, at + win) <= floor:
        at += win
    return at


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

    best, best_at, peak = None, 0, 0.0
    for start in range(n - back, n - win + 1, win):
        loudest = _loudest(samples, start, start + win)
        if loudest > peak:
            peak = loudest
        if best is None or loudest < best:
            best, best_at = loudest, start

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
    lo, hi = best_at, best_at + win
    while lo - win >= 0 and _loudest(samples, lo - win, lo) <= level:
        lo -= win
    while hi + win <= n and _loudest(samples, hi, hi + win) <= level:
        hi += win

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
_MARK_OPEN = "<!-- current-voice -->"
_MARK_CLOSE = "<!-- /current-voice -->"
_NOTE_OPEN = "<!-- claude-voice -->"
_NOTE_CLOSE = "<!-- /claude-voice -->"


def _voice_block(voice):
    """The text between the current-voice markers. See announce_voice."""
    name = voice["name"]
    persona = (voice.get("persona") or "").rstrip(".")
    paras = [
        f"The voice is currently **{name}**" + (f" — {persona}." if persona else "."),
        f"Your lines are spoken out loud in that voice, so it is nice to play {name} a "
        f"little. Don't be caught out when the user calls you that — to them it is your "
        f"name, and answering to it costs nothing.",
        f"The words are heard rather than read, so write them to be easy on the ear."
        + (f" And they are heard as {name}, so let them sit in that spirit: take the manner "
           f"from the description above and follow it without overdoing it. A calm voice can "
           f"be calming. A bright one can be bright." if persona else ""),
    ]
    if persona:
        paras.append(
            f"Where it belongs is the part that is actually heard: the `## TL;DR` and the "
            f"short lines between tool calls. Not the body of an answer — a character "
            f"reading somebody a stack trace helps nobody. None of this is a rule to "
            f"follow; if the user would rather have plain Claude, that is the answer. It is "
            f"what is happening, so that you can judge it yourself.")
    return _MARK_OPEN + "\n" + "\n\n".join(textwrap.fill(p, 90) for p in paras) + "\n"


def _spliced(text, open_mark, close_mark, body):
    """text with everything between the markers replaced. None if no markers.

    The closing marker comes back off the original rather than out of `body`,
    so a caller cannot lose it by forgetting to include it.
    """
    start, end = text.find(open_mark), text.find(close_mark)
    if start == -1 or end == -1 or end < start:
        return None
    return text[:start] + body + text[end:]


def sync_notes(path=None, state=None):
    """Put the current speaking notes back into CLAUDE.md, voice block and all.

    Everything else a release installs -- the slash command, the hooks, the
    desktop shortcut -- needs the PowerShell installer run again. The note does
    not: it is one marker-to-marker splice, which is already what announce_voice
    does for the block inside it. So this is the one installed file that can put
    itself right, and it is the one that matters most, because it is the whole
    of what a session knows about being heard.

    Which is the point. A release that changes how Claude is told to write used
    to reach nobody until they read a line of output and went and ran setup.ps1
    by hand, and the note is precisely the file whose staleness is invisible --
    a session following last month's rules looks exactly like a session.

    Two things it deliberately does not do. It will not create the block, only
    refresh one that is already there: no markers, no edit, so deleting them is
    still the whole of turning the note off and this will not undo that. And it
    writes only when the result differs, so calling it on every engine start
    costs two small reads and nothing else.

    Note that this replaces the whole block, so hand edits inside the markers do
    not survive an update. Put your own wording outside them; that has always
    been the deal with install.ps1, and this only makes it happen sooner.
    """
    path = path or CLAUDE_MD
    try:
        with open(NOTES_PATH, encoding="utf-8") as fh:
            template = fh.read().rstrip()
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False

    start, end = text.find(_NOTE_OPEN), text.find(_NOTE_CLOSE)
    if start == -1 or end == -1 or end < start:
        return False
    # The template carries both of its own markers, so the tail resumes after
    # the closing one -- and it is rstripped, so what follows the block is left
    # exactly as it was. Anything else grows a blank line per call.
    fresh = text[:start] + template + text[end + len(_NOTE_CLOSE):]

    state = state or load_state()
    try:
        voice, _ = resolve(state["voice"], state.get("source"), state)
    except LookupError:
        voice = None                       # a voice that has since been deleted
    if voice:
        fresh = _spliced(fresh, _MARK_OPEN, _MARK_CLOSE, _voice_block(voice)) or fresh

    if fresh == text:
        return False
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(fresh)
    except OSError:
        return False
    return True


def announce_voice(voice, path=None):
    """Write the current voice into the note every session loads at startup.

    That file is read once when a session begins, so a session cannot discover
    a voice chosen later without being asked to go and look. Keeping this block
    up to date means it simply knows, with nothing to run.

    It says more than the name on purpose. A session told only "the voice is
    Abby" still writes as though handing finished text to a component further
    down the line; one told the answers are *said* in that voice, to somebody
    listening, has a reason to write differently -- which is the point of the
    notes above it. Given the situation instead of a rule, it can judge.

    Playing the part a little is the default: the voice is the face the user
    actually meets, and a session that knows whose face it is wearing does the
    job better than one performing to order. It is told to take the manner from
    the persona string rather than from anything written here, because the whole
    point is that the user can add voices nobody anticipated -- "a calm voice
    can be calming" adapts; a list of adjectives would not.

    What keeps it from going wrong is scope, not restraint, because personas
    describe a voice and not a manner. Max's reads "brave, driving, motivating",
    and a session that took that for a writing instruction would deliver a stack
    trace as a pep talk. Abby's is mild enough to hide the problem; his is not.
    Hence: colour what is heard, leave the body of an answer alone.

    No pronouns for the voice, deliberately -- this text is generated for every
    voice in the catalogue, including ones nobody here has met.

    Voices with no persona get the first two paragraphs only. "Let the character
    through" reads as an instruction to invent one when there is nothing above
    it to point at.

    Only the text between the markers is touched. No markers, no edit.
    """
    path = path or CLAUDE_MD
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    fresh = _spliced(text, _MARK_OPEN, _MARK_CLOSE, _voice_block(voice))
    if fresh is None:
        return False
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(fresh)
    except OSError:
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

    The key is the state the text describes plus a checksum of the text
    itself, so it moves whenever the words would: an engine swapped, or the
    wording changed by an update. The hook tells a session once and then only
    when the key has moved. The text is built from ENGINE_CAN rather than from
    engine names, so an engine added later is described by what it can do
    without anyone writing it a paragraph.

    It encourages rather than rations. Anton, hearing the first of these:
    "dont shy playing with the mood". A session told that most lines want no
    mood took it at its word and wrote almost none, which left the one engine
    that can laugh reading like the ones that cannot.
    """
    import zlib

    state = state if state is not None else load_state()
    engine = engine_of(state)
    on = bool(state.get("enabled"))
    try:
        who = resolve(state.get("voice"), state.get("source"), state)[0]["name"]
    except Exception:
        who = state.get("voice") or ""
    speaker = (f"speaking as {who} through {engine_name(engine)}" if who
               else f"speaking through {engine_name(engine)}")
    moods = engine_can(engine, "instruction")
    sounds = engine_can(engine, "events")

    if not on:
        text = (f"{SESSION_NOTE_HEAD} the voice is off, so nothing written is heard until "
                "it is switched on again. You will be told when it is.")
    elif not moods and not sounds:
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
                         "brackets as the first thing in the TL;DR, or at the front of a "
                         "short line between tool calls: "
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

    key = f"{'on' if on else 'off'}|{engine}|{who}|{zlib.crc32(text.encode('utf-8')):08x}"
    return key, text


# The events install.ps1 registers speak_hook.py for. Its docstring says what
# each one is for.
HOOK_EVENTS = ("Stop", "PreToolUse", "Notification", "SessionStart", "UserPromptSubmit")


def hook_health(paths=None, log_path=None):
    """How this tool's Claude Code hooks are wired, for 'status' to say.

    Returns {"events": [...], "files": [...], "backslashed": bool,
    "missing": [...], "last": "YYYY-MM-DD HH:MM:SS" or None}. Read off the
    settings files and the hook's own trace; nothing is run. `backslashed`
    is the one that matters: Claude Code runs a hook's command through bash on
    Windows, and bash takes the backslashes in C:\\Users\\... for escapes, so
    such a hook fails on every single call without a word to anybody.
    """
    paths = paths or [os.path.expanduser(os.path.join("~", ".claude", "settings.json")),
                      os.path.join(ROOT, ".claude", "settings.json")]
    found = {"events": [], "files": [], "backslashed": False, "missing": [], "last": None}
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


def set_voice(voice_id, state=None):
    """Switch voices: config, and the note every new session reads at startup.

    Both the CLI's 'set' and the panel's dropdown come through here, so the two
    cannot drift into doing different halves of the job.

    Returns (voice, announced) -- announced is False when CLAUDE.md has no
    markers to write between, which is not an error, just nothing to do.
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
    return voice, announce_voice(voice)


def set_engine(name, state=None):
    """Switch engines, bringing a voice that engine actually has.

    Returns (engine, voice). The voice is whoever was speaking there last, or
    that engine's default the first time -- never the old engine's, which does
    not exist on the new one and would leave the next answer silent with a
    LookupError nobody sees.
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

    after = dict(state)
    after["engine"] = want
    remembered = (state.get("voiceByEngine") or {}).get(want)
    choices = [v["id"] for v in catalog(after, want)]
    if remembered in choices:
        voice_id = remembered
    elif want == "pocket":
        import pocket_engine

        voice_id = pocket_engine.default_voice(engine_language(after))
    else:
        voice_id = "abby" if "abby" in choices else (choices[0] if choices else None)

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


def server_alive(port, timeout=1.5):
    try:
        return post(port, "/health", timeout=timeout)
    except Exception:
        return None


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


def start_server(state, wait=0):
    """Launch the engine host detached. Returns True if it is (or came) up."""
    import subprocess
    import time

    port = state["port"]
    if server_alive(port):
        return True

    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(_rolled(os.path.join(LOG_DIR, "speak-server.log")), "a", encoding="utf-8")
    DETACHED = 0x00000008 | 0x08000000          # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen(
        [_python(), "-u", os.path.join(ROOT, "speak_server.py"), "--port", str(port)],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        creationflags=DETACHED, close_fds=True, cwd=ROOT)

    deadline = time.time() + wait
    while time.time() < deadline:
        health = server_alive(port)
        if health and health.get("ready"):
            return True
        time.sleep(1.0)
    return bool(server_alive(port))


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
        creationflags=DETACHED, close_fds=True, cwd=ROOT)
    return True


def _python():
    import sys
    exe = sys.executable or "python"
    # pythonw keeps the detached engine from flashing a console; it sits next to python.exe
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe
