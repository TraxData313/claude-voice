"""
The switch and the dial: turn Claude's voice on or off, choose whose it is,
and make new ones.

    python voice_cli.py on|off|toggle       # the button
    python voice_cli.py on --panel          # ...and open the window with it
    python voice_cli.py panel               # what is playing, and the switches
    python voice_cli.py status
    python voice_cli.py list [filter]       # what is available to sound like
    python voice_cli.py set <voice-id>      # accepts any unambiguous substring
    python voice_cli.py say <text>          # try a line without waiting for a turn
    python voice_cli.py stop                # shut up mid-sentence
    python voice_cli.py start | kill        # engine host, by hand
    python voice_cli.py replay [n]          # say the last answer again (or the nth back)
    python voice_cli.py replay-all [n]      # the whole message, not just its summary
    python voice_cli.py narrate on|off      # speak the short lines said mid-work
    python voice_cli.py volume <0-100>     # how loud, applied mid-sentence
    python voice_cli.py source embedding|icl
    python voice_cli.py max <chars>
    python voice_cli.py version              # what this copy is, contacting nobody
    python voice_cli.py update [--apply]     # is there a newer one? (asks GitHub)
    python voice_cli.py update on|off        # allow a weekly look. Off by default
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
    print(f"  volume       : {_volume_percent(state)}%")
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
    # Reads the last look off disk; it never checks from here. Quote this
    # version when reporting a bug -- until now there was nothing to quote.
    import update_check
    print(f"  updates      : {update_check.status_line(state)}")
    lines = _persona_lines(state)
    if lines:
        print()
        for line in lines:
            print(line)


def cmd_on(state, args):
    state["enabled"] = True
    voice_lib.save_state(state)
    print(f"Voice ON -- {_voice_label(state)}")
    if args and args[0].lstrip("-") == "panel":
        cmd_panel(state, [])
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


def _persona_lines(state):
    """The manner that goes with the current voice, for whoever is speaking as it."""
    try:
        voice, _ = voice_lib.resolve(state["voice"], state["source"], state)
    except LookupError:
        return []
    if not voice.get("persona"):
        return []
    body = voice["persona"]
    wrapped, line = [], "   "
    for word in body.split():
        if len(line) + len(word) + 1 > 76:
            wrapped.append(line)
            line = "   "
        line += " " + word
    wrapped.append(line)
    return [f"  You are {voice['name']}. When the user calls you that, they mean you."] + wrapped


def cmd_set(state, args):
    if not args:
        raise SystemExit("usage: voice_cli.py set <voice-id>")
    # The panel's dropdown comes through the same function, so the two cannot
    # end up doing different halves of the job.
    voice, announced = voice_lib.set_voice(args[0], state)
    state["voice"] = voice["id"]
    print(f"Voice set to {_voice_label(state)}")
    if announced:
        print("  New sessions will know they are speaking as "
              f"{voice['name']} without being asked.")


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


def cmd_panel(state, _args):
    """Open the little window that shows what is being said, and steers it."""
    try:
        import tkinter                                    # noqa: F401
    except ImportError:
        raise SystemExit("this Python has no tkinter, so the panel cannot open")
    voice_lib.start_panel(state)
    print("Panel opened -- it floats on top. Closing it changes nothing else.")
    if not voice_lib.server_alive(state["port"]):
        print("  The engine is not running; the panel has a button to start it.")


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


def cmd_volume(state, args):
    """How loud, 0 to 100. With nothing to set, says where it is."""
    if not args:
        print(f"Volume {_volume_percent(state)}%.")
        return
    want = args[0].strip().rstrip("%")
    if not want.isdigit():
        raise SystemExit("usage: voice_cli.py volume <0-100>")
    level = max(0, min(100, int(want)))
    # The engine holds the actual slider, because the slider belongs to the
    # process making the noise. Tell it, and let it write the setting down.
    # With no engine there is nothing to be loud, and the number waits.
    try:
        voice_lib.post(state["port"], "/volume", {"level": level / 100.0}, timeout=5)
        print(f"Volume {level}%.")
    except Exception:
        voice_lib.patch_state(volume=level / 100.0)
        print(f"Volume {level}% -- saved. The engine is not running to be loud yet.")


def _volume_percent(state):
    try:
        return max(0, min(100, int(round(float(state.get("volume", 1.0)) * 100))))
    except (TypeError, ValueError):
        return 100


def cmd_max(state, args):
    if not args or not args[0].isdigit():
        raise SystemExit("usage: voice_cli.py max <chars>")
    state["maxChars"] = int(args[0])
    voice_lib.save_state(state)
    print(f"Reading at most {state['maxChars']} characters per answer.")


def cmd_clone(state, args):
    """Turn a recording into a voice this can speak with.

    Use a clean 20-40s mono clip of ONE person talking, no music, no second
    speaker. Whose voice you are entitled to clone is your call -- see docs/voices.md.
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


