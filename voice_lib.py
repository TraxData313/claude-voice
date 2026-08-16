"""
Shared bits: config, the voice catalogue, turning a markdown answer into
something worth hearing, and talking to the speech server.

Imported by speak_server.py (the engine host), speak_hook.py (the Stop hook)
and voice_cli.py (the switch).
"""

import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
LOG_DIR = os.path.join(ROOT, "logs")

DEFAULTS = {
    # --- machine-specific, written by install.ps1 -------------------------
    "studioDir": r"C:\Program Files\qwen-tts-studio",
    "modelDir": os.path.expanduser(r"~\.qwen-tts-studio\models"),
    "talker": "qwen-talker-1.7b-base-Q8_0.gguf",   # d2048; the 0.6b model gives d1024
    # Voices shipped with this repo. Anything here is public.
    "voicesDir": "voices",
    # Extra folders searched as well -- for voices you may keep but not publish.
    # Same <sex>\<culture>\<id> layout. Never written to, never committed.
    "extraVoicesDirs": [],

    # --- runtime state, written by voice_cli ------------------------------
    "enabled": False,
    "voice": "abby",
    "source": "embedding",     # or 'icl': closer clone, larger files
    "maxChars": 600,
    "port": 8765,
    "autostart": True,
    # Speak the short lines said mid-work, not just the final answer. The Stop
    # hook only fires when a turn ends, so this rides PreToolUse instead.
    "narrate": True,
    # Only a label boundary now: below this a message reads as a passing remark,
    # above it as a spoken answer. Both are read in full.
    "narrateMaxChars": 240,
    # The ceiling on a message with no TL;DR. Roughly five minutes of speech.
    "fullMaxChars": 4000,
    # Follow the session transcripts directly instead of waiting to be called.
    # Hooks need the client to run them; this needs nothing but the files.
    "watch": True,
    # Pause between one message and the next, so the seam is audible.
    "gapSeconds": 0.45,
    # Say where a line came from when the speaker changes, and only then.
    # 'project' is the folder you are working in and is two words; 'title' is
    # the conversation's own name and is a sentence; 'off' says nothing.
    "sessionLabel": "project",
    # After a restart the watcher resumes where it left off, but will not read
    # anything older than this -- catching up after a crash, not after a night.
    "catchupSeconds": 300,

    # --- the panel --------------------------------------------------------
    # How many past utterances keep their audio, so replaying one is instant
    # and costs no synthesis at all.
    "historyKeep": 40,
    # Transcripts the panel has muted, as the watcher spells their paths.
    "mutedSessions": [],
    # Where the panel last sat, written when it closes.
    "panelGeometry": "",
    # Whether it floats over everything else. Its own tick box writes this.
    "panelTopmost": True,
    # Dark colours in the panel. Its own tick box writes this too.
    "panelDark": False,
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

def _is_voice_dir(d):
    return (os.path.exists(os.path.join(d, "embedding.json"))
            or os.path.exists(os.path.join(d, "icl-prompt.json")))


def _read_voice(d, vid, sex, culture, root):
    name, persona = vid, ""
    try:
        with open(os.path.join(d, "voice.json"), encoding="utf-8-sig") as fh:
            doc = json.load(fh)
        name = doc.get("Name") or vid
        # How this voice behaves, not just how it sounds. Being addressed by
        # name is natural once a voice has one, so the manner should match it.
        persona = doc.get("Persona") or ""
    except (OSError, ValueError):
        pass
    emb = os.path.join(d, "embedding.json")
    icl = os.path.join(d, "icl-prompt.json")
    return {
        "id": vid, "name": name, "sex": sex, "culture": culture,
        "persona": persona, "dir": d, "root": root,
        "embedding": emb if os.path.exists(emb) else None,
        "icl": icl if os.path.exists(icl) else None,
    }


def catalog(state=None):
    """Every voice under every configured root, as flat dicts.

    Two layouts are accepted, because a handful of personal voices and a whole
    generated library want different shapes:

        <root>\\<sex>\\<id>              flat -- what this repo ships
        <root>\\<sex>\\<culture>\\<id>   grouped -- for larger collections

    Earlier roots win, so a bundled voice shadows a same-named local one.
    """
    state = state or load_state()
    out, seen = [], set()
    for root in voice_roots(state):
        for sex in sorted(os.listdir(root)):
            sex_dir = os.path.join(root, sex)
            if not os.path.isdir(sex_dir):
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
    return out


def resolve(voice_id, source="embedding", state=None):
    """Find a voice by id (exact first, then substring). Returns (voice, kwargs)."""
    voices = catalog(state)
    if not voices:
        raise LookupError(
            "no voices found. Add one with: python voice_cli.py clone <sample.wav> --name <name>")

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

    path = hit.get("icl" if source == "icl" else "embedding") or hit["embedding"] or hit["icl"]
    if path is None:
        raise LookupError(f"voice '{hit['id']}' has no embedding or ICL prompt")
    key = "icl_prompt_path" if path.endswith("icl-prompt.json") else "embedding_path"
    return hit, {key: path}


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
    "—": ",", "–": ",", "―": ",",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": ".", " ": " ", "​": "",
    "×": " times ", "°": " degrees ", "±": " plus or minus ",
    "→": " to ", "←": " from ", "⇒": " gives ",
    "≤": " at most ", "≥": " at least ", "≠": " not equal to ",
    "•": " ", "·": " ", "✓": " ", "✗": " ",
}
_KEEP_PUNCT = set(" \n\t.,;:!?'\"()-/%$&+=@#")

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
    t = _normalize(md)
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
    """Cut at a sentence boundary rather than mid-word."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    m = list(re.finditer(r"[.!?](\s|$)", cut))
    if m and m[-1].end() > max_chars * 0.4:
        return cut[:m[-1].end()].strip()
    return cut.rsplit(" ", 1)[0].strip() + "..."


def chunks(text, first=140, target=320):
    """Sentence-ish pieces, so speech starts before the whole thing is synthesised.

    Every chunk is a separate generation, and two generations of the same voice
    are not identical -- the timbre drifts a little across a boundary, which is
    heard as the speaker changing between sentences. So boundaries are worth
    spending: only the *first* chunk is kept short, to get speech started
    quickly, and the rest are large enough that most answers are one more piece.
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


