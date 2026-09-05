"""
Claude Code hooks: say the answer out loud.

Wired to three events, because they catch different things:

  Stop        fires when a turn ends. Speaks the answer's TL;DR -- or the whole
              answer when it has none, which is right, since an answer short
              enough to skip the summary is short enough to hear in full.
  PreToolUse  fires mid-turn, before each tool call. Speaks the line of
              narration that precedes it ("Let me check whether that exists"),
              which the Stop hook never sees because the turn has not ended.
              That line is not always a text block: some models write it into
              the thinking block instead, and it is read from there too.
  Notification
              fires when the session stops and waits for you -- a tool asking
              to be allowed, a question nobody has answered. There is nothing
              to read in the transcript for these, so the hook is the only way
              to hear them at all.

The first two dedupe against recently spoken text, so a line said during the
work is not said again in the summary, and a narration block is not repeated
across the several tool calls that follow it. Notifications dedupe on time
instead: the same words twice over is the ordinary case there, and only a
double-fire within a few seconds is worth swallowing.

Always exits 0. A voice toy must never be able to break the session it decorates.
"""


import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_lib

def trace(msg):
    """One line per invocation. The hook is silent by design, which makes
    'nothing happened' impossible to tell apart from 'never ran' without this."""
    try:
        os.makedirs(voice_lib.LOG_DIR, exist_ok=True)
        with open(os.path.join(voice_lib.LOG_DIR, "hook.log"), "a", encoding="utf-8") as fh:
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


def speak(state, speech, what):
    try:
        voice_lib.post(state["port"], "/speak", {
            "text": speech,
            "voice": state.get("voice"),
            "source": state.get("source", "embedding"),
        }, timeout=5)
        trace(f"  spoke the {what} ({len(speech)} chars)")
    except Exception as exc:
        # Server down (or still loading). Bring it up for next time and stay quiet.
        trace(f"  engine unreachable ({exc}); autostart={state.get('autostart', True)}")
        if state.get("autostart", True):
            try:
                voice_lib.start_server(state, wait=0)
            except Exception:
                pass


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}

    event = payload.get("hook_event_name") or "Stop"

    # Log before deciding anything. The whole point of this file is to tell
    # "Claude Code never called us" apart from "we ran and chose to stay quiet",
    # and a trace that sits below the early returns cannot do that.
    trace(f"fired: {event}")

    state = voice_lib.load_state()
    if not state.get("enabled"):
        trace("  voice is off")
        return

    # Deliberately above the narration switch rather than under it. Narration is
    # chatter about work in progress and turning it off is a taste; a question
    # halts the turn until it is answered, so somebody who wants the chatter off
    # needs this one more than ever, not less. It is also the only speech here
    # that must come from the tool's input -- the message carrying it has no
    # text block at all. See voice_lib.question_speech.
    if event == "PreToolUse" and payload.get("tool_name") == "AskUserQuestion":
        speech = voice_lib.question_speech(payload.get("tool_input"),
                                           state.get("maxChars", 4000))
        if not speech:
            trace("  question had nothing speakable")
        elif voice_lib.already_spoken(speech):
            trace("  question already spoken, skipping")
        else:
            speak(state, speech, "question")
        return

    # Beside the question above, and for the same reason: a notification is
    # raised precisely when the session has stopped and is waiting, so it does
    # not answer to the narration switch. Its own switch is 'alerts'.
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
            speak(state, speech, "notification")
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
    # Shared with the transcript watcher: whichever sees a message first, it is
    # only ever said once.
    if voice_lib.already_spoken(speech):
        trace(f"  {what} already spoken, skipping")
        return

    speak(state, speech, what)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