REPO_URL = "https://github.com/TraxData313/claude-voice"

# The one description of every command. `help` prints it and `help --markdown`
# emits docs/commands.md from it, so the documentation cannot drift from the tool.
HELP = [
    ("Turning it on and off", [
        ("on", "[--panel]", "Start speaking. Loads the engine if it is cold (40-60s the first time)."),
        ("off", "", "Stop speaking. The engine stays warm, so 'on' is instant."),
        ("toggle", "", "Whichever of the two you are not."),
        ("status", "", "Voice, engine, and whether it is following your sessions."),
        ("panel", "", "A small window on top: what is playing, the queue, and the switches."),
    ]),
    ("Choosing a voice", [
        ("list", "[filter]", "Every voice available. Filter by name or culture."),
        ("set", "<voice>", "Switch voice. Any unambiguous substring: 'set ab' finds Abby."),
        ("clone", "<file.wav> --name X", "Make a new voice from a 20-40s clip of one person."),
    ]),
    ("Hearing something again", [
        ("repeat", "[n]", "Say the last answer's summary again. 'repeat 3' for three answers back."),
        ("repeat-all", "[n]", "Say the whole of that answer, not just its summary."),
        ("say", "<text>", "Speak a line of your own, right now."),
        ("stop", "", "Cut off what is playing now. The voice stays on. ('break' works too.)"),
    ]),
    ("Tuning what you hear", [
        ("volume", "<0-100>", "How loud. It is this app's own slider in the Windows mixer."),
        ("max", "<chars>", "How much of a summary to read. Default 600."),
        ("narrate", "on|off", "Whether the short lines said mid-work are spoken."),
        ("watch", "on|off", "Follow sessions directly. Off means relying on hooks alone."),
        ("source", "embedding|icl", "Which clone to use. 'icl' is closer, and heavier."),
    ]),
    ("The engine itself", [
        ("start", "", "Load the model without turning the voice on."),
        ("kill", "", "Unload it and give the memory back."),
    ]),
    ("Keeping it current", [
        ("version", "", "What this copy is. Contacts nobody -- worth quoting in a bug report."),
        ("update", "[--apply]", "Is there a newer version? --apply pulls it and restarts the engine."),
        ("update", "on|off", "Allow one look a week. Off by default: nothing is contacted until you say so."),
    ]),
]

ALIASES = {"repeat": "replay, again", "repeat-all": "replay-all, all",
           "volume": "vol, loud"}


def cmd_help(state, args):
    if args and args[0].lstrip("-") == "markdown":
        return _help_markdown()
    print("\n  Claude's voice. Inside Claude Code these are the same thing:")
    print("      /voice status          python voice_cli.py status\n")
    for group, rows in HELP:
        print(f"  {group}")
        for name, arg, desc in rows:
            print(f"    {name + ' ' + arg:<28} {desc}")
        print()
    print("  Aliases: " + "; ".join(f"{k} = {v}" for k, v in ALIASES.items()))
    print("  Voices shipped: abby (female), max (male). Others come from your\n"
          "  training folder and are marked 'local only' in the list.")
    print(f"\n  The voice is one setting shared by every session: change it here and it\n"
          f"  changes everywhere. Full details, and how it all works:\n"
          f"      {REPO_URL}\n"
          f"      {os.path.join(voice_lib.ROOT, 'docs', 'commands.md')}\n")