CLAUDE_MD = os.path.expanduser(os.path.join("~", ".claude", "CLAUDE.md"))
_MARK_OPEN = "<!-- current-voice -->"
_MARK_CLOSE = "<!-- /current-voice -->"


def announce_voice(voice, path=None):
    """Write the current voice into the note every session loads at startup.

    That file is read once when a session begins, so a session cannot discover
    a voice chosen later without being asked to go and look. Keeping one marked
    line up to date means it simply knows, with nothing to run.

    Only the text between the markers is touched. No markers, no edit.
    """
    path = path or CLAUDE_MD
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    start, end = text.find(_MARK_OPEN), text.find(_MARK_CLOSE)
    if start == -1 or end == -1 or end < start:
        return False

    persona = (voice.get("persona") or "").rstrip(".")
    line = (f"{_MARK_OPEN}\nThe voice is currently **{voice['name']}**"
            + (f" — {persona}." if persona else ".")
            + f" That is you: if the\nuser calls you {voice['name']}, they mean you, "
              "so answer to it.\n")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text[:start] + line + text[end:])
    except OSError:
        return False
    return True


def set_voice(voice_id, state=None):
    """Switch voices: config, and the note every new session reads at startup.

    Both the CLI's 'set' and the panel's dropdown come through here, so the two
    cannot drift into doing different halves of the job.

    Returns (voice, announced) -- announced is False when CLAUDE.md has no
    markers to write between, which is not an error, just nothing to do.
    """
    state = state or load_state()
    voice, _ = resolve(voice_id, state.get("source"), state)
    patch_state(voice=voice["id"])
    return voice, announce_voice(voice)


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


def start_server(state, wait=0):
    """Launch the engine host detached. Returns True if it is (or came) up."""
    import subprocess
    import time

    port = state["port"]
    if server_alive(port):
        return True

    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(os.path.join(LOG_DIR, "speak-server.log"), "a", encoding="utf-8")
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
