"""
Claude Code hooks: tell a session what its voice can do, and say what only a
hook can hear.

Wired to five events, because they do different jobs:

  SessionStart
              fires when a session begins, is resumed, or has just been
              compacted. Tells it what the voice will do right now -- on or
              off, whose voice, and whether that engine takes a mood or makes a
              sound -- because CLAUDE.md is read once and the engine can change
              under a conversation. See voice_lib.session_note.
  UserPromptSubmit
              fires on every prompt. Tells the session again only if that has
              changed since it was last told, so an engine swapped mid-way
              reaches the conversation already under way, and an unchanged one
              costs nothing at all. It also starts the engine if the voice is
              on and nothing is answering, so the reply is heard.
  Notification
              fires when the session stops and waits for you -- a tool asking
              to be allowed, a question nobody has answered. There is nothing
              to read in the transcript for these, so the hook is the only way
              to hear them at all.
  Stop        fires when a turn ends.
  PreToolUse  fires mid-turn, before each tool call.

The last two speak what the session wrote -- the answer's TL;DR, and the line
of narration before a tool -- but only when the transcript watcher is switched
off. The watcher reads the same lines out of the same transcript, queues them
behind one another, knows which sessions are muted and which have nobody in
front of them, and it is the one that has actually been speaking them: until
the hook command was written with forward slashes these hooks failed on every
call, and nobody could tell. Two readers racing for the same line was only ever
going to make the outcome depend on which one won. So with the watcher on, Stop
does no more than bring a dead engine back, and PreToolUse does nothing.

Notifications dedupe on time rather than on words: the same words twice over is
the ordinary case there, and only a double-fire within a few seconds is worth
swallowing.

Always exits 0. A voice toy must never be able to break the session it
decorates -- and a UserPromptSubmit hook that exits 2 throws the prompt away.
"""


import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_lib

HOOK_LOG = os.path.join(voice_lib.LOG_DIR, "hook.log")
# One line per call, and PreToolUse is called before every tool a session
# uses, so this is rolled rather than left to grow.
HOOK_LOG_MAX = 1024 * 1024

# Which session was told what, so each is told once and then only again when
# it changed. Keyed by session id; the newest few dozen are kept.
TOLD_PATH = os.path.join(voice_lib.LOG_DIR, "told.json")
TOLD_KEEP = 64


