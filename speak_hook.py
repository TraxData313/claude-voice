"""
Claude Code hooks: say the answer out loud.

Wired to two events, because they catch different things:

  Stop        fires when a turn ends. Speaks the answer's TL;DR -- or the whole
              answer when it has none, which is right, since an answer short
              enough to skip the summary is short enough to hear in full.
  PreToolUse  fires mid-turn, before each tool call. Speaks the line of
              narration that precedes it ("Let me check whether that exists"),
              which the Stop hook never sees because the turn has not ended.

Both dedupe against recently spoken text, so a line said during the work is not
said again in the summary, and a narration block is not repeated across the
several tool calls that follow it.

Always exits 0. A voice toy must never be able to break the session it decorates.
"""


import json
import os
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


def last_assistant_text(transcript_path):
    """Newest assistant turn in the JSONL transcript, as plain text.

    Entries holding only tool calls have no text block, so this walks back to
    the newest one that actually said something.
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
            text = content
        else:
            text = "\n".join(
                b.get("text", "") for b in (content or [])
                if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            return text
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
    if event != "Stop" and not state.get("narrate", True):
        trace("  narration is off")
        return
    text = payload.get("text") or last_assistant_text(payload.get("transcript_path"))
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