def _help_markdown():
    """Write docs/commands.md. Writing the file here rather than printing it keeps
    the em dashes intact -- redirecting stdout on Windows encodes to the console
    codepage and quietly mangles anything outside it."""
    out = [
        "# Commands\n",
        "Everything below works two ways: `/voice status` inside Claude Code, or",
        "`python voice_cli.py status` in a terminal. Mind the space in the slash",
        "command — it is `/voice on`, never `/voice_on`.\n",
        "*Generated by `python voice_cli.py help --markdown`; edit the tool, not this file.*\n",
    ]
    for group, rows in HELP:
        out += [f"## {group}\n", "| command | does |", "|---|---|"]
        for name, arg, desc in rows:
            out.append(f"| `{(name + ' ' + arg).strip()}` | {desc} |")
        out.append("")
    out += [
        "## Also worth knowing\n",
        "- Aliases: " + "; ".join(f"`{k}` = `{v}`" for k, v in ALIASES.items()) + ".",
        "- The voice is **one setting shared by every session**. Change it anywhere and it",
        "  changes everywhere at once, including sessions already open, and it survives a",
        "  reboot.",
        "- The engine does **not** survive a reboot. After restarting the machine, say `on`",
        "  once and it stays warm until you shut down or `kill` it.",
        "- If it goes quiet unexpectedly, the engine has died — nothing revives it by",
        "  itself. `status` will say so, and `on` brings it back.",
        f"\nFull description of how it works: {REPO_URL}",
    ]
    path = os.path.join(voice_lib.ROOT, "docs", "commands.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"Wrote {path}")


def cmd_version(state, _args):
    """What this copy is. Nothing is contacted -- somebody typing 'version'
    is asking what they have, not asking to be looked up somewhere."""
    import update_check

    doc = update_check.local()
    print(f"claude-voice {update_check.shown_version()}"
          + (f"  ({doc['date']})" if doc.get("date") else ""))
    if doc.get("headline"):
        print(f"  {update_check.plain(doc['headline'])}")
    print(f"  {update_check.status_line(state)}")
    print(f"  {voice_lib.ROOT}")


def cmd_update(state, args):
    """Is there a newer claude-voice, and do you want it?

    This is the one command in the project that uses the network, and typing it
    is the whole of the consent for that. 'update on' additionally allows a look
    once a week without being asked; see update_check.py for what goes out.
    """
    import update_check

    flags = [a.lstrip("-").lower() for a in args]

    if flags and flags[0] in ("on", "off"):
        state["updateCheck"] = flags[0] == "on"
        voice_lib.save_state(state)
        if state["updateCheck"]:
            print("Update checks on -- one look a week, made while you run some other")
            print("voice command, so nothing ever waits on the network.")
            print(f"Every contact is written down in {update_check.CONTACT_LOG}")
        else:
            print("Update checks off. Nothing is contacted unless you type 'update'.")
        return

    print(f"You have {update_check.shown_version()}. Asking GitHub...")
    result = update_check.look_now()
    if result["error"]:
        print(update_check.plain(result.get("why") or result["error"]))
        print("Nothing else here wants the network, so this failing costs you nothing.")
        return

    remote = result["remote"] or {}
    if not update_check.is_newer(remote.get("version")):
        print(f"{update_check.shown_version()} is the newest there is.")
        return

    print(f"\n  {remote['version']} is out -- {update_check.plain(remote.get('headline', ''))}\n")
    for note in (remote.get("notes") or [])[:6]:
        print(f"    - {update_check.plain(str(note))}")

    if "apply" in flags or "now" in flags or "yes" in flags:
        print()
        update_check.apply_update(state)
        return
    print("\n  Take it :  python voice_cli.py update --apply")
    print(f"  Read it :  {remote.get('changelog') or update_check.CHANGELOG_URL}")


def cmd_narrate(state, args):
    if not args or args[0] not in ("on", "off"):
        raise SystemExit("usage: voice_cli.py narrate on|off")
    state["narrate"] = args[0] == "on"
    voice_lib.save_state(state)
    print(f"Mid-work narration {'on' if state['narrate'] else 'off'}.")


COMMANDS = {
    "on": cmd_on, "off": cmd_off, "toggle": cmd_toggle, "status": cmd_status,
    "panel": cmd_panel, "window": cmd_panel,
    "list": cmd_list, "set": cmd_set, "say": cmd_say,
    "stop": cmd_stop, "break": cmd_stop, "shush": cmd_stop,
    "start": cmd_start, "kill": cmd_kill, "source": cmd_source, "max": cmd_max,
    "volume": cmd_volume, "vol": cmd_volume, "loud": cmd_volume,
    "clone": cmd_clone,
    # Named several ways on purpose. This gets typed from memory while
    # listening, so the word that comes to mind should simply work rather than
    # send you to the help text.
    "replay": cmd_replay, "again": cmd_replay, "repeat": cmd_replay,
    "replay-all": cmd_replay_all, "replay_all": cmd_replay_all,
    "replayall": cmd_replay_all, "all": cmd_replay_all,
    "repeat-all": cmd_replay_all, "repeat_all": cmd_replay_all,
    "narrate": cmd_narrate, "watch": cmd_watch,
    "update": cmd_update, "upgrade": cmd_update, "version": cmd_version,
    "help": cmd_help, "commands": cmd_help,
}


def main():
    argv = sys.argv[1:] or ["status"]
    cmd, args = argv[0].lower(), argv[1:]
    if cmd in ("-h", "--help", "/?"):
        cmd = "help"
    if cmd not in COMMANDS:
        # A list of twenty words is not an answer to "I typed the wrong one".
        import difflib
        near = difflib.get_close_matches(cmd, COMMANDS, n=2, cutoff=0.5)
        hint = f" Did you mean: {' or '.join(near)}?" if near else \
               f" Try: {', '.join(sorted(set(COMMANDS)))}"
        raise SystemExit(f"unknown command '{cmd}'.{hint}")
    state = voice_lib.load_state()
    # The weekly look, if it has been allowed and is overdue. Detached, so it
    # cannot slow this command down or fail along with it -- and skipped for
    # 'update' itself, which is on its way to ask GitHub directly.
    if cmd not in ("update", "upgrade"):
        import update_check
        update_check.refresh_soon(state)
    COMMANDS[cmd](state, args)


if __name__ == "__main__":
    main()
