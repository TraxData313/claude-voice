"""Pausing whatever else is playing while she talks, and starting it again after.

**Nothing imports this.** It is a spike, kept because it works and was proved on
a real machine, and left out of the engine on purpose: it is a background thread
polling COM, a config key, a tick box and a class of failure that ends in
somebody's meeting, in exchange for not having to press space. Run it by hand --
`python hush.py --try` -- and see docs/pausing-other-media.md, which is where the
findings live and what it would take to wire it in.

Listening to a podcast in a browser and having a voice talk over it is not two
things at once, it is neither -- so if something else is playing when she is
about to speak, this asks it to pause, and asks it to carry on once she has
finished.

Windows has no "pause that tab" call, so this is two halves that have to agree
with each other:

- **Knowing whether anything is playing** is Core Audio, the same COM the
  volume slider uses -- the session list of the default playback device, which
  process each session belongs to, and the peak meter of each one. A browser
  with a paused video keeps its session open and silent, so the meter is the
  question worth asking, not the session's own state.
- **Pausing it** is a media key, because that is the only pause every player
  understands. First as a window message aimed at the browser itself
  (WM_APPCOMMAND), which is polite and cannot touch anything else; and if the
  sound carries on, as the global media key, which whatever holds the media
  focus answers -- usually the browser, but not always.

The global key is a toggle, and a toggle sent blind is how you *start* music
nobody asked for. So nothing here is sent without checking the meter
afterwards: a pause is only recorded as ours if the sound actually stopped,
resuming only happens if it is still stopped when she finishes, and a key press
that changed nothing is pressed once more to undo whatever else it did.

**It keeps away from meetings.** Teams, Zoom, Discord and the rest are never in the
list of things to pause, and while any of them is making a sound the global key is
not pressed at all -- only the message aimed at the browser, which cannot reach
them. Being in a call beats pausing a video, every time.

What it cannot do, so that nobody goes looking:

- **A muted tab that is still "playing" is silent to the meter**, so a video
  playing on mute is not noticed and not paused. Nothing is lost by that.
- **Chrome plays everything through one audio process**, so the pid on the
  session is not the pid of the window. The window is found by the *name* of
  the executable instead, which is why a second browser of the same brand gets
  the same message.
- **A browser started after she began talking** is not caught until the next
  thing she says. This looks once, when she starts.
- **If the engine is killed outright while she is speaking**, whatever was
  paused stays paused. Press play; nothing is broken.
"""

import contextlib
import ctypes
import threading
import time
from ctypes import POINTER, byref, c_float, c_int, c_uint32, c_void_p

import voice_lib
# The COM plumbing is already written once, for the volume slider: a vtable
# call by slot number, a release, and a string to a GUID. Two copies of that
# would be two places to get a slot number wrong.
from win_volume import GUID, _call as call, _release as release

ole32 = ctypes.windll.ole32
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
# The '2' is what makes this worth having over the interface win_volume asks
# for: it is the one with a session *enumerator* on it, so every application
# playing anything can be walked rather than only our own.
IID_IAudioSessionManager2 = "{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}"
IID_IAudioSessionControl2 = "{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}"
IID_IAudioMeterInformation = "{C02216F6-8C67-4B5B-9D00-D008E73E0064}"

CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0
RENDER, CONSOLE = 0, 0                # eRender, eConsole
ACTIVE = 1                            # AudioSessionStateActive

# Below this a session is silence rather than sound. Not zero: a stream that is
# open and playing digital silence still meters a few millionths, and a decoder
# tailing off does the same.
SILENT = 0.0005

# What the media buttons on a keyboard send. The window message carries a
# separate pause and play, which is the whole reason it is tried first -- the
# key itself only has the one, and a toggle can only be used by somebody who
# already knows which way round things are.
WM_APPCOMMAND = 0x0319
APPCOMMAND_MEDIA_PLAY = 46
APPCOMMAND_MEDIA_PAUSE = 47
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002

# Whose sound counts as "something else playing". Browsers, because that is
# what a video or a podcast is playing in; overridable in config.json, since
# somebody who wants a music player paused too should not have to be asked to
# edit this file. Lower case, and compared lower case.
BROWSERS = (
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "opera_gx.exe", "vivaldi.exe", "chromium.exe", "librewolf.exe",
    "waterfox.exe", "zen.exe", "arc.exe", "thorium.exe",
)