def trace(msg):
    """One line per invocation. The hook is silent by design, which makes
    'nothing happened' impossible to tell apart from 'never ran' without this."""
    try:
        os.makedirs(voice_lib.LOG_DIR, exist_ok=True)
        with open(voice_lib._rolled(HOOK_LOG, HOOK_LOG_MAX), "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


# So that a message cut short at the ceiling says so in the same file that
# records whether the hook ran at all.
voice_lib.notify = trace


def last_assistant_text(transcript_path, state=None):
    """Newest thing the assistant actually said, as plain text.

    Entries holding only tool calls have no text block, so this walks back to
    the newest one that said something. A thinking block counts as having said
    something, because on some models it is the only place the narration goes
    -- see voice_lib.thinking_speech for which of them are worth hearing.

    Walking backwards is what makes that safe, and for free. Within one response
    the thinking always comes before the text, so if the response said both, the
    text is what this meets first and the reasoning behind it is never reached.
    A thinking block is only ever returned when its response said nothing else,
    which is exactly the rule the watcher goes to some trouble to enforce while
    reading the same file forwards.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""

    for line in reversed(lines[-400:]):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if content.strip():
                return content
            continue
        blocks = [b for b in (content or []) if isinstance(b, dict)]
        text = "\n".join(b.get("text", "") for b in blocks
                         if b.get("type") == "text")
        if text.strip():
            return text
        # Nothing said out loud in this response, so the thinking is the line
        # -- if it reads like one. Either way this stops here rather than
        # reaching further back: an older line has already had its turn, and
        # digging one up now would only be saying it twice.
        thought = "\n".join(b.get("thinking", "") for b in blocks
                            if b.get("type") == "thinking")
        if thought.strip():
            return voice_lib.thinking_speech(thought, state or {})
    return ""


def tool_phrase(name):
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


def pending_tool(transcript_path):
    """The tool the newest assistant turn is waiting to be allowed to run.

    A permission notification carries no tool name -- "Claude needs your
    permission" is the whole of the message -- but the tool_use block it is
    stopped on is already written to the transcript by the time the dialog
    appears. Without this the announcement can only say that something wants
    an answer, which is the difference between useful and merely startling.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""

    for line in reversed(lines[-200:]):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message") or {}
        names = [b.get("name") for b in (msg.get("content") or [])
                 if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")]
        if names:
            return tool_phrase(names[-1])
    return ""


def session_title(transcript_path):
    """What the panel should call this session: its title, as the watcher has it.

    A title the user set wins over the one Claude generated. Only the lines
    that could hold one are parsed, so a long transcript costs a scan and not
    a thousand json.loads.
    """
    custom = ai = None
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"custom-title"' not in line and '"ai-title"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                custom = entry.get("customTitle") or custom
                ai = entry.get("aiTitle") or ai
    except (OSError, TypeError):
        return None
    return custom or ai


def headless(state):
    """Nobody is sitting in front of this run: claude -p, an SDK caller, an
    errand one of your own programs sent off. The watcher reads the same thing
    off the transcript and stays quiet for it; a hook is told in its own
    environment, where Claude Code puts its entrypoint."""
    if state.get("watchHeadless", False):
        return False
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT") in voice_lib.HEADLESS_ENTRYPOINTS


def muted(state, payload):
    """Whether the panel has silenced this session. The watcher keeps the list
    in the config as transcript paths, which is also what a hook is given."""
    path = payload.get("transcript_path")
    if not path:
        return False
    want = os.path.normcase(os.path.normpath(path))
    return any(os.path.normcase(os.path.normpath(p)) == want
               for p in state.get("mutedSessions") or [] if isinstance(p, str))


def speak(state, speech, what, payload):
    """Hand one line to the engine, the way the watcher would have queued it.

    Behind whatever is playing rather than over it. A hook used to take the
    floor, which was right when all it ever spoke was one finished answer and
    is wrong for commentary: cutting the line before off mid-word only loses
    it. And under the same project name the watcher would give it, so the
    announcement when the speaker changes does not depend on who got there
    first.
    """
    mood, speech = voice_lib.direction(speech)
    if not speech:
        trace(f"  the {what} was only a direction")
        return
    body = {"text": speech, "voice": state.get("voice"),
            "source": state.get("source", "embedding"), "queue": True}
    cwd = payload.get("cwd")
    if cwd:
        body["project"] = os.path.basename(os.path.normpath(cwd))
    title = session_title(payload.get("transcript_path"))
    if title:
        body["session"] = title
    if mood:
        body["mood"] = mood
    try:
        voice_lib.post(state["port"], "/speak", body, timeout=5)
        trace(f"  spoke the {what} ({len(speech)} chars" + (f", {mood})" if mood else ")"))
    except Exception as exc:
        # Server down (or still loading). Bring it up for next time and stay quiet.
        trace(f"  engine unreachable ({exc}); autostart={state.get('autostart', True)}")
        if state.get("autostart", True):
            try:
                voice_lib.start_server(state, wait=0)
            except Exception:
                pass


def wake(state):
    """Start the engine if the voice is on and nothing is answering.

    The watcher lives inside the engine, so an engine that has died takes the
    reader of every transcript with it, and nothing notices until somebody asks
    why it went quiet. It remembers where it was in each file, so bringing it
    back also brings back whatever was said while it was gone.

    Only with the voice on. The panel's unload button turns it off first for
    exactly this reason: an engine unloaded to give the memory back must not be
    started again by the next thing somebody types.
    """
    if not state.get("enabled") or not state.get("autostart", True):
        return
    if voice_lib.server_alive(state["port"], timeout=1.0) is not None:
        return
    trace("  engine not answering; starting it")
    try:
        voice_lib.start_server(state, wait=0)
    except Exception as exc:
        trace(f"  could not start it: {exc}")


def _told():
    try:
        with open(TOLD_PATH, encoding="utf-8") as fh:
            told = json.load(fh)
    except (OSError, ValueError):
        return {}
    return told if isinstance(told, dict) else {}


def _remember(session, key):
    """Note what this session was told, newest last.

    Kept in the order they were told rather than sorted by the clock: two
    sessions told in the same tick of Windows' fifteen-millisecond clock would
    otherwise be a coin toss, and the one just told must never be the one let
    go. Written whole and moved into place, because a second session's hook
    can be reading it at the same moment.
    """
    told = _told()
    told.pop(session, None)
    told[session] = {"key": key, "at": time.time()}
    temp = TOLD_PATH + ".new"
    try:
        os.makedirs(voice_lib.LOG_DIR, exist_ok=True)
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(dict(list(told.items())[-TOLD_KEEP:]), fh)
        os.replace(temp, TOLD_PATH)
    except OSError:
        pass


def tell(event, payload, state):
    """Put what the voice can do right now in front of the session.

    SessionStart always tells -- a new session, a resumed one, and one just
    compacted, which may have lost what it was told along with everything
    else. UserPromptSubmit tells only what changed since this session last
    heard, and says nothing at all the rest of the time: the same paragraph
    on every prompt would be a tax on every prompt.

    Printed as additionalContext, which Claude Code adds to what the model
    reads and does not show as a message of its own.
    """
    if headless(state):
        trace("  headless run, telling it nothing")
        return
    if event == "UserPromptSubmit":
        wake(state)
    session = payload.get("session_id") or ""
    key, note = voice_lib.session_note(state)
    if event == "UserPromptSubmit" and session and \
            (_told().get(session) or {}).get("key") == key:
        trace("  nothing new to tell")
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                             "additionalContext": note}}))
    if session:
        _remember(session, key)
    trace(f"  told the session: {key}")


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    event = payload.get("hook_event_name") or "Stop"

    # Log before deciding anything. The whole point of this file is to tell
    # "Claude Code never called us" apart from "we ran and chose to stay quiet",
    # and a trace that sits below the early returns cannot do that.
    trace(f"fired: {event}")

    state = voice_lib.load_state()

    # Above the on-off switch, because "the voice is off" is itself worth
    # telling a session: it is the difference between writing for a listener
    # and writing for a screen, and it changes without a new session.
    if event in ("SessionStart", "UserPromptSubmit"):
        tell(event, payload, state)
        return

    if not state.get("enabled"):
        trace("  voice is off")
        return
    if headless(state):
        trace("  headless run, staying quiet")
        return
    if muted(state, payload):
        trace("  this session is muted")
        return

    # Deliberately above the narration switch rather than under it. Narration is
    # chatter about work in progress and turning it off is a taste; a session
    # stopped and waiting on you is not chatter. Its own switch is 'alerts'.
    if event == "Notification":
        trace(f"  {payload.get('notification_type')}: {(payload.get('message') or '')!r}")
        if not state.get("alerts", True):
            trace("  alerts are off")
            return
        speech = voice_lib.notification_speech(
            payload.get("message"),
            pending_tool(payload.get("transcript_path")),
            state.get("narrateMaxChars", 240))
        if not speech:
            trace("  notification had nothing speakable")
        elif not voice_lib.notification_due(speech):
            trace("  just said that, skipping")
        else:
            speak(state, speech, "notification", payload)
        return

    # Everything below is a line the session wrote, and the watcher reads the
    # same lines from the same transcript. When it is on, it speaks them.
    if state.get("watch", True):
        if event == "Stop":
            wake(state)
        return

    # A question halts the turn until it is answered, so somebody who has the
    # chatter off needs it more than ever, not less. It is also the only speech
    # here that must come from the tool's input -- the message carrying it has
    # no text block at all. See voice_lib.question_speech.
    if event == "PreToolUse" and payload.get("tool_name") == "AskUserQuestion":
        speech = voice_lib.question_speech(payload.get("tool_input"),
                                           state.get("maxChars", 4000))
        if not speech:
            trace("  question had nothing speakable")
        elif voice_lib.already_spoken(speech):
            trace("  question already spoken, skipping")
        else:
            speak(state, speech, "question", payload)
        return

    if event != "Stop" and not state.get("narrate", True):
        trace("  narration is off")
        return
    text = payload.get("text") or last_assistant_text(
        payload.get("transcript_path"), state)
    if not text.strip():
        return

    speech, what = voice_lib.speech_for(text, state)
    if event == "Stop" and what != "summary":
        # A finished answer with no TL;DR is short enough to hear in full.
        speech, what = voice_lib.clean_text(text, state.get("maxChars", 600)), "whole answer"

    if not speech:
        trace("  nothing speakable")
        return
    if voice_lib.already_spoken(speech):
        trace(f"  {what} already spoken, skipping")
        return

    speak(state, speech, what, payload)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
