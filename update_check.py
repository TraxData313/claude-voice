"""
Is there a newer claude-voice than the one you have?

This is the only file in the project that touches the network, and it is the
only reason the project ever does. Nothing here runs on its own:

    python update_check.py              # look now, and say what was found
    python update_check.py --refresh    # look only if a look is due, say nothing
    python voice_cli.py update          # the same, with the sentences around it

The look is off until it is turned on. `updateCheck` in config.json is false by
default and nothing sets it but the user -- either `voice_cli.py update on` or
the installer's -UpdateChecks. Typing `update` by hand always works whatever the
setting says, because typing it is the asking.

What goes out is one GET of a public file, with no query string, no identifier,
no version, no operating system and no counter -- the request cannot tell one
machine from another, and nothing comes back but the file. Every contact is
written to logs\\update.log with the time and the outcome, so "it stays quiet
unless you ask" is a claim you can audit rather than one you have to believe.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_lib

REPO = "TraxData313/claude-voice"
BRANCH = "main"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/version.json"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
CHANGELOG_URL = f"https://github.com/{REPO}/blob/{BRANCH}/CHANGELOG.md"

# Deliberately carries no version and no platform. Sending "claude-voice/1.0.0
# (Windows 11)" would be ordinary and would also be the one line in the project
# that reports something about the machine to somebody else. There is nothing
# this needs to know about you in order to hand you a file.
USER_AGENT = "claude-voice (update check)"

VERSION_PATH = os.path.join(voice_lib.ROOT, "version.json")
CHANGELOG_PATH = os.path.join(voice_lib.ROOT, "CHANGELOG.md")
CACHE_PATH = os.path.join(voice_lib.LOG_DIR, "update-check.json")
CONTACT_LOG = os.path.join(voice_lib.LOG_DIR, "update.log")

# Files a `git pull` brings in but does not install. The slash command and the
# hooks were copied out of here into .claude at setup time, so a release that
# changes any of them needs setup.ps1 run again before the change reaches
# anything.
#
# speaking-notes.md used to be on this list and no longer is: voice_lib.sync_notes
# puts it back into ~\.claude\CLAUDE.md after a pull. It was the worst one to
# leave to a hand-run installer, because a session quietly following last
# month's rules looks exactly like a session.
INSTALLED_FROM = ("commands/", "install.ps1", "setup.ps1", "make_shortcut.ps1")


# --------------------------------------------------------------------------
# what version this is
# --------------------------------------------------------------------------

def local():
    """This copy's own version.json, or a stand-in old enough to lose to anything."""
    try:
        with open(VERSION_PATH, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {"version": "", "headline": ""}
    return doc


def local_version():
    return (local().get("version") or "").strip()


def shown_version():
    """For putting on screen: an unnumbered copy says so rather than lying."""
    return local_version() or "unknown"


def _parts(version):
    """1.2.10 -> (1, 2, 10, 0). Anything unparsable sorts as oldest."""
    out = []
    for piece in str(version or "").split(".")[:4]:
        digits = "".join(c for c in piece if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (4 - len(out)))


def is_newer(remote_version, local_version_=None):
    local_version_ = local_version() if local_version_ is None else local_version_
    if not remote_version:
        return False
    return _parts(remote_version) > _parts(local_version_)


# --------------------------------------------------------------------------
# the look itself
# --------------------------------------------------------------------------

def _note_contact(outcome):
    """One line per contact, whatever came of it. This file existing and being
    short is the evidence that nothing checks behind your back."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(voice_lib.LOG_DIR, exist_ok=True)
        with open(CONTACT_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  GET {RAW_URL}  ->  {outcome}\n")
    except OSError:
        pass


def _why(exc):
    """One sentence somebody can act on, instead of the exception's own words.

    'HTTPError: HTTP Error 404' is true and tells a person nothing; the useful
    part is that GitHub answered perfectly well and had no such file.
    """
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return ("GitHub has no version.json on that branch -- this copy may be "
                    "from a fork, or the project has moved.")
        if exc.code in (403, 429):
            return "GitHub is rate-limiting this address. Try again later."
        return f"GitHub answered {exc.code}."
    if isinstance(exc, urllib.error.URLError):
        return "No answer from GitHub -- offline, or something in the way."
    if isinstance(exc, ValueError):
        return "What came back was not the version file."
    return f"{type(exc).__name__}: {exc}"


def look_now(timeout=4.0):
    """Contact GitHub, whatever the setting says. Returns the cache it wrote.

    Never raises: a machine with no network is the ordinary case here, not an
    error, and an update check has no business being the thing that fails a
    command someone was in the middle of.
    """
    import urllib.request

    result = {"checked": time.time(), "local": local_version(),
              "remote": None, "error": "", "why": ""}
    try:
        req = urllib.request.Request(
            RAW_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Capped: this is a 400-byte file, and reading whatever arrives
            # would make a misrouted response somebody else's problem to bound.
            body = resp.read(16384).decode("utf-8", "replace")
        doc = json.loads(body)
        if not isinstance(doc, dict) or not doc.get("version"):
            raise ValueError("no version in the file")
        result["remote"] = doc
        _note_contact(f"{doc.get('version')}")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["why"] = _why(exc)
        _note_contact(f"failed ({type(exc).__name__})")
    _write_cache(result)
    return result


def cached():
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_cache(result):
    try:
        os.makedirs(voice_lib.LOG_DIR, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    except OSError:
        pass


def due(state=None):
    """Is an unprompted look allowed, and old enough to be worth making?"""
    state = state or voice_lib.load_state()
    if not state.get("updateCheck", False):
        return False
    days = state.get("updateCheckDays", 7)
    if not days or days <= 0:
        return False
    last = cached().get("checked") or 0
    return (time.time() - last) > days * 86400


def refresh_soon(state=None):
    """Start a look in the background if one is due, and return at once.

    Not done inline, and not done in the engine. Inline would make `status`
    wait on the network -- which offline means waiting for a timeout every
    single time -- and the engine is holding a model and the sound card and has
    no business making requests. So: a detached one-shot, exactly as the engine
    and the panel are started, and the answer is on screen the next time you
    ask rather than this time.
    """
    state = state or voice_lib.load_state()
    if not due(state):
        return False
    try:
        DETACHED = 0x00000008 | 0x08000000       # DETACHED_PROCESS | CREATE_NO_WINDOW
        subprocess.Popen(
            [voice_lib._python(), os.path.join(voice_lib.ROOT, "update_check.py"), "--refresh"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True,
            cwd=voice_lib.ROOT)
        return True
    except OSError:
        return False


def available(state=None):
    """The newer version's version.json, if the last look found one. No network."""
    doc = cached()
    remote = doc.get("remote") or {}
    version = remote.get("version")
    return remote if version and is_newer(version) else None


# --------------------------------------------------------------------------
# saying so
# --------------------------------------------------------------------------

def plain(text):
    """Remote text, made fit for a Windows console.

    Everything this prints may have been fetched a moment ago, and a console
    codepage that cannot encode a character raises rather than shrugs -- so
    somebody else's em dash would become a crash on your machine.
    """
    swaps = {"—": "--", "–": "-", "→": "->", "‘": "'",
             "’": "'", "“": '"', "”": '"', "…": "..."}
    for fancy, plainer in swaps.items():
        text = text.replace(fancy, plainer)
    return text.encode("ascii", "replace").decode("ascii")


def ago(when):
    if not when:
        return "never"
    seconds = max(0, int(time.time() - when))
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds // 3600} hours ago"
    return f"{seconds // 86400} days ago"


def status_line(state=None):
    """The single line `voice_cli.py status` prints. ASCII: this goes to a console."""
    state = state or voice_lib.load_state()
    doc = cached()
    newer = available(state)
    if newer:
        return plain(f"{shown_version()} -- {newer['version']} is out: "
                     f"{newer.get('headline', '')}").rstrip()

    on = bool(state.get("updateCheck", False))
    if not doc.get("checked"):
        # Nothing has ever looked. Whether that is about to change is the only
        # thing worth saying, and if it is not, say what would change it.
        return f"{shown_version()} -- " + ("checking in the background" if on else
                                           "not checked yet ('update' looks now)")
    # A look that has already happened outranks the setting: 'checks are off'
    # is a poor answer to "am I current?" when the answer is sitting in a file.
    said = (f"last check failed, {ago(doc['checked'])}" if doc.get("error") else
            f"up to date (checked {ago(doc['checked'])})")
    return f"{shown_version()} -- {said}" + ("" if on else ", auto-check off")


def last_look():
    """How the last look went, in a few words -- for a corner with room for one
    short line. Shared, so the panel and `status` cannot come to describe the
    same file two different ways."""
    doc = cached()
    if not doc.get("checked"):
        return "not checked yet"
    when = ago(doc["checked"])
    if doc.get("error"):
        return f"check failed, {when}"
    if available():
        return f"checked {when}"
    return f"up to date, {when}"


def changelog_section(version):
    """This copy's own account of a version, straight out of CHANGELOG.md.

    Read after pulling, when the file is the new one -- so what is printed is
    the release you just took, written out in full rather than in a headline.
    """
    try:
        with open(CHANGELOG_PATH, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ""
    out, taking = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if taking:
                break
            heading = line[3:].split()
            taking = bool(heading) and _parts(heading[0]) == _parts(version)
            continue
        if taking:
            out.append(line)
    return "\n".join(out).strip()


# --------------------------------------------------------------------------
# taking it
# --------------------------------------------------------------------------

# Where git is when PATH does not say, tried in this order and only after PATH
# itself. `*` is allowed: GitHub Desktop keeps its copy under a versioned folder.
#
# The panel is the reason this list exists. The command line pulled and the
# button said git was not on PATH, which is a confusing way to find out that the
# two do not have the same PATH -- and both were telling the truth. The panel is
# started from the Desktop shortcut, so its environment is Explorer's, built
# from the registry at login; a terminal inherits whatever started it, and
# Claude Code hands its children a PATH with a git of its own prepended. Git for
# Windows only lists itself if you tick the box during install, so a machine can
# have git, use git all day, and still have nothing about git in the registry.
#
# So: look in the places it lives. Refusing to update over a missing PATH entry
# helps nobody, and the alternative was somebody being told to download a zip by
# hand while git sat in a folder this could have named.
GIT_GUESSES = (
    r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe",
    r"%ProgramFiles%\Git\cmd\git.exe",
    r"%ProgramFiles(x86)%\Git\cmd\git.exe",
    r"%LOCALAPPDATA%\GitHubDesktop\app-*\resources\app\git\cmd\git.exe",
)

_GIT_EXE = ""       # "" is "not looked yet"; None is "looked, and there is none"


def _git_exe():
    """The git to run, or None if this machine has none. Looked up once.

    PATH first, always: an entry somebody put there is a choice, and it outranks
    anything guessed here.
    """
    global _GIT_EXE
    if _GIT_EXE != "":
        return _GIT_EXE
    found = shutil.which("git")
    if not found:
        for guess in GIT_GUESSES:
            # Reversed, so a versioned folder resolves to the newest of them.
            hits = sorted(glob.glob(os.path.expandvars(guess)), reverse=True)
            if hits:
                found = hits[0]
                break
    _GIT_EXE = found or None
    return _GIT_EXE


def _git(*args, cwd=None):
    """Returns (ok, output). Git missing is a plain False, not a traceback."""
    exe = _git_exe()
    if not exe:
        return False, "no git on this machine"
    try:
        done = subprocess.run([exe, "-C", cwd or voice_lib.ROOT] + list(args),
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    # rstrip, not strip: git's porcelain status puts a meaningful space in the
    # first column, and taking it off the first line alone misaligns the list.
    return done.returncode == 0, (done.stdout + done.stderr).rstrip()


def _by_hand(say):
    # Says no *why* of its own: both callers have just given one, and this used
    # to open with "no git here" in front of the case that has nothing to do
    # with git.
    say("")
    say("Taking it by hand instead:")
    say(f"  1. Download {ZIP_URL}")
    say("  2. Unpack it and copy the contents over this folder, replacing what is there.")
    say("  3. Run setup.ps1 again, then: voice_cli.py kill, then start.")
    say("")
    say("Nothing you own is in that zip, so this does not touch your settings,")
    say("your logs, or any voice you made yourself.")


def apply_update(state=None, say=print):
    """git pull, then put the engine back. Returns True if anything changed.

    The restart is the whole reason this exists. A pull replaces the code the
    running engine loaded at startup and the engine goes on running the old
    copy, so an update that is not followed by kill-and-start is an update that
    appears to have done nothing.
    """
    state = state or voice_lib.load_state()

    if not os.path.isdir(os.path.join(voice_lib.ROOT, ".git")):
        say("This copy was not cloned -- there is no .git folder to pull into.")
        _by_hand(say)
        return False

    exe = _git_exe()
    if not exe:
        say("No git on this machine -- not on PATH, and not in the folders it")
        say("usually installs into, so there is nothing here to pull with.")
        _by_hand(say)
        return False

    ok, refused = _git("rev-parse", "--git-dir")
    if not ok:
        say(f"git is here ({exe}) but would not run in this folder:")
        for line in refused.splitlines()[:5]:
            say(f"  {line}")
        return False

    if not shutil.which("git"):
        # Worth one line, once, on the machines where it is true: it is the whole
        # explanation of why the panel's button used to fail while the same
        # command worked in a terminal.
        say(f"git is not on PATH -- using the one at {exe}.")

    # Modified tracked files only. Untracked ones are not in the way of a pull,
    # and telling somebody their own new voice folder is blocking an update
    # would be both wrong and annoying.
    ok, dirty = _git("status", "--porcelain", "--untracked-files=no")
    if ok and dirty:
        say("Local changes to files this would overwrite:")
        for line in dirty.splitlines()[:10]:
            say(f"  {line}")
        say("")
        say("Nothing was pulled. Commit them, or 'git stash', then try again.")
        return False

    ok, upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not ok:
        say("This branch is not tracking anything, so there is nowhere to pull from.")
        say("  git branch --set-upstream-to=origin/main")
        return False

    ok, before = _git("rev-parse", "HEAD")
    if not ok:
        say("This repo has no commit to move from, which is not a state to pull into.")
        return False

    say(f"Pulling from {upstream}...")
    ok, output = _git("pull", "--ff-only")
    if not ok:
        say("git pull failed:")
        for line in output.splitlines()[:10]:
            say(f"  {line}")
        say("")
        say("A pull that will not fast-forward means this copy has commits of its")
        say("own. Sort that out by hand rather than having it done for you.")
        return False

    _, after = _git("rev-parse", "HEAD")
    if before == after:
        say("Already at the newest commit -- nothing came down.")
        return False

    _, changed = _git("diff", "--name-only", f"{before}..{after}")
    files = [f for f in changed.splitlines() if f.strip()]
    say(f"Updated to {shown_version()} ({len(files)} file(s) changed).")

    notes = changelog_section(local_version())
    if notes:
        say("")
        for line in notes.splitlines():
            say(("  " + plain(line)).rstrip())     # blank lines are blank, not two spaces

    # The note is the one installed file that can reinstall itself. Done here as
    # well as on engine start, so it still happens for somebody whose engine was
    # not running when they updated.
    if voice_lib.sync_notes(state=state):
        say("")
        say("Refreshed the speaking notes in ~\\.claude\\CLAUDE.md. Restart Claude")
        say("Code to pick them up -- that file is read once, at session start.")

    # --- and now the part everyone forgets --------------------------------
    say("")
    was_warm = voice_lib.server_alive(state["port"]) is not None
    if was_warm:
        say("Restarting the engine so the new code is the code that runs...")
        try:
            voice_lib.post(state["port"], "/quit", timeout=5)
        except Exception:
            pass
        time.sleep(1.5)
        if voice_lib.start_server(state, wait=120):
            say("Engine ready.")
        else:
            say("Engine is still loading; the next answer or two may pass in silence.")
    else:
        say("The engine was not running, so there was nothing to restart.")

    needs_setup = bool(local().get("needsSetup")) or any(
        f.startswith(p) or f == p for f in files for p in INSTALLED_FROM)
    if needs_setup:
        say("")
        say("This one changed a part that is installed rather than just read:")
        say("run setup.ps1 again, then restart Claude Code -- hooks and the")
        say("slash command are read once, at session start.")
    return True


# --------------------------------------------------------------------------

def main():
    args = [a.lstrip("-") for a in sys.argv[1:]]
    if "refresh" in args:
        # Checked again here rather than trusted from the caller: this is a
        # separate process that may have been queued behind another one.
        if due():
            look_now(timeout=6.0)
        return
    result = look_now()
    if result["error"]:
        print(plain(result.get("why") or result["error"]))
        return
    remote = result["remote"] or {}
    if is_newer(remote.get("version")):
        print(plain(f"{shown_version()} -> {remote['version']}: {remote.get('headline', '')}"))
    else:
        print(f"{shown_version()} is current.")


if __name__ == "__main__":
    main()
