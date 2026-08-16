"""
The switch and the dial: turn Claude's voice on or off, choose whose it is,
and make new ones.

    python voice_cli.py on|off|toggle       # the button
    python voice_cli.py status
    python voice_cli.py list [filter]       # what is available to sound like
    python voice_cli.py set <voice-id>      # accepts any unambiguous substring
    python voice_cli.py say <text>          # try a line without waiting for a turn
    python voice_cli.py stop                # shut up mid-sentence
    python voice_cli.py start | kill        # engine host, by hand
    python voice_cli.py replay [n]          # say the last answer again (or the nth back)
    python voice_cli.py replay-all [n]      # the whole message, not just its summary
    python voice_cli.py narrate on|off      # speak the short lines said mid-work
    python voice_cli.py source embedding|icl
    python voice_cli.py max <chars>
    python voice_cli.py clone <sample.wav> --name "Ada"   # add a voice of your own
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_lib


def _voice_label(state):
    try:
        v, _ = voice_lib.resolve(state["voice"], state["source"], state)
        where = v["culture"] if v["culture"] != "other" else v["sex"]
        return f"{v['name']} ({v['id']}, {where})"
    except LookupError as exc:
        return f"{state['voice']} -- unresolved: {exc}"


def cmd_status(state, _args):
    health = voice_lib.server_alive(state["port"])
    if health is None:
        engine = "not running"
    elif health.get("error"):
        engine = f"failed: {health['error']}"
    elif health.get("ready"):
        engine = "ready" + (", speaking" if health.get("speaking") else "")
    else:
        engine = "loading model..."

    print(f"  voice output : {'ON' if state['enabled'] else 'off'}")
    print(f"  voice        : {_voice_label(state)}")
    print(f"  source       : {state['source']}")
    print(f"  max chars    : {state['maxChars']}")
    print(f"  engine       : {engine}  (port {state['port']})")
    if health and health.get("watching"):
        how = "following sessions itself (no hooks needed)"
    elif state.get("watch", True):
        how = "will follow sessions once the engine starts"
    else:
        how = "off -- relying on Claude Code hooks instead"
    print(f"  watching     : {how}")
    print(f"  voices from  : {len(voice_lib.catalog(state))} in "
          f"{len(voice_lib.voice_roots(state))} folder(s)")


def cmd_on(state, _args):
    state["enabled"] = True
    voice_lib.save_state(state)
    print(f"Voice ON -- {_voice_label(state)}")
    if voice_lib.server_alive(state["port"]):
        print("Engine already warm.")
        return
    print("Starting the engine (first model load takes ~40-60s)...")
    ok = voice_lib.start_server(state, wait=120)
    print("Engine ready." if ok else
          "Engine still loading; the first answer or two may pass in silence.")


def cmd_off(state, _args):
    state["enabled"] = False
    voice_lib.save_state(state)
    try:
        voice_lib.post(state["port"], "/stop", timeout=2)
    except Exception:
        pass
    print("Voice off. (Engine stays warm -- 'kill' unloads it.)")


def cmd_toggle(state, args):
    (cmd_off if state["enabled"] else cmd_on)(state, args)


def cmd_list(state, args):
    needle = (args[0] if args else "").lower()
    rows = [v for v in voice_lib.catalog(state)
            if not needle or needle in v["id"].lower() or needle in v["culture"].lower()]
    if not rows:
        print(f"No voices matching '{needle}'.")
        return
    current = state["voice"].lower()
    bundled = voice_lib.voice_roots(state)[0]
    by_culture = {}
    for v in rows:
        by_culture.setdefault(v["culture"], []).append(v)
    for culture in sorted(by_culture):
        print(f"\n{culture}")
        for v in by_culture[culture]:
            mark = "*" if v["id"].lower() == current else " "
            kinds = "".join(("e" if v["embedding"] else "-", "i" if v["icl"] else "-"))
            where = "" if v["root"] == bundled else "  (local only)"
            print(f" {mark} {v['id']:<34} {v['sex']:<7} [{kinds}]{where}")
    print(f"\n{len(rows)} voice(s). '*' is current. [ei] = embedding / ICL present.")


def cmd_set(state, args):
    if not args:
        raise SystemExit("usage: voice_cli.py set <voice-id>")
    voice, _ = voice_lib.resolve(args[0], state["source"], state)
    state["voice"] = voice["id"]
    voice_lib.save_state(state)
    print(f"Voice set to {_voice_label(state)}")


def cmd_say(state, args):
    text = " ".join(args) or "The road is long, and the night is longer still."
    if not voice_lib.server_alive(state["port"]):
        print("Engine not running; starting it (this takes a moment)...")
        if not voice_lib.start_server(state, wait=120):
            raise SystemExit("engine did not come up -- see logs/speak-server.log")
    try:
        r = voice_lib.post(state["port"], "/speak", {
            "text": voice_lib.clean_text(text, state["maxChars"]),
            "voice": state["voice"], "source": state["source"]}, timeout=10)
        print(f"Speaking {r.get('queued')} chunk(s) as {r.get('voice')}.")
    except Exception as exc:
        raise SystemExit(f"could not speak: {exc}")


def _transcript_texts(project_dir):
    """Assistant messages from this project's newest session, oldest first.

    Claude Code names the transcript folder after the project path with every
    non-alphanumeric character turned into a dash.
    """
    import glob
    import re

    enc = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project_dir))
    d = os.path.expanduser(os.path.join("~", ".claude", "projects", enc))
    files = sorted(glob.glob(os.path.join(d, "*.jsonl")), key=os.path.getmtime)
    if not files:
        raise SystemExit(f"no session transcript found for {project_dir}")

    import json as _json
    out = []
    with open(files[-1], encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                entry = _json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str):
                text = content
            else:
                text = "\n".join(b.get("text", "") for b in (content or [])
                                 if isinstance(b, dict) and b.get("type") == "text")
            if text.strip():
                out.append(text)
    return out


def _replay(state, args, full):
    """Say something again. There is no play button to put next to a message --
    Claude Code has no API for that -- so this is how you re-hear one."""
    n = int(args[0]) if args and args[0].lstrip("-").isdigit() else 1
    texts = _transcript_texts(os.getcwd())

    # Count answers, not utterances. The newest thing said is often a one-line
    # "let me check that" written before a command, and replaying seven seconds
    # of that is never what "say it again" meant.
    answers = [t for t in texts
               if voice_lib.extract_summary(t) or len(voice_lib.clean_text(t, 0)) >= 200]
    texts = answers or texts

    if n < 1 or n > len(texts):
        raise SystemExit(f"only {len(texts)} answers in this session")

    text = texts[-n]
    if full:
        # Everything, and no length cap: asking for the whole message and then
        # cutting it off at the usual limit would answer a different question.
        speech = voice_lib.clean_text(text, 0)
    else:
        speech = (voice_lib.clean_text(voice_lib.extract_summary(text), state["maxChars"])
                  or voice_lib.clean_text(text, state["maxChars"]))
    if not speech:
        raise SystemExit("that answer has nothing speakable in it")

    if not voice_lib.server_alive(state["port"]):
        print("Engine not running; starting it...")
        if not voice_lib.start_server(state, wait=120):
            raise SystemExit("engine did not come up -- see logs/speak-server.log")
    r = voice_lib.post(state["port"], "/speak", {
        "text": speech, "voice": state["voice"], "source": state["source"]}, timeout=10)

    # Roughly 14 characters a second, measured over the clips this produces.
    mins, secs = divmod(int(len(speech) / 14), 60)
    length = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
    print(f"Replaying {'all of ' if full else ''}answer -{n} "
          f"({len(speech)} chars, about {length}, {r.get('queued')} chunks)")
    print(f"  {speech[:70]}...")
    if full:
        print("  'stop' cuts it off.")


def cmd_replay(state, args):
    _replay(state, args, full=False)


def cmd_replay_all(state, args):
    _replay(state, args, full=True)


def cmd_stop(state, _args):
    try:
        voice_lib.post(state["port"], "/stop", timeout=3)
        print("Stopped.")
    except Exception:
        print("Engine not running.")


def cmd_start(state, _args):
    if voice_lib.server_alive(state["port"]):
        print("Already running.")
        return
    print("Loading the talker model...")
    print("Ready." if voice_lib.start_server(state, wait=120) else
          "Did not come up in time -- check logs/speak-server.log")


def cmd_kill(state, _args):
    try:
        voice_lib.post(state["port"], "/quit", timeout=3)
        print("Engine unloaded.")
    except Exception:
        print("Engine not running.")


def cmd_source(state, args):
    if not args or args[0] not in ("embedding", "icl"):
        raise SystemExit("usage: voice_cli.py source embedding|icl")
    state["source"] = args[0]
    voice_lib.save_state(state)
    print(f"Source set to {args[0]}.")


def cmd_max(state, args):
    if not args or not args[0].isdigit():
        raise SystemExit("usage: voice_cli.py max <chars>")
    state["maxChars"] = int(args[0])
    voice_lib.save_state(state)
    print(f"Reading at most {state['maxChars']} characters per answer.")


def cmd_clone(state, args):
    """Turn a recording into a voice this can speak with.

    Use a clean 20-40s mono clip of ONE person talking, no music, no second
    speaker. Whose voice you are entitled to clone is your call -- see VOICES.md.
    """
    import argparse
    import json
    from datetime import datetime, timezone

    ap = argparse.ArgumentParser(prog="voice_cli.py clone")
    ap.add_argument("wav")
    ap.add_argument("--name", help="display name (default: from the id)")
    ap.add_argument("--id", help="folder name (default: from the file name)")
    ap.add_argument("--sex", default="female", choices=("female", "male", "other"))
    ap.add_argument("--culture", default="other",
                    help="grouping only; 'other' means it belongs to nobody")
    ap.add_argument("--text", default="",
                    help="what is actually said in the clip -- required for --icl-only")
    ap.add_argument("--icl-only", action="store_true",
                    help="add the ICL prompt to an existing voice (separate run: "
                         "loading the ICL encoder unloads the talker model)")
    a = ap.parse_args(args)

    wav = os.path.abspath(a.wav)
    if not os.path.exists(wav):
        raise SystemExit(f"no such file: {wav}")

    vid = (a.id or os.path.splitext(os.path.basename(wav))[0]).strip().lower()
    vid = "".join(c if c.isalnum() or c in "_-" else "_" for c in vid)
    name = a.name or vid.replace("_", " ").title()
    out_dir = os.path.join(voice_lib.voice_roots(state)[0], a.sex, a.culture, vid)

    # Two engines at once will fight over the GPU, so put the warm one away.
    if voice_lib.server_alive(state["port"]):
        print("Unloading the running engine first...")
        try:
            voice_lib.post(state["port"], "/quit", timeout=5)
        except Exception:
            pass

    from qwen_engine import Engine

    eng = Engine(state["studioDir"], verbose=True)
    try:
        if a.icl_only:
            if not a.text:
                raise SystemExit("--icl-only needs --text: the words spoken in the clip")
            eng.load_icl_encoder(state["modelDir"], state["talker"])
            eng.extract_icl_prompt(wav, a.text, os.path.join(out_dir, "icl-prompt.json"))
            print(f"ICL prompt written to {out_dir}")
            return
        eng.load_models(state["modelDir"], state["talker"])
        eng.extract_embedding(wav, os.path.join(out_dir, "embedding.json"))
    finally:
        eng.close()

    doc = {
        "Id": vid,
        "Name": name,
        "Backend": 0,
        "RemoteVoiceId": "",
        "Dimension": 2048,
        # Kept identical to whatever the ICL prompt is built with, so the
        # prompt's text and its token ids never disagree.
        "ReferenceText": a.text,
        "Gender": 1 if a.sex == "female" else 0,
        "Note": "",
        "Source": "Qwen-TTS Studio",
        "CreatedUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
    }
    with open(os.path.join(out_dir, "voice.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=4)
        fh.write("\n")

    print(f"\nVoice '{vid}' created in {out_dir}")
    print(f"Try it:  python voice_cli.py set {vid} && python voice_cli.py say \"hello\"")


def cmd_watch(state, args):
    if not args or args[0] not in ("on", "off"):
        raise SystemExit("usage: voice_cli.py watch on|off")
    state["watch"] = args[0] == "on"
    voice_lib.save_state(state)
    print(f"Transcript watching {'on' if state['watch'] else 'off'}. "
          "Restart the engine for it to take effect: kill, then start.")


def cmd_narrate(state, args):
    if not args or args[0] not in ("on", "off"):
        raise SystemExit("usage: voice_cli.py narrate on|off")
    state["narrate"] = args[0] == "on"
    voice_lib.save_state(state)
    print(f"Mid-work narration {'on' if state['narrate'] else 'off'}.")


COMMANDS = {
    "on": cmd_on, "off": cmd_off, "toggle": cmd_toggle, "status": cmd_status,
    "list": cmd_list, "set": cmd_set, "say": cmd_say, "stop": cmd_stop,
    "start": cmd_start, "kill": cmd_kill, "source": cmd_source, "max": cmd_max,
    "clone": cmd_clone,
    # Named several ways on purpose. This gets typed from memory while
    # listening, so the word that comes to mind should simply work rather than
    # send you to the help text.
    "replay": cmd_replay, "again": cmd_replay, "repeat": cmd_replay,
    "replay-all": cmd_replay_all, "replay_all": cmd_replay_all,
    "replayall": cmd_replay_all, "all": cmd_replay_all,
    "repeat-all": cmd_replay_all, "repeat_all": cmd_replay_all,
    "narrate": cmd_narrate, "watch": cmd_watch,
}


def main():
    argv = sys.argv[1:] or ["status"]
    cmd, args = argv[0].lower(), argv[1:]
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return
    if cmd not in COMMANDS:
        # A list of twenty words is not an answer to "I typed the wrong one".
        import difflib
        near = difflib.get_close_matches(cmd, COMMANDS, n=2, cutoff=0.5)
        hint = f" Did you mean: {' or '.join(near)}?" if near else \
               f" Try: {', '.join(sorted(set(COMMANDS)))}"
        raise SystemExit(f"unknown command '{cmd}'.{hint}")
    COMMANDS[cmd](voice_lib.load_state(), args)


if __name__ == "__main__":
    main()