# Whose sound means somebody is in a meeting. The window message is aimed at one
# window and cannot reach these; the global media key is aimed at nothing in
# particular and in principle could. So while any of them is making a sound the
# key is not pressed at all -- being in a call is worth more than pausing a
# video, and "it did nothing" is a far better failure than "it did something to
# my meeting". The targeted message is still sent, since it is safe.
CALLS = (
    "ms-teams.exe", "teams.exe", "zoom.exe", "discord.exe", "slack.exe",
    "webex.exe", "webexmta.exe", "skype.exe", "lync.exe", "bluejeans.exe",
    "gotomeeting.exe", "ringcentral.exe",
)

# How long to give a browser to act on what it was just sent, and how much
# unbroken silence counts as having stopped.
#
# The first version demanded silence within a fifth of a second and was simply
# wrong: Chrome pauses immediately but its audio is already in flight, and the
# meter kept reading for long enough that the check called it a failure. It then
# skipped the fallback, recorded nothing as paused -- and so never put back the
# video it had in fact just paused. A pause it cannot see is worse than a slow
# one, because the resume hangs off it.
#
# So it waits for silence rather than demanding it, up to a second and a half,
# and then wants that silence to hold. A third of a second is the number that
# separates the two things being told apart: a gap between two words in a
# podcast is shorter, and a paused video is silent for good.
SETTLE_WAIT, SETTLE_QUIET, SETTLE_GAP = 1.5, 0.35, 0.05

# How long to leave a browser alone after it has refused to pause. Without this
# a player that answers nothing is tried again on every single thing she says --
# three seconds of waiting each time, and two media key presses each time, which
# is a lot of keystrokes going somewhere for a feature that has already failed.
# Cleared as soon as what is playing changes, so a different tab gets its turn.
GAVE_UP_FOR = 120.0


# --------------------------------------------------------------------------
# what is making a noise
# --------------------------------------------------------------------------

