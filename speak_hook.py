"""
Claude Code's hooks, and Codex's notify: the only door from a coding session
into the voice.

Each call hands what the client gave it to the engine over HTTP (POST /hook)
and goes. The engine decides what is said: the TL;DR or the whole answer,
muted sessions, headless runs, the dedupe, the mood. Nothing here, or in the
engine, watches or tails Claude Code's files. Until 2.0 the engine read every
session transcript under ~/.claude/projects every 0.7 s and rewrote a block of
the user's own ~/.claude/CLAUDE.md on every start and voice change; Anton
asked for that to stop (2026-10-17), and this is what replaced it.

The events (voice_lib.HOOK_EVENTS; voice_lib.hook_entries writes them):

  SessionStart
              a session begins, resumes, or has just been compacted. Prints the
              whole of what it is told about being heard -- the writing rules,
              the voice and its persona, what the engine can do -- as
              additionalContext. See voice_lib.session_note.
  UserPromptSubmit
              every prompt. Tells the session again only if that has changed
              since it was last told. Also tells the engine the session exists
              (its first prompt becomes its name in the panel), which starts a
              dead engine if the voice is on, so the reply is heard.
  Stop        a turn ended. Claude Code hands over the final message as
              last_assistant_message; the engine speaks its TL;DR.
  Notification
              the session stops and waits on you: a permission prompt, a
              question nobody answered.
  PreToolUse  AskUserQuestion only, by default: a question halts the turn, and
              its words are in the tool's input.

What is no longer heard: the short lines written between tool calls. No hook
carries them -- PreToolUse is given the tool and its input, nothing the
assistant said -- so the only way to hear them is to read the end of the
transcript Claude Code names in that hook call. That is behind a setting,
narrateFromTranscript, off by default; with it on, PreToolUse is registered for
every tool and reads the last ~256 KB of that one file at that moment.

The three that speak are registered "async": Claude Code does not wait for
them, so neither a slow engine nor an absent one can hold up a turn. The two
that tell must print, so they run in line, on a short timeout. An engine that is
down is started (if the voice is on), and an answer that found it down is left
in the spool (voice_lib.spool) to be said once it is up.

Codex runs no Claude hooks, but its `notify` setting runs a program at the end
of every turn with a JSON argument carrying "last-assistant-message". Pointed
at this file it is treated exactly like a Stop. See docs/codex.md.

Always exits 0. A voice toy must never be able to break the session it
decorates -- and a UserPromptSubmit hook that exits 2 throws the prompt away.
"""


import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_lib

HOOK_LOG = os.path.join(voice_lib.LOG_DIR, "hook.log")
# One line per call, rolled rather than left to grow.
HOOK_LOG_MAX = 1024 * 1024

# Which session was told what, so each is told once and then only again when
# it changed. Keyed by session id; the newest few dozen are kept.
TOLD_PATH = os.path.join(voice_lib.LOG_DIR, "told.json")
TOLD_KEEP = 64

# How long a POST to the engine may take. The engine answers /hook before it
# synthesises anything, in milliseconds; this is only what a hang could cost.
# A refused connection on Windows still takes about two seconds (the stack
# retries the SYN), which is why the speaking hooks run async.
POST_TIMEOUT = 3.0
# The same for the one hook Claude Code waits on (see send).
SYNC_POST_TIMEOUT = 1.0

# How much of a transcript's end is read when a line has to come from it.
TAIL_BYTES = 256 * 1024

# A prompt is kept this long as the session's name.
PROMPT_KEEP = 200


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
    """Newest thing the assistant said, read off the end of its transcript.

    Used only where the hook payload does not carry it: a Stop from a Claude
    Code too old to send last_assistant_message, and the lines between tool
    calls when narrateFromTranscript is on. Reads the last TAIL_BYTES of the
    one file Claude Code named in this call, once.

    Entries holding only tool calls have no text block, so this walks back to
    the newest one that said something. A thinking block counts, because on
    some models it is the only place the narration goes -- but only when its
    response said nothing else: walking backwards meets the text first.
    """
    if not transcript_path:
        return ""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            raw = fh.read()
    except OSError:
        return ""
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]                     # the first is cut somewhere in the middle

    for line in reversed(lines):
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
        text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if text.strip():
            return text
        thought = "\n".join(b.get("thinking", "") for b in blocks
                            if b.get("type") == "thinking")
        if thought.strip():
            return voice_lib.thinking_speech(thought, state or {})
    return ""


def headless(state):
    """Nobody is sitting in front of this run: claude -p, an SDK caller. Claude
    Code puts its entrypoint in the hook's environment."""
    if state.get("watchHeadless", False):
        return False
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT") in voice_lib.HEADLESS_ENTRYPOINTS


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
    otherwise be a coin toss. Written whole and moved into place, because a
    second session's hook can be reading it at the same moment.
    """
    told = _told()
    told.pop(session, None)
    told[session] = {"key": key, "at": time.time()}
    temp = f"{TOLD_PATH}.{os.getpid()}.new"
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
    heard, and says nothing at all the rest of the time.
    """
    if headless(state):
        trace("  headless run, telling it nothing")
        return
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