@contextlib.contextmanager
def _session_list():
    """Every audio session on the default playback device, for one call.

    Built and torn down per call, exactly as win_volume does it, and for the
    same reason: COM belongs to the thread that started it, and holding an
    interface pointer across a sleep means holding an apartment open too.
    """
    started = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    ours = started in (0, 1)             # S_OK and S_FALSE are both ours to undo
    made = []
    try:
        devices = c_void_p()
        if ole32.CoCreateInstance(byref(GUID(CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
                                  byref(GUID(IID_IMMDeviceEnumerator)), byref(devices)):
            raise OSError("no audio device enumerator")
        made.append(devices)

        speakers = c_void_p()                             # GetDefaultAudioEndpoint
        call(devices, 4, RENDER, CONSOLE, byref(speakers),
             types=(c_int, c_int, POINTER(c_void_p)))
        made.append(speakers)

        manager = c_void_p()                              # IMMDevice::Activate
        call(speakers, 3, byref(GUID(IID_IAudioSessionManager2)), CLSCTX_ALL, None,
             byref(manager), types=(c_void_p, c_uint32, c_void_p, POINTER(c_void_p)))
        made.append(manager)

        sessions = c_void_p()                             # GetSessionEnumerator
        call(manager, 5, byref(sessions), types=(POINTER(c_void_p),))
        made.append(sessions)
        yield sessions
    finally:
        for ptr in reversed(made):
            release(ptr)
        if ours:
            ole32.CoUninitialize()


def _exe_name(pid):
    """The bare name of the executable behind a process id, lower case."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = c_uint32(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(handle)


def _peak(control):
    """How loud this session is right now, 0.0 to 1.0.

    The meter is another interface on the same session object, so it is asked
    for rather than looked up: QueryInterface is slot 0 of everything.
    """
    meter = c_void_p()
    call(control, 0, byref(GUID(IID_IAudioMeterInformation)), byref(meter),
         types=(c_void_p, POINTER(c_void_p)))
    try:
        level = c_float()
        call(meter, 3, byref(level), types=(POINTER(c_float),))   # GetPeakValue
        return level.value
    finally:
        release(meter)


def _pid_of(control):
    """Which process this session belongs to, via IAudioSessionControl2."""
    two = c_void_p()
    call(control, 0, byref(GUID(IID_IAudioSessionControl2)), byref(two),
         types=(c_void_p, POINTER(c_void_p)))
    try:
        pid = c_uint32()
        call(two, 14, byref(pid), types=(POINTER(c_uint32),))     # GetProcessId
        return pid.value
    finally:
        release(two)


def loud(names):
    """Which of these executables are making a sound this instant.

    A set, not a list of sessions: one browser is one thing to pause however
    many tabs it has open, and the caller only ever needs the name to find a
    window with.
    """
    names = {n.lower() for n in names}
    found = set()
    with _session_list() as sessions:
        count = c_int()
        call(sessions, 3, byref(count), types=(POINTER(c_int),))       # GetCount
        for i in range(count.value):
            control = c_void_p()
            call(sessions, 4, i, byref(control),
                 types=(c_int, POINTER(c_void_p)))                     # GetSession
            try:
                state = c_int()
                call(control, 3, byref(state), types=(POINTER(c_int),))  # GetState
                if state.value != ACTIVE:
                    continue
                name = _exe_name(_pid_of(control))
                if name in names and name not in found and _peak(control) > SILENT:
                    found.add(name)
            except OSError:
                continue          # a session that ended between count and read
            finally:
                release(control)
    return found


def _playing(names, tries=3, gap=SETTLE_GAP):
    """Which of these is making a sound, over a moment rather than an instant.

    A single reading lands wherever it lands, and speech has gaps in it: asked
    once, a podcast between two words looks like nothing playing at all.
    """
    found = set()
    for i in range(tries):
        if i:
            time.sleep(gap)
        found |= loud(names)
    return found


def _silent(names, wait=SETTLE_WAIT, quiet=SETTLE_QUIET, gap=SETTLE_GAP):
    """Wait for them to go quiet, and for the quiet to hold. True if it did.

    Returns the moment the silence has lasted long enough, so the usual case is
    quick; it only spends the full wait when nothing ever stops.
    """
    deadline = time.monotonic() + wait
    hush_since = None
    while True:
        if loud(names):
            hush_since = None
        else:
            now = time.monotonic()
            if hush_since is None:
                hush_since = now
            elif now - hush_since >= quiet:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(gap)


# --------------------------------------------------------------------------
# asking it to stop, and to start again
# --------------------------------------------------------------------------

def _windows_for(names):
    """A visible top-level window belonging to each of these executables.

    One per name. A browser has a window per profile and per popup, and every
    one of them routes a media key to the same place, so the first will do.
    """
    names = {n.lower() for n in names}
    found = {}
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, c_void_p, c_void_p)

    def look(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            pid = c_uint32()
            user32.GetWindowThreadProcessId(hwnd, byref(pid))
            name = _exe_name(pid.value)
            if name in names and name not in found:
                found[name] = hwnd
        return True

    user32.EnumWindows(proto(look), 0)
    return list(found.values())


def _appcommand(hwnd, command):
    """Send one media command to one window, without waiting for an answer.

    Posted rather than sent: a browser that is busy would otherwise hold up the
    engine, and there is nothing in the reply worth having -- whether it worked
    is answered by the meter a moment later, not by this.
    """
    user32.PostMessageW(c_void_p(hwnd), WM_APPCOMMAND, c_void_p(hwnd), command << 16)


def _media_key():
    """The play/pause key on a keyboard, pressed and released."""
    user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
    user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_KEYUP, 0)


class Hush:
    """Whatever we paused, and how, so that it can be put back the same way."""

    def __init__(self, log=print):
        self.log = log
        self.held = None              # (how, {names}) while something is ours
        self.lock = threading.Lock()
        self.gave_up = {}             # what refused to pause, and when

    def hold(self, names=BROWSERS):
        """Pause anything of these that is playing. True if something stopped."""
        with self.lock:
            if self.held:
                return True
            playing = _playing(names)
            if not playing:
                return False
            since = self.gave_up.get(frozenset(playing))
            if since is not None and time.monotonic() - since < GAVE_UP_FOR:
                return False          # asked recently, refused recently

            windows = _windows_for(playing)
            for hwnd in windows:
                _appcommand(hwnd, APPCOMMAND_MEDIA_PAUSE)
            if windows and _silent(playing):
                self.held = ("window", playing)
                self.log(f"paused {', '.join(sorted(playing))} while speaking")
                return True

            in_a_call = loud(CALLS)
            if in_a_call:
                # Not remembered as a refusal: it is the call that stopped this,
                # not the browser, and the call will end.
                self.log(f"{', '.join(sorted(playing))} ignored the pause, and "
                         f"{', '.join(sorted(in_a_call))} is in a call -- "
                         f"leaving the media key alone")
                return False

            _media_key()
            if _silent(playing):
                self.held = ("key", playing)
                self.log(f"paused {', '.join(sorted(playing))} with the media key")
                return True

            # It did nothing to them -- but a play/pause key is never nothing,
            # so put back whatever else it may have started or stopped.
            _media_key()
            self.gave_up[frozenset(playing)] = time.monotonic()
            self.log(f"{', '.join(sorted(playing))} would not pause; left alone "
                     f"for now")
            return False

    def free(self):
        """Start again whatever we stopped, if it is still stopped."""
        with self.lock:
            if not self.held:
                return
            how, names = self.held
            self.held = None
            if loud(names):
                return                # started again by hand; not ours any more
            if how == "window":
                for hwnd in _windows_for(names):
                    _appcommand(hwnd, APPCOMMAND_MEDIA_PLAY)
            elif loud(CALLS):
                # Same rule as pausing, and it costs the same thing: whatever
                # we stopped stays stopped until somebody presses play. That is
                # a podcast waiting, against a keystroke landing in a meeting.
                self.log("a call is running -- not pressing the media key to resume")
                return
            else:
                _media_key()
            self.log(f"{', '.join(sorted(names))} playing again")


# --------------------------------------------------------------------------
# the thread that watches her
# --------------------------------------------------------------------------

class Watcher(threading.Thread):
    """Pause when she starts, start again once she has been quiet a moment.

    It watches rather than being called, so that nothing in the speaking path
    waits on COM: pausing takes up to a fifth of a second, and the answer to
    "is she talking" is a flag anybody can read. Being a poll also means it
    catches the *synthesis* starting, which is a second or two before the first
    word -- so by the time she speaks, the other thing has already stopped.
    """

    TICK = 0.2
    # How long she must have been quiet before the podcast comes back. The gap
    # between two queued messages is 0.45s and the gap between a message and
    # the next tool call is longer, and neither is a reason to hear two seconds
    # of something else and then lose it again.
    GRACE = 2.0
    # The config is re-read on this, so ticking the box in the panel takes
    # effect within a couple of seconds and needs no restart.
    CONFIG_TTL = 2.0

    def __init__(self, busy, log=print):
        super().__init__(name="hush", daemon=True)
        self.busy = busy
        self.hush = Hush(log)
        self.log = log
        self.stopping = threading.Event()
        self._settings = (False, BROWSERS, self.GRACE)
        self._read_at = 0.0
        self._quiet_since = None
        self._complained = False

    def settings(self):
        now = time.monotonic()
        if now - self._read_at > self.CONFIG_TTL:
            self._read_at = now
            try:
                state = voice_lib.load_state()
                names = state.get("pauseApps") or BROWSERS
                self._settings = (bool(state.get("pauseMedia")),
                                  tuple(str(n).lower() for n in names),
                                  float(state.get("pauseGraceSeconds", self.GRACE)))
            except (OSError, ValueError, TypeError):
                pass              # a config caught half-written; keep the last
        return self._settings

    def run(self):
        while not self.stopping.wait(self.TICK):
            try:
                self._pass()
            except Exception as exc:
                # Once. Something is wrong with the audio session list or with
                # the window messages, and saying so every fifth of a second
                # would bury the log it belongs in.
                if not self._complained:
                    self._complained = True
                    self.log(f"pausing other media is not working here: {exc}")

    def _pass(self):
        on, names, grace = self.settings()
        if not on:
            if self.hush.held:
                self.hush.free()      # turned off while holding something
            return
        if self.busy():
            self._quiet_since = None
            if not self.hush.held:
                self.hush.hold(names)
            return
        if not self.hush.held:
            return
        if self._quiet_since is None:
            self._quiet_since = time.monotonic()
        elif time.monotonic() - self._quiet_since >= grace:
            self._quiet_since = None
            self.hush.free()

    def stop(self):
        """On the way out: never leave somebody's podcast paused."""
        self.stopping.set()
        try:
            self.hush.free()
        except Exception:
            pass
# --------------------------------------------------------------------------
# checking it by hand
# --------------------------------------------------------------------------

def main():
    """`python hush.py` says what it can see; `--try` pauses and starts again.

    Worth having because everything above is guesswork about somebody else's
    window: whether a browser answers a media key at all is a question about
    that browser, that machine and that page, and the only honest way to answer
    it is to play something and watch. This is that, in one command.
    """
    import sys

    names = tuple(a.lower() for a in sys.argv[1:] if not a.startswith("-")) or BROWSERS
    playing = _playing(names)
    print("watching:", ", ".join(names))
    print("playing now:", ", ".join(sorted(playing)) or "nothing")
    print("windows found:", _windows_for(names) or "none")
    if "--try" not in sys.argv:
        print("play something in a browser, then run this again with --try")
        return
    if not playing:
        print("nothing to pause -- play something first")
        return

    quiet = Hush()
    if not quiet.hold(names):
        return
    print("waiting three seconds, as if she were talking...")
    time.sleep(3)
    quiet.free()
    # Give it as long to start as it was given to stop. Asked the instant the
    # play is sent, a video that resumes perfectly reads as one that never came
    # back -- which is exactly the impatience that broke the pause check.
    deadline = time.monotonic() + SETTLE_WAIT
    back = set()
    while not back and time.monotonic() < deadline:
        back = loud(names)
        time.sleep(SETTLE_GAP)
    print("playing again:", ", ".join(sorted(back)) or "nothing -- it did not come back")


if __name__ == "__main__":
    main()