def body_for(event, payload, state):
    """What the engine is handed for this event, or None for nothing to send.

    Only the fields it uses, and the entrypoint read here because only the
    hook can see its own environment.
    """
    body = {"event": event, "client": "claude",
            "session_id": payload.get("session_id"),
            "transcript_path": payload.get("transcript_path"),
            "cwd": payload.get("cwd"),
            "entrypoint": os.environ.get("CLAUDE_CODE_ENTRYPOINT")}
    if event == "UserPromptSubmit":
        prompt = payload.get("prompt")
        if isinstance(prompt, str):
            body["prompt"] = prompt[:PROMPT_KEEP]
    elif event == "Stop":
        text = payload.get("last_assistant_message")
        if not isinstance(text, str) or not text.strip():
            # Older Claude Code: the final message is only in the transcript.
            text = last_assistant_text(payload.get("transcript_path"), state)
        if not text.strip():
            trace("  nothing in the final message")
            return None
        body["text"] = text
    elif event == "Notification":
        body["message"] = payload.get("message")
        body["notification_type"] = payload.get("notification_type")
    elif event == "PreToolUse":
        body["tool_name"] = payload.get("tool_name")
        if payload.get("tool_name") == "AskUserQuestion":
            body["tool_input"] = payload.get("tool_input")
        elif voice_lib._lines_heard(state):
            text = last_assistant_text(payload.get("transcript_path"), state)
            if not text.strip():
                return None
            body["text"] = text
        else:
            return None
    return body


def codex_body(arg):
    """A Codex notify argument as a /hook body, or None if it is not one.

    Codex calls its notify program with one JSON argument; at the end of a turn
    it says "agent-turn-complete" and carries the final message. A Codex task
    has no transcript path the panel could name it by, so its thread id stands
    in for one.
    """
    try:
        note = json.loads(arg)
    except ValueError:
        return None
    if not isinstance(note, dict) or note.get("type") != "agent-turn-complete":
        return None
    text = note.get("last-assistant-message")
    if not isinstance(text, str) or not text.strip():
        return None
    thread = note.get("thread-id") or ""
    asked = note.get("input-messages") or []
    body = {"event": "Stop", "client": "codex", "session_id": thread,
            "transcript_path": f"codex:{thread}" if thread else None,
            "cwd": note.get("cwd"), "text": text}
    if asked and isinstance(asked[0], str):
        body["prompt"] = asked[0][:PROMPT_KEEP]
    return body


def pass_on(argv):
    """Run the notify program this one stands in front of, if there is one.

    Codex has a single `notify` slot, and the Codex app may already use it (on
    Anton's machine its computer-use helper does). So the setting can read
    ["python", "speak_hook.py", "--then", <the old command...>]: the old
    command is started, detached, with the same JSON argument it always got,
    and is not waited for.
    """
    if "--then" not in argv[:-1]:
        return
    rest = argv[argv.index("--then") + 1:]
    if len(rest) < 2:
        return
    import subprocess

    try:
        subprocess.Popen(rest, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=0x00000008 | 0x08000000)   # detached, no window
        trace(f"  passed on to {os.path.basename(rest[0])}")
    except OSError as exc:
        trace(f"  could not pass on to {rest[0]}: {exc}")


def send(state, body):
    """POST one body to the engine; bring it up if it is down.

    An answer that finds it down is spooled and said once it is up -- the one
    thing the old transcript watcher did that would otherwise be lost. Only
    while the voice is on and autostart allows it: an engine unloaded from the
    panel to give the memory back must not be started by the next prompt.
    """
    # UserPromptSubmit runs in line -- Claude Code waits for it, on a 5 s
    # timeout -- so an engine that is down or hung costs it a second at most
    # here, not three, before start_server's own check spends more.
    timeout = SYNC_POST_TIMEOUT if body.get("event") == "UserPromptSubmit" else POST_TIMEOUT
    try:
        reply = voice_lib.post(state["port"], "/hook", body, timeout=timeout)
        trace(f"  engine: {reply.get('said') or reply.get('why') or 'noted'}")
        return True
    except Exception as exc:
        trace(f"  engine unreachable ({exc.__class__.__name__})")
        # Refused = nobody listening. A timeout may be a busy engine that did
        # take the body and will say it: spooling that too says it twice.
        refused = isinstance(exc, ConnectionRefusedError) or             isinstance(getattr(exc, "reason", None), ConnectionRefusedError)
    if not state.get("enabled") or not state.get("autostart", True):
        return False
    if refused and body.get("event") != "UserPromptSubmit" and voice_lib.spool(body):
        trace("  kept it for when the engine is up")
    try:
        if voice_lib.start_server(state, from_hook=True):
            trace("  starting the engine")
        else:
            trace("  an engine was started moments ago; not another")
    except Exception as exc:
        trace(f"  could not start it: {exc}")
    return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    state = voice_lib.load_state()

    # Codex's notify: a JSON argument, nothing on stdin.
    if argv:
        trace("fired: codex notify")
        pass_on(argv)
        body = codex_body(argv[-1])
        if body and state.get("enabled"):
            send(state, body)
        return

    try:
        # A byte-order mark is tolerated: a payload piped in by hand from
        # PowerShell carries one, and json refuses it.
        payload = json.loads(sys.stdin.read().lstrip("﻿") or "{}")
    except (ValueError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = payload.get("hook_event_name") or "Stop"

    # Log before deciding anything, so "Claude Code never called us" can be
    # told apart from "we ran and chose to stay quiet".
    trace(f"fired: {event}")

    # Above the on-off switch, because "the voice is off" is itself worth
    # telling a session.
    if event in ("SessionStart", "UserPromptSubmit"):
        tell(event, payload, state)

    if not state.get("enabled"):
        trace("  voice is off")
        return
    if headless(state):
        # claude -p, an SDK caller: nobody to hear it, so nothing is sent,
        # spooled, or started for it.
        trace("  headless run, nothing to say")
        return
    if event == "SessionStart":
        # Registered with the engine at the first prompt, which also names it;
        # a session that is opened and never used is not worth a row.
        return
    body = body_for(event, payload, state)
    if body is not None:
        send(state, body)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
