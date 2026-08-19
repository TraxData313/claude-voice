"""
A small window that shows what the voice is doing, and lets you steer it.

    python panel.py            # or: python voice_cli.py panel

It floats on top -- there is a tick box to stop it doing that -- and is
deliberately plain:

    what is playing now, and which session it came from
    which voice is speaking, and how big to draw them
    load the engine or give its memory back, turn the voice off, skip a line,
    skip everything -- the two switches are green while working, red while not
    how loud it is
    what is queued behind it, and a box to add a line of your own to it
    what has been said -- click a line to hear it again
    which sessions are heard and which are muted
    and Abby along the bottom, whoever is actually talking

One rule keeps this simple: **the panel owns no state**. It is a view over the
engine's HTTP API, polling /state about twice a second; everything it shows
came from there and everything it does is a POST. Close it and nothing changes
anywhere. Open it before the engine is even running and it offers to start one.

Two threads, and the line between them matters. urllib is never called from the
Tk thread, because a request that takes a second would freeze the window for a
second; Tk is never touched from the polling thread, because Tk is
single-threaded and reaching into it from elsewhere corrupts it in ways that
surface much later, somewhere else. So the poller only puts results in a queue,
and the UI drains that queue on a timer of its own.
"""

import argparse
import ctypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import font as tkfont
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_check
import voice_lib

POLL_SECONDS = 0.5
DRAIN_MS = 120
# How long the volume slider waits, after you stop moving it, before saying so.
SEND_MS = 160
# Tall enough to open with a proper picture of her at the bottom rather than a
# strip. She is most of the reason the window is this shape.
DEFAULT_GEOMETRY = "440x860"
# WxH, optionally offset: '+100+80', or '-8+0' meaning 8 in from the right, or
# '+-8+0' meaning 8 past the left edge. Tk writes all three.
GEOMETRY = re.compile(r"^(\d+x\d+)(?:([+-])(-?\d+)([+-])(-?\d+))?$")

FONT = ("Segoe UI", 9)
# The transport row is icons, and this is the font that has them as plain
# monochrome shapes. Windows draws U+23F8 and friends out of Segoe UI Emoji if
# nobody says otherwise -- little boxed colour pictures, which on a green
# button look like clip art stuck to it. Named explicitly, they come out as
# solid glyphs that take the button's own foreground colour like any letter.
#
# It has shipped with Windows since 7, but it is checked for rather than
# assumed: a missing font is not an error in Tk, it is a silent substitution,
# and the substitute here is the boxed emoji. Without it the buttons go back to
# saying what they do in words.
ICON_FAMILY = "Segoe UI Symbol"
# And two glyphs that font does not do at all well. U+2699, the cog, comes out
# of Segoe UI Symbol as a small ring with a dot in it -- at button size that
# reads as a record button rather than a cog -- and it has nothing resembling a
# chip. Windows 10 and 11 ship a whole icon set with proper ones, so these two
# buttons borrow from it and nothing else does. Private use codepoints are
# font-specific by definition, hence the fallback behind each.
#
# The cog is settings, where a cog means what everybody already thinks it
# means, and the chip is the engine: what that button loads and hands back is
# three and a half gigabytes of model, and a cog on it only ever said
# "something machinery". Both were drawn and looked at; the chip won by eye
# over a power symbol, a bolt and a robot.
MDL2_FAMILY = "Segoe MDL2 Assets"
MDL2_GEAR = "\ue713"
MDL2_CHIP = "\ue950"
FONT_SMALL = ("Segoe UI", 8)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_LINK = ("Segoe UI", 8, "underline")
GREY = "#666666"
LINK = "#1a5fb4"
LINK_DARK = "#7aa7ff"

# What the window is called, on its title bar and in the taskbar. The project
# is claude-voice; the thing with a face on it is Abby, and the Desktop
# shortcut says the same, so that the icon and the window agree.
APP_NAME = "Abby for Claude"
AUTHOR = "TraxData313"
AUTHOR_URL = "https://github.com/TraxData313"
REPO_URL = "https://github.com/TraxData313/claude-voice"

# What Windows opens when you log in is whatever is in this folder, and it is
# per-user -- which is why nothing here needs administrator rights, the same
# reason the installer does not. The name matches the Desktop shortcut, so the
# two are one icon in two places rather than two things called the same.
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                           "Start Menu", "Programs", "Startup")
STARTUP_LNK = os.path.join(STARTUP_DIR, f"{APP_NAME}.lnk")
# Writing a .lnk means COM, and there is no binding for it in a project with no
# dependencies -- so it is PowerShell's WScript.Shell, the same object
# make_shortcut.ps1 hands the Desktop icon to, writing the same shortcut.
#
# The values arrive through the environment rather than on the command line. A
# repo path with a space in it is the obvious way this breaks, and quoting
# PowerShell inside Python inside a shell is three chances to get it wrong.
SHORTCUT_PS = ("$s = New-Object -ComObject WScript.Shell; "
               "$l = $s.CreateShortcut($env:CV_LINK); "
               "$l.TargetPath = $env:CV_EXE; "
               "$l.Arguments = $env:CV_ARGS; "
               "$l.WorkingDirectory = $env:CV_DIR; "
               "$l.Description = $env:CV_TEXT; "
               "if (Test-Path $env:CV_ICON) { $l.IconLocation = \"$($env:CV_ICON),0\" }; "
               "$l.Save()")
NO_WINDOW = 0x08000000

# Dark is a set of colours plus a change of ttk theme. The native Windows theme
# draws real Windows widgets and ignores most colour you ask it for; 'clam' is
# drawn by Tk itself and does as it is told. So light stays native, and dark
# switches theme -- which is also why every style has to be set again on the
# way in: ttk keeps its settings per theme, not per widget.
DARK = {"bg": "#24262b", "field": "#1c1e22", "fg": "#e4e6eb",
        "dim": "#9aa0a6", "sel": "#3a4a63", "line": "#3a3d44"}
ICON_DIR = os.path.join(voice_lib.ROOT, "docs", "icons")
ART_DIR = os.path.join(voice_lib.ROOT, "docs", "art")
# Abby along the bottom, whoever happens to be speaking. She is the face of the
# thing rather than a readout of anything -- the window already says whose voice
# it is, in three other places -- so she stays put when the voice changes.
ART = "abby"
ART_STEP = 16                # ask in steps, so a slow drag is not a hundred redraws
# The window will not shrink below its contents plus this much, so there is
# always room for the whole of her. Any less and she would have to be cropped
# to fit, and she is shown whole or not at all.
ART_FLOOR = 180
FACE = 128                   # the portrait beside the line being spoken
# The four worth offering. Any number can still be typed in, and the sizes in
# between were a longer list saying nothing a person choosing would want said.
FACE_SIZES = ("48", "96", "128", "256")
# The button row used to set this and no longer does: as icons those four want
# 159 pixels rather than 332, which is less than anything else in the window.
# What sets it now is the header: 136 for the portrait column, 26 of padding,
# and about 185 for the longest thing the line above the words ever says --
# "nothing is being spoken anywhere". Tried 320, which cut that line in half.
#
# For the record, because it has moved a lot: 420 for most of this window's
# life, 466 when the engine switch briefly shared a row with the tick boxes,
# 352 once they moved to the top strip, and 348 now the row is icons. Below
# about 396 the update note in the footer truncates -- that label is anchored
# west and cut to 30 characters precisely so it can be the thing that gives.
#
# It did not move when the tick boxes went into a settings dialog, which was
# expected to drop it. The strip fell from 190 to 33 -- one button where three
# tick boxes were -- and it turns out the strip was never what set this: the
# constant is a hand-measured 348 for the longest thing the line above the words
# says, and nothing else asks that much. Measured again rather than assumed,
# which is the whole reason the number is written down here.
#
# The button row is the closest thing to it now that the volume slider is on the
# end of it: 262 in dark and 270 in light, against the 332 the row is given at
# 348 wide. That headroom is what VOLUME_WIDE is chosen to protect.
MIN_WIDE = 348
FACE_MIN, FACE_MAX = 24, 320
ROW_ICON = 24                # and the small one on every row
# The volume slider shares the button row now, and the least it will accept is
# what decides whether that row is the thing setting the window's minimum
# width. Small on purpose: it grows into whatever the row has spare, so this is
# a floor rather than a size. At 90 the row asks 268 of the 332 it has at the
# narrowest the window goes, so the row is still not what sets it.
VOLUME_WIDE = 90
# However many sessions are live, only the newest few are worth a tick box --
# an unbounded list pushes the history off the window exactly as a huge
# portrait does.
MAX_SESSIONS = 5
# The two voices the repo ships. Clicking the portrait swaps between them.
SHIPPED = ("abby", "max")
# What a line you typed yourself is filed under. Every other line in the queue
# came from a folder Claude was working in, and the column says which; this one
# came from the box in this window, and an empty cell would not say that.
TYPED_PROJECT = "manual input"
# The master switch says what pressing it *does*; its colour says what is
# happening *now*. So a green button reads "turn off", and that is the right way
# round -- green is the voice working, and the label is what you would be doing
# to it. Getting these two the same way round would mean either a button that
# does not say what it does or a colour that does not say how things are.
#
# Face, then the same again lighter for the pointer being over it. Both
# switches wear them: the engine either holds the model or it does not, and the
# voice either speaks or it does not, and green-means-working reads the same on
# either one.
POWER_ON = ("#2f7d4f", "#3a9a61")
POWER_OFF = ("#a33a3a", "#c04a4a")
# Nothing to ask, so the switch has nothing to report -- the voice switch while
# there is no engine, and the engine switch during the minute it takes to load
# one. Grey rather than red: red is a state it has settled into, and neither of
# these has settled yet.
POWER_DEAD = ("#6b6e76", "#6b6e76")

# What each button shows, and what it says instead when the icon font is
# missing. The pair matters: an icon nobody can name is a puzzle rather than a
# control, which is what the hover text is for -- but if even that font is
# gone, words are better than boxes.
#
# Fast-forward for one line and next-track for all of them: they differ by the
# bar at the end, which is the difference itself -- one more, or straight to
# where there is nothing left.
GLYPH = {
    "engine": ("\u2638", "engine"),        # see MDL2_CHIP, preferred over this
    "settings": ("\u2699", "settings"),    # and MDL2_GEAR over this one
    "play": ("\u25b6", "turn on"),         # the voice is off; this starts it
    "stop": ("\u25a0", "turn off"),        # it is on; this stops it. Not a
                                           # pause: there is no coming back to
                                           # the sentence it cut off.
    "skip": ("\u23e9", "skip line"),       # >>
    "skip_all": ("\u23ed", "skip all"),    # >|
    "add": ("+", "read custom text"),      # into the queue, in your own words
}

# For the voices with no picture -- which is most of them. Picked from the id,
# so a voice keeps its colour between runs and between rows.
PALETTE = ["#2A9D8F", "#E9A13B", "#4C7FE0", "#8E6FD0", "#D96A82", "#4CA36A"]


class Tip:
    """The little label that appears under a button if you rest on it.

    Tk has no tooltip, and once the transport row stopped using words it needed
    one: an icon is only obvious to somebody who already knows what it does.
    It is a borderless Toplevel with a label in it, shown on a timer so that
    crossing the row on the way somewhere else does not flash four of them.

    The words can be a callable, because half of these buttons do different
    things depending on how the thing is set -- and a tooltip that says the
    wrong one of the two is worse than none at all.
    """

    DELAY = 450                   # long enough not to fire while passing over

    def __init__(self, widget, words, dark, keep=False):
        self.widget = widget
        self.words = words        # str, or a callable returning one
        self.dark = dark          # callable: is the panel in dark theme
        self.tip = None
        self.said = None          # the label inside it, so the words can change
        self.timer = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        if not keep:
            # A press has been understood, so the explanation has done its job.
            # Except on the volume slider, where pressing is the beginning of
            # using it and the tooltip is also the only readout it has.
            widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _event=None):
        self._leave()
        self.timer = self.widget.after(self.DELAY, self._show)

    def _leave(self, _event=None):
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
            self.said = None

    def now(self):
        """Say it this instant, or change what it is already saying.

        For the volume slider, whose tooltip does the job the number beside it
        used to: dragging has to show where the handle has got to, and waiting
        450ms to find out is not a readout.
        """
        if self.tip is None:
            self._leave()             # drop any timer, then do it now
            return self._show()
        words = self.words() if callable(self.words) else self.words
        if words and self.said is not None:
            self.said.configure(text=words)

    def _show(self):
        self.timer = None
        words = self.words() if callable(self.words) else self.words
        if not words or not self.widget.winfo_exists():
            return
        dark = self.dark()
        self.tip = tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)          # no title bar, no border, no taskbar
        tip.wm_attributes("-topmost", True)    # the panel itself usually is
        self.said = tk.Label(tip, text=words, font=FONT_SMALL, justify="left",
                             padx=6, pady=3,
                             background=DARK["field"] if dark else "#ffffe1",
                             foreground=DARK["fg"] if dark else "#000000",
                             relief="solid", borderwidth=1)
        self.said.pack()
        # Under the button rather than over it, so it never covers the thing
        # you are pointing at -- and pulled left if it would run off the edge.
        tip.update_idletasks()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        right = self.widget.winfo_screenwidth() - tip.winfo_reqwidth() - 4
        tip.wm_geometry(f"+{min(x, right)}+{y}")


def colour_for(voice_id):
    return PALETTE[sum((voice_id or "?").encode("utf-8")) % len(PALETTE)]


def initial(voice_id):
    letter = (voice_id or "?").lstrip("_-")
    return (letter[:1] or "?").upper()


class Icons:
    """The voice pictures, loaded once each and then held on to.

    Tk keeps no reference of its own to an image: hand a PhotoImage to a widget
    and let go of it, and it is collected and the widget shows nothing at all.
    Hence the cache -- it is not only about avoiding the re-read.

    Most voices have no picture. That is not an error, it is the normal case,
    and the caller draws a coloured initial instead.
    """

    def __init__(self, where=None):
        self.where = where or ICON_DIR
        self.loaded = {}
        self.on_disk = {}
        self.chose = {}          # which file each drawn size came from, and how
        self.fitted = None       # the one picture drawn big, and the box it fits

    def get(self, voice_id, size):
        key = (voice_id, size)
        if key not in self.loaded:
            path = os.path.join(self.where, f"{voice_id}-{size}.png")
            try:
                self.loaded[key] = tk.PhotoImage(file=path) if os.path.exists(path) else None
            except tk.TclError:
                self.loaded[key] = None            # unreadable png; the letter will do
        return self.loaded[key]

    def stored(self, voice_id):
        """Which sizes were drawn for this voice and committed as files."""
        if voice_id not in self.on_disk:
            found = []
            for name in os.listdir(self.where) if os.path.isdir(self.where) else []:
                stem, dot, ext = name.rpartition(".")
                if ext != "png" or not dot:
                    continue
                vid, dash, size = stem.rpartition("-")
                if dash and vid == voice_id and size.isdigit():
                    found.append(int(size))
            self.on_disk[voice_id] = sorted(found)
        return self.on_disk[voice_id]

    def at(self, voice_id, want):
        """The portrait at, or as near as Tk can manage to, a size in pixels.

        PhotoImage scales by whole numbers only -- zoom multiplies, subsample
        divides -- so an arbitrary size is reached by doing both, from whichever
        stored size gets there most cheaply. 160 from the 96px file is five up
        and three down, exactly; a size with no such pair lands on the nearest
        that has one.

        Returns (image, actual size), or (None, want) for a voice with no
        picture -- the caller draws its initial instead, at any size it likes.
        """
        key = (voice_id, "at", want)
        if key in self.loaded:
            return self.loaded[key]
        best = None
        for src in self.stored(voice_id):
            for zoom in range(1, 9):
                if src * zoom > 1536:              # keep the working copy sane
                    break
                for shrink in range(1, 9):
                    if src * zoom % shrink:
                        continue                   # not a whole number of pixels
                    got = src * zoom // shrink
                    # Right size first, then the one that will look best.
                    # Zoom is pixel doubling, plain and blocky, so a file drawn
                    # at this exact size beats everything, coming down from a
                    # bigger file beats going up from a smaller one, and less
                    # zoom beats more. Cheapest-to-compute is not a criterion:
                    # picking that gave 192 by doubling the 96, which looked it.
                    native = zoom == 1 and shrink == 1
                    score = (abs(got - want), 0 if native else 1 if src >= want else 2,
                             zoom, -src)
                    if best is None or score < best[0]:
                        best = (score, got, src, zoom, shrink)
        if best is None:
            self.loaded[key] = (None, want)
            return self.loaded[key]
        _score, got, src, zoom, shrink = best
        self.chose[(voice_id, want)] = (src, zoom, shrink)
        image = self.get(voice_id, src)
        if image is not None:
            if zoom > 1:
                image = image.zoom(zoom)
            if shrink > 1:
                image = image.subsample(shrink)
        self.loaded[key] = (image, got if image is not None else want)
        return self.loaded[key]


    def fit(self, voice_id, wide, tall, over=1.12):
        """The largest a picture can be drawn in a box without distorting it.

        Not the same question as at(): that one is about a square of a given
        size, this one is about filling a space of whatever shape the window
        has left over. Returns (image, width, height), or (None, 0, 0).

        Width may overshoot, because the canvas clips it and a picture that
        reaches both edges looks intended while one sitting in a margin looks
        like a mistake -- and what goes over the edge is scenery, not her.
        Height may not: she is shown whole, and a window too short for that
        gets a smaller picture rather than a cropped one. The window's own
        minimum keeps a size worth looking at always possible.

        One result is kept, not a cache of them. Dragging a window edge asks
        this a hundred times, and every answer is a few megabytes of pixels.
        """
        key = (voice_id, wide, tall)
        if self.fitted and self.fitted[0] == key:
            return self.fitted[1]
        best = None
        for src in self.stored(voice_id):
            base = self.get(voice_id, src)
            if base is None:
                continue
            for zoom in range(1, 5):
                if base.width() * zoom > 2048:      # keep the working copy sane
                    break
                for shrink in range(1, 9):
                    # zoom multiplies exactly; subsample keeps every nth pixel
                    # and rounds up, so the arithmetic has to as well.
                    w = -(-base.width() * zoom // shrink)
                    h = -(-base.height() * zoom // shrink)
                    if w > wide * over or h > tall or w < 60:
                        continue
                    # Biggest wins; between two of a size, the one that got
                    # there with less zoom, since zoom is pixel doubling.
                    score = (-w * h, zoom, -src)
                    if best is None or score < best[0]:
                        best = (score, base, zoom, shrink, w, h)
        if best is None:
            self.fitted = (key, (None, 0, 0))
            return self.fitted[1]
        _score, base, zoom, shrink, w, h = best
        picture = base
        if zoom > 1:
            picture = picture.zoom(zoom)
        if shrink > 1:
            picture = picture.subsample(shrink)
        self.fitted = (key, (picture, w, h))
        return self.fitted[1]


def one_line(text, width):
    """Collapse to a single line short enough to sit in a list."""
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 1].rstrip() + "…"


def _percent(level, fallback=100):
    """A 0-to-1 volume as a whole percent, or the fallback if it is nonsense."""
    try:
        return max(0, min(100, int(round(float(level) * 100))))
    except (TypeError, ValueError):
        return fallback


def _sane_size(value, fallback=FACE):
    try:
        return max(FACE_MIN, min(FACE_MAX, int(float(value))))
    except (TypeError, ValueError):
        return fallback


def starts_with_windows():
    """Whether the login shortcut is there. This is the whole of that setting.

    There is no config key behind the tick box: Windows opens what is in that
    folder, so the folder is the truth, and a key beside it would only be a
    second opinion capable of disagreeing with it.
    """
    return os.path.isfile(STARTUP_LNK)


def make_startup_shortcut():
    """Write the login shortcut. Says nothing -- the caller re-reads the folder.

    A quarter of a second, measured, which is why it is done on the Tk thread
    and read straight back. A tick box that waits on a thread for its answer is
    a tick box that is briefly wrong, and this one is only ever pressed on
    purpose.
    """
    env = dict(os.environ,
               CV_LINK=STARTUP_LNK,
               # pythonw where there is one, so logging in does not also open a
               # console window behind the panel. voice_lib picks it by the same
               # rule for the engine and for the Desktop icon; one rule, one
               # place, even though the name says it is private.
               CV_EXE=voice_lib._python(),
               CV_ARGS=f'"{os.path.join(voice_lib.ROOT, "panel.py")}"',
               CV_DIR=voice_lib.ROOT,
               CV_TEXT="Abby, and what she is saying -- the claude-voice panel",
               CV_ICON=os.path.join(ICON_DIR, "abby.ico"))
    try:
        os.makedirs(STARTUP_DIR, exist_ok=True)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", SHORTCUT_PS],
                       capture_output=True, timeout=30, env=env,
                       creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


def drop_startup_shortcut():
    try:
        os.remove(STARTUP_LNK)
    except OSError:
        pass


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def named(label, project, width=34):
    """Which folder, then which conversation in it.

    Two sessions in different repos can carry near enough the same title, and
    the title on its own then says nothing about which of them is speaking.
    """
    label = one_line(label, width)
    project = one_line(project, 22)
    if project and project.lower() not in label.lower():
        return f"{project} · {label}" if label else project
    return label


def voice_labels(voices):
    """Display name -> voice id, keeping the catalogue's order.

    Two voices can share a display name across cultures, so a repeated name
    carries its id; a unique one does not need to.
    """
    times = {}
    for v in voices:
        times[v["name"]] = times.get(v["name"], 0) + 1
    out = {}
    for v in voices:
        label = v["name"] if times[v["name"]] == 1 else f"{v['name']} ({v['id']})"
        out[label] = v["id"]
    return out


class Panel:
    def __init__(self, root, port):
        self.root = root
        self.port = port
        self.inbox = queue.Queue()
        self.stopping = threading.Event()
        self.voice_ids = {}          # what the dropdown shows -> voice id
        self.voice_names = {}        # voice id -> its display name
        self.session_vars = {}       # transcript path -> its checkbox
        self.drawn = {}              # last drawn content, to skip pointless redraws
        self.pending = {}            # clicked, not yet confirmed by a poll
        self.width = 0
        self.volume_send = None      # a drag waiting to be told to the engine
        self.echoing = False         # drawing the slider, rather than being dragged
        # Fitting the picture settles the layout, and settling the layout can
        # deliver another resize event -- which would arrive in the middle of
        # this one. Doing that to any depth is a hang, so it is done once.
        self.drawing_art = False
        self.icons = Icons()
        self.art_pics = Icons(ART_DIR)     # the big picture, in its own folder
        # One transparent pixel, held for as long as the window is. The two
        # coloured buttons wear it so that their size can be given in pixels
        # rather than in characters of their font -- see _match_switches. Tk
        # silently draws nothing for an image nobody keeps a reference to.
        self.blank = tk.PhotoImage(width=1, height=1)
        self.dim = []                # the labels that are grey in either theme
        self.links = []              # and the ones that are clickable
        saved = voice_lib.load_state()
        self.face_size = _sane_size(saved.get("panelFace", FACE))
        self.on_top = tk.BooleanVar(value=bool(saved.get("panelTopmost", True)))
        self.dark = tk.BooleanVar(value=bool(saved.get("panelDark", False)))
        # Load an engine and turn the voice on as the window opens, because
        # most of the times it is opened at all, it is opened to be spoken to.
        # Off unless asked for: three and a half gigabytes is not something to
        # take without being asked.
        self.at_open = tk.BooleanVar(value=bool(saved.get("panelAutostart", False)))
        # Not acted on here. Whether an engine is already up is the whole
        # difference between loading one and doing nothing, and nothing knows
        # that until the first poll answers -- so it waits for that answer.
        self.opening = bool(self.at_open.get())
        self.auto_update = tk.BooleanVar(value=bool(saved.get("updateCheck", False)))
        # How much audio to have in hand before the first word. Here rather
        # than only in the CLI because the machine it matters on is the one
        # somebody is already fighting, and a dropdown is quicker to reach
        # for than a terminal while the voice is stuttering at you.
        self.playback = tk.StringVar(value=str(saved.get("playback", "instant")))
        # Read off the filesystem rather than out of the config, and read again
        # every time the dialog opens -- see starts_with_windows.
        self.at_login = tk.BooleanVar(value=starts_with_windows())
        # Answers from the update thread, drained on the Tk timer like the
        # engine's. Nothing below touches a widget from another thread.
        self.update_inbox = queue.Queue()
        self.update_busy = False
        # Something the user's last press said that the files cannot: how a
        # pull went. It outranks the idle 'up to date' until they act again.
        self.update_msg = None
        # An update was taken in this window, so this window is now the one
        # thing running the old code.
        self.update_reopen = False
        # The update button lives in the settings dialog, so most of the time
        # there is no button at all -- and the answers it waits for arrive when
        # they arrive. What it *would* say is kept here, so a dialog opened
        # later opens saying it. See say_on_button.
        self.update_btn = None
        self.update_saying = "check now"
        # The settings dialog, while it is open. One at a time, like the typer.
        self.settings = None
        # The typing box and the box inside it, while it is open. One at a
        # time: a second copy would be two boxes with one queue behind them.
        self.typer = None
        self.typed = None
        self.native_theme = ttk.Style().theme_use()
        # Asked once, and only after there is a Tk to ask. Everything the
        # transport row draws hangs off this.
        families = tkfont.families()
        self.icons_ok = ICON_FAMILY in families
        # Two glyphs come out of that icon set now, not one, so the name says
        # the font rather than the cog.
        self.mdl2_ok = MDL2_FAMILY in families
        self.tips = {}               # button key -> its Tip, so the words can change
        self._build()
        self.apply_theme()
        self.show_saved_voice()
        threading.Thread(target=self._poll_loop, name="poll", daemon=True).start()
        self.tick = self.root.after(DRAIN_MS, self._drain)

    # -- laying it out -----------------------------------------------------
    def _build(self):
        root = self.root
        root.title(APP_NAME)
        self.wear_icon()
        root.wm_attributes("-topmost", self.on_top.get())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Configure>", self._rewrap)

        head = ttk.Frame(root)
        head.pack(fill="x", padx=8, pady=(8, 0))

        # A strip along the very top, the way an ordinary window has one, and
        # it did grow a Settings -- which is now the only thing in it. Nothing
        # in this strip is about what is being said; it is where the window
        # keeps the things that are about itself, and everything that used to be
        # spread along the row is one click behind that button now, with room to
        # say what it does.
        #
        # A row of its own rather than a corner of the row below, and that is
        # not tidiness. Sharing a row meant the tick boxes and the line being
        # spoken were competing for the same pixels, and in a narrow window the
        # line lost: "engine not running" came out as "engine not ru". Tried
        # both ways round and stacked; a row of its own is the only arrangement
        # where neither has to give. It costs about twenty pixels of height.
        #
        # Top right, where a window keeps this sort of button. The left is now
        # genuinely empty, and stays empty: a File menu is the only thing that
        # would ever go there and this window has no files.
        self.strip = ttk.Frame(head)
        self.strip.pack(fill="x")
        # A plain themed button, not one of the coloured pair: those two say a
        # state as well as an action, and this one only ever opens a window.
        cog, cog_style, cog_width = self._cog()
        self.settings_btn = ttk.Button(self.strip, text=cog, style=cog_style,
                                       width=cog_width, command=self.open_settings)
        self.settings_btn.pack(side="right")
        self.tips["settings"] = Tip(self.settings_btn, "settings", self.dark.get)

        top = ttk.Frame(head)
        top.pack(fill="x", pady=(2, 0))
        who = self.who = ttk.Frame(top)
        who.pack(side="left", padx=(0, 10))
        self.face = tk.Canvas(who, width=self.face_size, height=self.face_size,
                              highlightthickness=0,
                              cursor="hand2",
                              background=ttk.Style().lookup("TFrame", "background"))
        self.face.pack()
        self.face.bind("<Button-1>", self.flip_voice)
        # Directly under the portrait, in place of the name it used to print
        # there -- the dropdown says the name anyway, and this is where you
        # already click to change voice, so it is where the control belongs.
        # The size box beside it is the size of the picture above them both,
        # which is not something a box on its own in a corner ever said.
        picks = ttk.Frame(who)
        picks.pack(pady=(3, 0))
        self.voice_box = ttk.Combobox(picks, state="readonly", width=12, font=FONT_SMALL)
        self.voice_box.pack(side="left")
        self.voice_box.bind("<<ComboboxSelected>>", self.pick_voice)
        # Not read-only: pick one of the sizes or type your own.
        self.size_box = ttk.Combobox(picks, width=3, font=FONT_SMALL, values=FACE_SIZES)
        self.size_box.set(str(self.face_size))
        self.size_box.pack(side="left", padx=(4, 0))
        for event in ("<<ComboboxSelected>>", "<Return>", "<FocusOut>"):
            self.size_box.bind(event, self.pick_size)
        # The two switches that are about the window rather than the voice, in
        # the corner the header was not using. They were down in the button row
        # and they set its width: four buttons and two tick boxes did not fit
        # in anything under 466 pixels, and the row is the only thing in this
        # window with an opinion about how narrow it can be.
        #
        said = ttk.Frame(top)
        said.pack(side="left", fill="x", expand=True)
        # Who is talking goes above what they said: you read down to the words
        # already knowing whose they are, rather than back up to find out.
        self.whose = ttk.Label(said, text="", font=FONT_SMALL, foreground=GREY, anchor="w")
        self.whose.pack(fill="x")
        self.dim.append(self.whose)
        self.now = ttk.Label(said, text="…", font=FONT_BOLD, justify="left",
                             wraplength=270, anchor="w")
        self.now.pack(fill="x", pady=(1, 0))

        bar = ttk.Frame(head)
        bar.pack(fill="x", pady=(6, 0))
        # The engine first, then the voice, then the line: the row reads from
        # the biggest thing to the smallest. Loading a model is a minute and
        # three gigabytes; turning the voice off is instant and costs nothing;
        # skipping a line is about the sentence in the air right now.
        #
        # It replaces the old "start engine" button, which only ever appeared
        # when there was no engine -- so the window could start one and never
        # stop one, and getting the memory back meant a terminal. This says
        # both, in the place the first one used to be, and its colour says
        # which of the two it is about to do.
        # _chip owns the font as well as the glyph, since the two go together
        # and the fallback needs a different size from the chip itself.
        self.engine_btn = self._switch(bar, "engine", self.toggle_engine)
        chip, chip_font = self._chip()
        self.engine_btn.configure(text=chip, font=chip_font)
        self.engine_btn.pack(side="left", padx=(0, 6), fill="y")
        self.tips["engine"] = Tip(self.engine_btn, self._engine_says, self.dark.get)
        self.paint_engine(None)

        # The master switch second: it is the one reached for most, and the two
        # beside it are about the line being spoken, not about the voice.
        self.power = self._switch(bar, "stop", self.toggle_voice)
        self.power.pack(side="left", padx=(0, 6), fill="y")
        self.tips["power"] = Tip(self.power, self._power_says, self.dark.get)
        self.paint_power(None)       # until the first poll says otherwise
        # Neither switch is in here: they are tk.Buttons and take 'state' as an
        # option rather than as a method, so they are enabled by hand below.
        self.transport = []
        # The queue is the whole difference between these two, so the labels
        # say so rather than leaving it to be discovered: 'skip line' gives up
        # on this sentence and goes straight to the next, 'skip all' throws
        # away everything waiting as well. Nothing here is called stop, because
        # neither of them pauses anything -- there is no coming back to it.
        # Skipping one line is the small, everyday one, so it is nearest.
        for key, route, says in (("skip", "/skip", "skip this line"),
                                 ("skip_all", "/stop", "skip everything waiting")):
            b = ttk.Button(bar, text=self._glyph(key), style="Icon.TButton",
                           width=3 if self.icons_ok else 10,
                           command=lambda r=route: self.act(r))
            b.pack(side="left", padx=(0, 4))
            self.tips[key] = Tip(b, says, self.dark.get)
            self.transport.append(b)
            if key == "skip":
                self.skip_btn = b        # the one the coloured pair copy
        # Their size is not set here. _match_switches has to let Tk settle the
        # layout to measure this button, and settling it half way through
        # building the window delivers a resize event to a window that does not
        # have all its widgets yet. apply_theme calls it, immediately after
        # this returns and again on every theme switch, which it needs anyway.

        # And the volume, on the end of the same row. It had a row of its own
        # because three buttons and two tick boxes filled this one and a slider
        # squeezed in beside them would have been a few pixels long -- too short
        # to aim at, which is the one thing a slider must be. The tick boxes
        # went into the settings dialog, so the room is there now, and a row of
        # window is worth more than a label saying "volume" next to the only
        # slider there is.
        #
        # It takes what the row has spare, which is what it did in its own row.
        # VOLUME_WIDE is only the least it will accept, and the reason the row
        # still is not what sets how narrow the window can be.
        #
        # fill="both", not "x", so it is the height of the buttons rather than a
        # thin groove floating in the middle of their row -- clam draws the
        # trough to whatever height it is given. It costs the row nothing: the
        # slider still only *asks* for 16, so the buttons go on setting how tall
        # the row is. The native theme in light mode draws its own thin track
        # and a thumb of a fixed size whatever height it is handed, so there the
        # slider stays slim; nothing in ttk will talk it out of that, and the
        # two themes already disagree about the size of the buttons.
        self.volume = ttk.Scale(bar, from_=0, to=100, orient="horizontal",
                                length=VOLUME_WIDE, command=self.slide_volume)
        self.volume.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.transport.append(self.volume)
        # Both things the label and the percentage used to say, in the place
        # this window already puts that sort of thing. keep=True because
        # pressing a slider is the start of using it rather than the end of
        # wondering what it is, and while it is being dragged this is the only
        # thing saying where the handle has got to.
        self.tips["volume"] = Tip(self.volume, self._volume_says, self.dark.get,
                                  keep=True)

        ttk.Separator(root).pack(fill="x", padx=8, pady=(8, 0))

        # The bottom of the window is packed from the bottom up, so that the
        # part which grows -- the history -- gives up room to the parts which
        # must always be visible, rather than shouldering them off the edge.
        # The packer hands out space in the order things were packed, so the
        # credit going first is also what keeps it on screen in a small window.
        # The last two rows are one thought: who she is, and who made the thing.
        # Nothing here is operated -- the controls all live at the top now --
        # so it sits below the working part of the window and stays there.
        # The version, in the corner. Nobody needs it while working and
        # everybody needs it when writing a bug report, so it goes as far out of
        # the way as there is: the last row, hard right, in the small grey.
        # Packed before the credit because the bottom of the window is filled
        # upwards -- first one down is the lowest.
        #
        # It is read off disk. The panel never checks for anything and never
        # touches the network; if an update is known here, it is because the
        # user asked for a check somewhere else.
        stamp = ttk.Frame(root)
        stamp.pack(side="bottom", fill="x", padx=8, pady=(0, 6))

        self.version = ttk.Label(stamp, text="", font=FONT_SMALL, cursor="hand2")
        self.version.pack(side="right")
        self.version.bind("<Button-1>", lambda _event: webbrowser.open(self.new_url))

        # The tick box and the button that used to sit here have gone into the
        # settings dialog, where there is room for each of them to say what it
        # is for. What is left in this row is the *report*: whether there is
        # something to take, what is in it, and which version this is. None of
        # it is operated, and all of it has to be legible with no dialog open.

        # Whatever came of it, in a few words. The whole account goes to
        # logs\panel.log, which is where a window with one line to spare should
        # put the other twenty.
        #
        # anchor west, or it centres itself in whatever room is left and drifts
        # rightwards until it is touching the version. The padding on the right
        # is the gap it keeps from it when the text is too long and is cut.
        self.update_note = ttk.Label(stamp, text="", font=FONT_SMALL, anchor="w")
        self.update_note.pack(side="left", padx=(8, 10), fill="x", expand=True)
        self.dim.append(self.update_note)

        # A version number is not a reason to take it. This appears beside the
        # button when there is something to read, and opens the changelog --
        # whose newest section is at the top, so the link needs no anchor.
        self.new_url = update_check.CHANGELOG_URL
        self.whats_new = self.link(stamp, "what's new", self.new_url)
        self.whats_new.bind("<Button-1>", lambda _event: webbrowser.open(self.new_url))

        self.version_seen = None
        self.show_version()

        credit = ttk.Frame(root)
        credit.pack(side="bottom", fill="x", padx=8, pady=(2, 2))
        made = ttk.Label(credit, text="created by ", font=FONT_SMALL, foreground=GREY)
        made.pack(side="left")
        self.link(credit, AUTHOR, AUTHOR_URL).pack(side="left")
        dot = ttk.Label(credit, text="  ·  ", font=FONT_SMALL, foreground=GREY)
        dot.pack(side="left")
        self.link(credit, "GitHub repo", REPO_URL).pack(side="left")
        # The engine's state has no row of its own any more, and it belongs
        # with the small print rather than beside the buttons.
        self.status = ttk.Label(credit, text="", font=FONT_SMALL, foreground=GREY)
        self.status.pack(side="right")
        self.dim += [made, dot, self.status]


        # Her, right above the credit -- the picture is what she looks like,
        # not a readout of anything, so it belongs at this end of the window.
        # No expand: the height is worked out in draw_art and set here, which
        # leaves the spare room to the history rather than splitting it.
        self.art = tk.Canvas(root, highlightthickness=0, borderwidth=0, height=1)
        self.art.pack(side="bottom", fill="x")

        ttk.Separator(root).pack(side="bottom", fill="x", padx=8, pady=(8, 4))
        self.sessions = ttk.Frame(root)
        self.sessions.pack(side="bottom", fill="x", padx=8)
        self._section(root, "sessions — ticked means heard").pack_configure(side="bottom")

        # The heading, and beside it the one way to put something into the
        # queue by hand. It sits on this row rather than up with the transport
        # buttons because those three are all about the line being spoken --
        # this one adds a line of your own to the ones waiting behind it.
        asked = ttk.Frame(root)
        asked.pack(fill="x")
        self.queue_head = self._section(asked, "queued")
        self.queue_head.pack_configure(side="left", expand=True)
        # A plus, because that is what a button which adds one to a list looks
        # like everywhere else. It said "read custom text" before, which was
        # accurate and took a sixth of the width of the window to say -- the
        # hover text says the same thing and takes none of it.
        self.say_btn = ttk.Button(asked, text=self._glyph("add"),
                                  width=3 if self.icons_ok else 17,
                                  style="Add.TButton" if self.icons_ok else "Small.TButton",
                                  command=self.open_typer)
        self.say_btn.pack(side="right", padx=(0, 8), pady=(6, 0))
        self.tips["add"] = Tip(self.say_btn,
                               "type a line of your own — it joins the queue,\n"
                               "filed under “manual input”", self.dark.get)
        # Disabled with the rest when there is no engine: a box you can type
        # into but nothing can read from is worse than no box at all.
        self.transport.append(self.say_btn)
        self.queue_list = self._rows(root, height=3)

        # Three rows is what it insists on; it takes any spare height going.
        self.hist_head = self._section(root, "history — click to replay")
        self.hist_list = self._rows(root, height=3, expand=True)
        self.hist_list.bind("<Button-1>", self.replay)

    def show_version(self, force=False):
        """What version this is, and what the button is for at the moment.

        The check's answer lives in one small file, so this watches that file's
        timestamp rather than re-reading it twice a second -- and it changes
        perhaps once a week.
        """
        try:
            when = os.path.getmtime(update_check.CACHE_PATH)
        except OSError:
            when = 0
        if when == self.version_seen and not force:
            return
        self.version_seen = when

        dark = bool(self.dark.get())
        newer = update_check.available()
        if newer:
            self.new_url = newer.get("changelog") or update_check.CHANGELOG_URL
        # The version wears the news. The button that used to announce it is
        # behind the settings cog now, so with no dialog open this label is the
        # only thing in the window that can say there is something to take --
        # and it was already the clickable way to the changelog, so the link
        # colour is what it should turn to say it. Grey the rest of the time.
        self.version.configure(text=f"v{update_check.shown_version()}",
                               foreground=(LINK_DARK if dark else LINK) if newer
                               else (DARK["dim"] if dark else GREY))
        # Mid-errand the button is saying what it is doing, and this is only a
        # timer noticing a file; it does not get to argue with that.
        if self.update_busy:
            return
        if self.update_reopen:
            # The engine was put back by the update itself. This window was
            # not, and cannot be: it is the process it was started as. So the
            # one useful thing left for the button is to start its replacement.
            self.say_on_button("reopen panel")
            self.whats_new.pack_forget()
        elif newer:
            self.say_on_button(f"update to {newer['version']}")
            # Packed only now, and before the note so the row reads left to
            # right: what it would do, what is in it, how it went. Nobody
            # should have to decide from a version number alone.
            # winfo_manager, not winfo_ismapped: the question is whether it is
            # packed, and an unmapped window's children are all "not mapped"
            # whatever they are packed into.
            if not self.whats_new.winfo_manager():
                self.whats_new.pack(side="left", padx=(8, 0), before=self.update_note)
        else:
            self.say_on_button("check now")
            self.whats_new.pack_forget()

        # The rest of the row, which would otherwise be a stretch of nothing:
        # whether it is current and when that was last true. A message left
        # over from something the user just pressed outranks it, because it
        # says something this cannot work out from a file.
        note = self.update_msg or ("" if newer else update_check.last_look())
        self.update_note.configure(text=one_line(update_check.plain(note), 30))

    def _section(self, parent, title):
        label = ttk.Label(parent, text=title, font=FONT_SMALL, foreground=GREY)
        label.pack(fill="x", padx=8, pady=(8, 2), anchor="w")
        self.dim.append(label)
        return label

    def _glyph(self, key):
        """The icon, or the words for anyone whose Windows has lost the font."""
        icon, words = GLYPH[key]
        return icon if self.icons_ok else words

    def _icon_font(self, bump=0):
        return (ICON_FAMILY, 12 + bump) if self.icons_ok else FONT

    def _chip(self):
        """The chip on the engine switch, and the font that draws it.

        Twelve point, where the cog that used to sit here was thirteen. The cog
        needed the extra point because Segoe MDL2 draws it small inside its own
        box, and at twelve it read as the runt of a row of four; the chip is
        drawn nearly to the edges of that box, so the same bump made it heavier
        than the square and the arrows beside it. Rendered all three and looked.

        Falls back through the icon font's cog to the word "engine", which is
        what this button wore before either glyph existed -- and that one does
        still want the extra point.
        """
        if self.mdl2_ok:
            return MDL2_CHIP, (MDL2_FAMILY, 12)
        return self._glyph("engine"), self._icon_font(1)

    def _cog(self):
        """The settings cog: its text, its style and how wide to ask for.

        Three ways down rather than two, because this one is a themed button
        and its font comes from a style rather than from the widget. The style
        is set in apply_theme, since ttk keeps styles per theme and a switch
        would otherwise drop the font and leave a boxed emoji behind.
        """
        if self.mdl2_ok:
            return MDL2_GEAR, "Gear.TButton", 3
        if self.icons_ok:
            return self._glyph("settings"), "Icon.TButton", 3
        return GLYPH["settings"][1], "Small.TButton", 10

    def _switch(self, parent, key, command):
        """A button that can actually be green.

        The old plain Tk button rather than ttk's, for the same reason the tick
        boxes are: the native Windows theme draws a real Windows button and
        ignores the colour you ask it for, so a ttk one would be green in dark
        and grey in light. This one is drawn by Tk and does as it is told, in
        both. Flat and borderless because a 3D grey frame around a coloured
        face is the one thing that makes it look broken.

        White text on all three faces, so the icon does not change weight as
        the colour under it changes.

        The size is not set here: _match_switches measures it off the themed
        buttons once the row exists. The width below is what the fallback
        words need when there is no icon font to measure against.
        """
        return tk.Button(parent, text=self._glyph(key), command=command,
                         width=3 if self.icons_ok else 10,
                         font=self._icon_font(), pady=3,
                         relief="flat", borderwidth=0,
                         highlightthickness=0, takefocus=0,
                         foreground="#ffffff", activeforeground="#ffffff",
                         disabledforeground="#dcdcdc")

    def _match_switches(self):
        """Give the two coloured switches the size of the themed buttons.

        Asking both kinds for three characters does not make them the same
        shape, and the difference is not small: measured here, the themed skip
        button wanted 37 by 29 and the coloured pair 25 by 35 and 31 by 35 --
        narrower and taller, and not even the same width as each other. Most
        of that is the theme's own border and padding, which is not a number
        we are told, so it is measured rather than guessed, and measured again
        after every theme switch because the two themes draw it differently.

        The transparent pixel is what makes it possible at all: a Tk button
        showing an image takes its width and height in screen pixels, where
        one showing only text takes them in characters of its font. Its own
        padding goes to zero for the same reason -- the box is now the size
        asked for, not that size plus a margin.

        Nothing to do in the fallback where the icon font is missing: those
        buttons wear words, and words already agree on their character width.
        """
        if not self.icons_ok:
            return
        self.root.update_idletasks()      # measure the theme that is on now
        wide = self.skip_btn.winfo_reqwidth()
        tall = self.skip_btn.winfo_reqheight()
        for button in (self.engine_btn, self.power):
            button.configure(image=self.blank, compound="center",
                             width=wide, height=tall, padx=0, pady=0)

    def link(self, parent, text, url):
        """A word you can click. Tk has no such widget, but it is only a label
        with a hand over it and something to do when it is pressed."""
        label = ttk.Label(parent, text=text, font=FONT_LINK, cursor="hand2")
        label.bind("<Button-1>", lambda _event: webbrowser.open(url))
        self.links.append(label)
        return label

    def check(self, parent, **kw):
        """A tick box, the old plain Tk one rather than ttk's.

        ttk's is drawn by the theme, and clam -- the only theme that takes
        colour instructions at all -- draws its ticked state as a black cross,
        which in a list headed "ticked means heard" reads as exactly the
        opposite of what it means. The classic widget draws a real tick and
        takes its colours directly.
        """
        box = tk.Checkbutton(parent, font=FONT_SMALL, anchor="w", takefocus=0,
                             borderwidth=0, highlightthickness=0, **kw)
        self._paint_check(box)
        return box

    def _paint_check(self, box):
        dark = bool(self.dark.get())
        back = DARK["bg"] if dark else "SystemButtonFace"
        fore = DARK["fg"] if dark else "SystemButtonText"
        box.configure(background=back, activebackground=back,
                      foreground=fore, activeforeground=fore,
                      selectcolor=DARK["field"] if dark else "SystemWindow")

    def apply_theme(self):
        """Light is the native Windows look; dark is clam, recoloured."""
        dark = bool(self.dark.get())
        style = ttk.Style()
        style.theme_use("clam" if dark else self.native_theme)
        if dark:
            style.configure(".", background=DARK["bg"], foreground=DARK["fg"],
                            fieldbackground=DARK["field"], bordercolor=DARK["line"],
                            lightcolor=DARK["bg"], darkcolor=DARK["bg"],
                            troughcolor=DARK["field"], arrowcolor=DARK["fg"],
                            insertcolor=DARK["fg"], focuscolor=DARK["sel"])
            # Buttons and the dropdown need saying twice: clam has maps of its
            # own for hover and press, they are lighter than the text, and a
            # button nobody can read while pointing at it is worse than a plain
            # one. So every state gets a colour, explicitly.
            style.configure("TButton", background=DARK["line"], foreground=DARK["fg"],
                            bordercolor=DARK["line"], lightcolor=DARK["line"],
                            darkcolor=DARK["line"])
            style.map("TButton",
                      background=[("pressed", DARK["sel"]), ("active", DARK["sel"]),
                                  ("disabled", DARK["bg"])],
                      foreground=[("disabled", DARK["dim"]), ("active", "#ffffff")],
                      lightcolor=[("active", DARK["sel"]), ("pressed", DARK["sel"])],
                      darkcolor=[("active", DARK["sel"]), ("pressed", DARK["sel"])])
            # The slider's handle is drawn with the widget's own background,
            # which in this theme is the window's -- a handle the same colour
            # as everything behind it, sitting in a groove nobody can see. So
            # the groove is the darker field colour and the handle is lighter
            # than both, which is the only reason it reads as a handle.
            style.configure("Horizontal.TScale", background=DARK["dim"],
                            troughcolor=DARK["field"], bordercolor=DARK["line"],
                            lightcolor=DARK["dim"], darkcolor=DARK["dim"])
            style.map("Horizontal.TScale",
                      background=[("active", "#ffffff"), ("disabled", DARK["line"])],
                      lightcolor=[("active", "#ffffff"), ("disabled", DARK["line"])],
                      darkcolor=[("active", "#ffffff"), ("disabled", DARK["line"])])
            style.configure("TCombobox", selectbackground=DARK["sel"],
                            selectforeground=DARK["fg"], arrowcolor=DARK["fg"])
            style.map("TCombobox",
                      fieldbackground=[("readonly", DARK["field"])],
                      foreground=[("readonly", DARK["fg"])],
                      background=[("active", DARK["line"]), ("pressed", DARK["line"])],
                      arrowcolor=[("active", DARK["fg"])])
        # The footer's own button: the colours of any other, at the size of the
        # small print around it. Set here rather than once at startup because
        # ttk keeps styles per theme, so a switch would otherwise lose it.
        style.configure("Small.TButton", font=FONT_SMALL, padding=(6, 1))
        # The two skip buttons and the plus. Same story as every other style
        # here: ttk keeps them per theme, so a switch would otherwise lose the
        # font and drop the row back to boxed emoji.
        if self.icons_ok:
            style.configure("Icon.TButton", font=(ICON_FAMILY, 12), padding=(2, 1))
            style.configure("Add.TButton", font=(ICON_FAMILY, 11), padding=(2, 0))
        # The settings cog, which is drawn out of the other font and so cannot
        # share a style with them.
        if self.mdl2_ok:
            style.configure("Gear.TButton", font=(MDL2_FAMILY, 12), padding=(2, 1))

        # The list a combobox drops is a plain Tk listbox, coloured the old way.
        popup = ((DARK["field"], DARK["fg"], DARK["sel"], DARK["fg"]) if dark else
                 ("SystemWindow", "SystemWindowText", "SystemHighlight", "SystemHighlightText"))
        for option, value in zip(("background", "foreground",
                                  "selectBackground", "selectForeground"), popup):
            self.root.option_add(f"*TCombobox*Listbox.{option}", value)

        # Per theme, so it has to be said again on every switch.
        style.configure("Voice.Treeview", rowheight=28, font=FONT_SMALL,
                        **({"background": DARK["field"], "fieldbackground": DARK["field"],
                            "foreground": DARK["fg"]} if dark else {}))
        style.map("Voice.Treeview", background=[("selected", DARK["sel"] if dark else "#cfe3ff")],
                  foreground=[("selected", DARK["fg"] if dark else "#000000")])

        back = DARK["bg"] if dark else style.lookup("TFrame", "background")
        self.root.configure(background=back)
        self.face.configure(background=back)
        # What shows either side of her when the window is a shape no scaling
        # of the picture quite fills.
        self.art.configure(background=back)
        for label in self.dim:
            label.configure(foreground=DARK["dim"] if dark else GREY)
        for label in self.links:
            label.configure(foreground=LINK_DARK if dark else LINK)
        # Not in either list: its colour says whether there is an update, so it
        # picks its own and has to be asked again when the theme changes.
        self.show_version(force=True)
        for widget in _descendants(self.root):
            if isinstance(widget, tk.Checkbutton):     # ttk's are not these
                self._paint_check(widget)
        # Their ttk parts follow the styles set above on their own, and the tick
        # boxes were caught by the walk just above -- a Toplevel is a child of
        # root, so _descendants reaches into both of these windows. What is
        # left is what takes no styling: the Toplevels' own backgrounds, and
        # the box you type into.
        self._paint_typer()
        self._paint_settings()
        self.drawn.pop("face", None)              # redraw it on the new background
        # The themed buttons are not the same size in both themes, so the two
        # coloured ones are measured against them here rather than once at
        # startup -- and this is also the first moment after _build at which
        # measuring is safe at all. See the note where the row is built.
        self._match_switches()

    def toggle_auto_update(self):
        """The one switch here that lets this project use the network at all.

        Ticking it looks straight away as well as weekly: somebody who has just
        asked for update checks should see one happen, not wait a week to find
        out whether it works.
        """
        on = bool(self.auto_update.get())
        voice_lib.patch_state(updateCheck=on)
        if on:
            self.look_for_update()
        else:
            # The box itself now says nothing will be contacted, so the line
            # beside it goes back to reporting the last look there was.
            self.update_msg = None
            self.show_version(force=True)

    def say_on_button(self, text=None, enabled=None):
        """Tell the update button something, if there is one on screen to tell.

        It lives in the settings dialog now, and a check started from there
        answers whenever the network answers -- which may well be after the
        dialog has been closed. So what it would have said is remembered here
        instead, and a dialog opened afterwards builds its button already
        saying it. The typer has a comment about the same class of bug: a
        widget that has gone is not an error to be caught later, it is a fact
        the code around it has to be able to hold.
        """
        if text is not None:
            self.update_saying = text
        if self.update_btn is None:
            return
        try:
            if text is not None:
                self.update_btn.configure(text=text)
            if enabled is not None:
                self.update_btn.state(["!disabled" if enabled else "disabled"])
        except tk.TclError:
            self.update_btn = None      # it went while we were talking to it

    def press_update(self):
        """One button, three stages: find out, take it, then stand aside."""
        if self.update_reopen:
            return self.reopen_panel()
        newer = update_check.available()
        if newer:
            self.take_update(newer)
        else:
            self.look_for_update()

    def reopen_panel(self):
        """Close this window and open one running the code it just pulled.

        Closing first, so the geometry is written before the new one reads it;
        the spawn survives this process ending, being detached, exactly as the
        one that opened this window did.
        """
        self.close()
        voice_lib.start_panel()

    def look_for_update(self):
        self.update_busy = True
        self.update_msg = None
        self.say_on_button("checking…", enabled=False)
        self.update_note.configure(text="")

        # urllib on the Tk thread would freeze the window for as long as the
        # request takes, which offline is the whole timeout.
        def go():
            self.update_inbox.put(("checked", update_check.look_now()))

        threading.Thread(target=go, name="update-check", daemon=True).start()

    def take_update(self, newer):
        self.update_busy = True
        self.update_msg = None
        self.say_on_button("updating…", enabled=False)
        self.update_note.configure(text="pulling, then restarting the engine…")

        def go():
            # apply_update talks to git and then waits on a model load, so it
            # belongs on a thread of its own twice over. Its account goes to
            # panel.log, since this row has space for one line of it.
            spoken = []
            ok = update_check.apply_update(say=spoken.append)
            for line in spoken:
                print(line)
            self.update_inbox.put(("applied", ok, spoken, newer))

        threading.Thread(target=go, name="update-apply", daemon=True).start()

    def drain_update(self):
        """Answers from those threads, applied to widgets on the Tk thread."""
        while True:
            try:
                item = self.update_inbox.get_nowait()
            except queue.Empty:
                return

            if item[0] == "checked":
                # Nothing worth keeping: what a check found is exactly what the
                # line below reads back off the file, in fewer words.
                self.update_msg = None
            else:
                _, ok, spoken, _newer = item
                if ok:
                    self.update_reopen = True     # the button says the rest
                    self.update_msg = f"updated to {update_check.shown_version()}"
                    if update_check.local().get("needsSetup"):
                        self.update_msg += " -- run setup.ps1 too"
                else:
                    # The first line is the refusal itself; the rest is detail,
                    # and detail is what the log is for.
                    self.update_msg = spoken[0] if spoken else "nothing was pulled"

            self.update_busy = False
            self.say_on_button(enabled=True)
            self.show_version(force=True)

    def toggle_dark(self):
        self.apply_theme()
        voice_lib.patch_state(panelDark=bool(self.dark.get()))

    @staticmethod
    def _rows(parent, height, expand=False):
        """A list of utterances: picture, time, project, session, words.

        A Treeview rather than a Listbox because a Listbox holds text and
        nothing else -- no row of it can carry a picture -- and because real
        columns line up without padding every line to a fixed width.

        The project is its own column rather than a "project - session" prefix,
        which is what the session list underneath does. Down there each row is
        the full width of the window and there is room to run them together; up
        here there is not, and a joined pair truncates into porridge. A column
        of its own is also the thing you can read straight down when what you
        actually want to know is which repo has been doing the talking.
        """
        tree = ttk.Treeview(parent, height=height, style="Voice.Treeview",
                            columns=("when", "project", "session", "text"),
                            show="tree", selectmode="browse")
        # Treeview adds its own indent in front of a row's picture, so this has
        # to be wider than the picture or the time beside it is sat on.
        tree.column("#0", width=ROW_ICON + 22, minwidth=ROW_ICON + 22, stretch=False)
        tree.column("when", width=44, minwidth=44, stretch=False, anchor="w")
        tree.column("project", width=92, minwidth=54, stretch=False)
        tree.column("session", width=96, minwidth=54, stretch=False)
        tree.column("text", width=180, minwidth=80, stretch=True)
        tree.pack(fill="both" if expand else "x", expand=expand, padx=8)
        return tree

    def _rewrap(self, _event=None):
        """Keep the now-playing line wrapping to the window, not past it.

        Measured from the portrait *column*, not the portrait. The dropdowns
        underneath are wider than a small picture, and taking the picture's
        width instead let the spoken line run off the right-hand edge -- by
        exactly the difference, which is why it only showed at some sizes.

        """
        width = self.root.winfo_width()
        column = max(self.face_size, self.who.winfo_width())
        if width and (width, column) != self.width:
            self.width = (width, column)
            self.now.configure(wraplength=max(140, width - column - 40))
        self.draw_art()

    def draw_art(self, force=False):
        """Abby along the bottom, as big as the window can spare.

        Scaled, never stretched: she is drawn at one of the sizes Tk can
        actually reach and centred, so a window of an awkward width clips a
        little scenery off her sides rather than squashing her.
        """
        if self.drawing_art:
            return
        wide = max(120, self.root.winfo_width()) // ART_STEP * ART_STEP
        # Room nothing else is entitled to. The packer hands out space in the
        # order things were packed and she comes before the lists, so simply
        # asking for a share of the window took it from them -- at which point
        # the queue and the history were not in the window at all. What is
        # spare, by contrast, is what is left once every row has what it asked
        # for, and taking all of it costs the lists nothing they insisted on.
        spare = max(0, self.root.winfo_height() - self._least_height())
        tall = spare // ART_STEP * ART_STEP
        if (wide, tall) == self.drawn.get("art") and not force:
            return
        self.drawn["art"] = (wide, tall)
        self.drawing_art = True
        try:
            picture, w, h = self.art_pics.fit(ART, wide, tall)
            # Exactly her height: the canvas hugs the picture, so there is no
            # band of background above her and nothing of her below the edge.
            self.art.configure(height=h or 1)
            self.art.delete("all")
            if picture is not None:
                self.art.create_image(self.root.winfo_width() // 2, 0,
                                      image=picture, anchor="n")
            self.hold_the_floor()
        finally:
            self.drawing_art = False

    # -- talking to the engine ---------------------------------------------
    def _poll_loop(self):
        while not self.stopping.is_set():
            try:
                data = voice_lib.post(self.port, "/state", timeout=3)
            except Exception:
                data = None                      # nobody home; the UI says so
            self.inbox.put(data)
            self.stopping.wait(POLL_SECONDS)

    def _drain(self):
        latest, got = None, False
        while True:                              # only the newest one matters
            try:
                latest, got = self.inbox.get_nowait(), True
            except queue.Empty:
                break
        if got:
            try:
                self.render(latest)
                self.show_version()
                if self.opening:
                    self.open_up(latest)
            except tk.TclError:
                return                           # the window is going away
        try:
            self.drain_update()
        except tk.TclError:
            return
        self.tick = self.root.after(DRAIN_MS, self._drain)

    def act(self, route, payload=None):
        """Every button is one POST, and never from the Tk thread."""
        def go():
            try:
                voice_lib.post(self.port, route, payload or {}, timeout=15)
            except Exception:
                pass

        threading.Thread(target=go, name="act", daemon=True).start()

    def toggle_top(self):
        on = bool(self.on_top.get())
        self.root.wm_attributes("-topmost", on)
        # And whatever this window has opened, which floats with it: the tick
        # that turns this off now lives in one of them, and a dialog that stays
        # glued over everything right after you unticked "on top" reads as the
        # tick not having worked.
        for win in (self.settings, self.typer):
            if win is not None and win.winfo_exists():
                win.wm_attributes("-topmost", on)
        voice_lib.patch_state(panelTopmost=on)

    def toggle_at_open(self):
        """Only ever about the next time. Nothing starts or stops here."""
        voice_lib.patch_state(panelAutostart=bool(self.at_open.get()))

    def open_up(self, st):
        """Load an engine and turn the voice on, once, as the window opens.

        On the first answer from the poll rather than at startup, because that
        answer is the first moment anything here knows whether there is an
        engine already. Starting a second one would do no harm -- start_server
        checks the port before it launches anything -- but the window would
        spend a minute saying it was loading a model that was already loaded,
        which is the one thing it exists to be right about.
        """
        self.opening = False
        if st is None:
            self.start_engine()
        if not (st or {}).get("enabled"):
            self.turn_voice_on()

    def turn_voice_on(self):
        """The master switch, written rather than posted.

        This can happen while there is no engine to post to -- the one being
        started a line earlier is still loading -- and the config is what that
        engine reads as it comes up. An engine already running hears it just as
        quickly, because the watcher re-reads that file every sweep. It is the
        same write the engine's own /enabled does, which is why the two agree.
        """
        voice_lib.patch_state(enabled=True)
        self.hold("enabled")
        self.drawn["enabled"] = True
        self.paint_power(True)

    def _engine_says(self):
        if self.drawn.get("engine_loading"):
            return "loading the model — 40 to 60 seconds"
        if self.drawn.get("engine_up"):
            return "turn the engine OFF — hands back about 3.5 GB"
        return "turn the engine ON — the first load takes 40 to 60 seconds"

    def _volume_says(self):
        """How loud, as a whole percent.

        Off drawn rather than off the widget: while it is being dragged the
        handle is ahead of the engine by design, and drawn is what the drag
        writes and what the POST afterwards reads -- so the three of them
        cannot disagree. Asking the widget instead would have made this depend
        on Tk having updated the value before it called the callback.
        """
        level = self.drawn.get("volume")
        if not self.drawn.get("engine_up") or level is None:
            return ""                # nothing to be loud, and no level to report
        return f"volume {level}%"

    def _power_says(self):
        if not self.drawn.get("engine_up"):
            return "no engine to speak with"
        return "stop speaking" if self.drawn.get("enabled") else "start speaking"

    def paint_engine(self, up):
        """The engine switch. None is "loading", which is neither yet.

        The cog does not change -- a cog is the engine either way, and what is
        being said is whether it is running, which is what the colour is for.
        Only the words under the pointer change.
        """
        face, hover = POWER_DEAD if up is None else (POWER_ON if up else POWER_OFF)
        if not self.icons_ok and up is not None:
            self.engine_btn.configure(text="engine off" if up else "engine on")
        self.engine_btn.configure(background=face, activebackground=hover)

    def toggle_engine(self):
        """Load the model, or give the memory back."""
        if self.drawn.get("engine_up"):
            return self.unload_engine()
        self.start_engine()

    def unload_engine(self):
        """Stop speaking, then shut the engine down.

        It turns the voice off as well, and has to. The hook starts an engine
        again the moment Claude says anything, so unloading with the voice
        still on frees three gigabytes for about five seconds. `voice kill`
        has the same trap and the same answer -- off first, then kill.

        The off is written straight to the config rather than posted, because
        these two have to happen in that order and the second one is what stops
        the engine answering. The panel already writes its own settings there,
        and the watcher re-reads that file every sweep.
        """
        voice_lib.patch_state(enabled=False)
        self.hold("enabled")
        self.drawn["enabled"] = False
        self.drawn["engine_up"] = False
        self.paint_power(False)
        self.paint_engine(False)               # answer the click at once
        self.status.configure(text="engine: unloading…")
        self.act("/quit")

    def start_engine(self):
        self.hold("engine", 25)
        self.drawn["engine_up"] = True         # so a second press unloads it
        self.now.configure(text="starting the engine — the first model load "
                                "takes 40 to 60 seconds")
        # Neither colour is true yet, and it cannot be pressed again until the
        # poll says which. A minute is long enough that saying nothing about it
        # would read as the click having been missed, so the line above the
        # buttons says it and the hover text says it.
        self.drawn["engine_loading"] = True
        self.paint_engine(None)
        self.engine_btn.configure(state="disabled")
        threading.Thread(target=lambda: voice_lib.start_server(voice_lib.load_state()),
                         name="start", daemon=True).start()

    # A click has to survive the next poll, which was already in flight and
    # still says otherwise. Without this the tick you just made flickers back.
    def hold(self, key, seconds=2.5):
        self.pending[key] = time.monotonic() + seconds

    def held(self, key):
        until = self.pending.get(key)
        if until is None:
            return False
        if time.monotonic() > until:
            del self.pending[key]
            return False
        return True

    # -- drawing what came back --------------------------------------------
    def render(self, st):
        if st is None:
            return self.render_down()
        for b in self.transport:
            b.state(["!disabled"])
        self.power.configure(state="normal")
        self.engine_btn.configure(state="normal")
        # It answered, so the wait is over whatever the clock said. The hold is
        # only there to stop the window flashing "engine not running" during a
        # load; a reply is better evidence than a countdown.
        self.pending.pop("engine", None)
        self.drawn["engine_loading"] = False
        self.paint_engine(True)

        # Before the portrait: it puts the voice's name under it, and that
        # comes from the catalogue this reads.
        self.render_voices(st)

        cur = st.get("current")
        if cur:
            self.now.configure(text=f"▶ “{one_line(cur['text'], 200)}”")
            self.whose.configure(text=named(cur.get("session"), cur.get("project"), 40)
                                 or "spoken by request")
        else:
            self.now.configure(text="— nothing playing")
            self.whose.configure(text="")
        # Idle, the face is whoever would speak next, which is worth seeing.
        speaker = (cur or {}).get("voice") or st.get("voice")
        if self.held("voice"):
            # Just clicked. The next poll or two still report the old voice --
            # drawing those would flick the portrait back and forth.
            speaker = self.drawn.get("voice") or speaker
        self.draw_face(speaker)

        self.drawn["engine_up"] = True
        rows = st.get("queue") or []
        self.queue_head.configure(text=f"queued ({len(rows)})")
        self.fill(self.queue_list, "queue", rows)
        self.fill(self.hist_list, "history", st.get("history") or [])

        self.render_sessions(st.get("sessions") or [], st.get("voice"))
        self.render_voices(st)

        on = bool(st.get("enabled"))
        if self.held("enabled"):
            on = self.drawn.get("enabled", on)     # just clicked; let it settle
        else:
            self.drawn["enabled"] = on
        self.paint_power(on)

        # The engine owns the level: it is the process making the noise, and
        # the mixer slider being moved is that process's own. So this only ever
        # echoes what came back -- except while it is being dragged, when
        # echoing would drag it out from under the hand doing the dragging.
        # An engine too old to know about volume says nothing about it, and
        # inventing a level for it would drag the slider back to full every
        # time it was moved -- which is exactly how this read from the outside.
        if "volume" in st and not self.held("volume"):
            level = _percent(st.get("volume"))
            if level != self.drawn.get("volume"):
                self.drawn["volume"] = level
                self.echoing = True
                try:
                    self.volume.set(level)
                finally:
                    self.echoing = False

        if st.get("error"):
            note = f"engine failed: {one_line(st['error'], 28)}"
        elif not st.get("ready"):
            note = "engine: loading the model…"
        elif st.get("speaking"):
            note = "engine: speaking"
        else:
            note = "engine: ready"
        if not st.get("enabled"):
            note += " · voice off"
        elif not st.get("watching"):
            note += " · not watching"
        self.status.configure(text=note)

    def render_down(self):
        if self.held("engine"):
            return                                # we just asked it to start
        self.now.configure(text="engine not running")
        self.whose.configure(text="nothing is being spoken anywhere")
        for b in self.transport:
            b.state(["disabled"])
        self.power.configure(state="disabled")
        self.paint_power(None)
        # The one thing still worth pressing in this row: it offers to load one.
        self.engine_btn.configure(state="normal")
        self.drawn["engine_up"] = False
        self.drawn["engine_loading"] = False
        self.paint_engine(False)
        self.status.configure(text="engine: down")

    def draw_face(self, voice_id):
        """The portrait, or a coloured initial for a voice with no picture.
        The name is not drawn here: the dropdown underneath says it."""
        if self.drawn.get("face") == voice_id:
            return
        self.drawn["face"] = voice_id
        self.face.delete("all")
        picture, size = self.icons.at(voice_id, self.face_size)
        # The canvas takes the size actually drawn, so nothing is cropped and
        # nothing sits in a box of empty space.
        self.face.configure(width=size, height=size)
        if picture is not None:
            return self.face.create_image(size // 2, size // 2, image=picture)
        colour = colour_for(voice_id)
        self.face.create_oval(3, 3, size - 3, size - 3, fill=colour, outline=colour)
        self.face.create_text(size // 2, size // 2 + 1, text=initial(voice_id),
                              fill="white", font=("Segoe UI", max(8, size // 3), "bold"))

    def wear_icon(self, voice_id=None):
        """The window's own icon -- title bar, taskbar, alt-tab.

        Abby's portrait, and it stays Abby's: the icon is how you find this
        window among thirty others, so it should not move about when the voice
        changes. Tk 8.6 takes the PNG directly, so it is the same picture the
        panel is already holding.
        """
        voice_id = voice_id or SHIPPED[0]
        icon = self.icons.get(voice_id, 48) or self.icons.get(voice_id, 96)
        if icon is None:
            return
        try:
            self.root.iconphoto(False, icon)
        except tk.TclError:
            pass

    def hold_the_floor(self):
        """Keep the window at least as tall as its contents need.

        Called whenever the layout changes shape -- a new portrait size, a
        different number of sessions -- because both change what the minimum is.
        """
        # Everything else, plus room for a picture of her worth having. The
        # lists give up their spare height to her happily, but not their own
        # rows, so without this the window could be dragged to a size where she
        # had nowhere to be.
        least = self._least_height() + ART_FLOOR
        if least == self.drawn.get("floor"):
            return
        self.drawn["floor"] = least
        self.root.minsize(MIN_WIDE, least)
        # Only once the window is really on screen. Before that winfo_width is
        # the packer's guess rather than the size asked for, and writing it
        # back as a geometry pinned the window to that guess -- which is how a
        # 440-wide default opened at 360 with the tick boxes cut off.
        if self.root.winfo_ismapped() and self.root.winfo_height() < least:
            self.root.geometry(f"{self.root.winfo_width()}x{least}")

    def _least_height(self):
        """The height at which nothing has to be squeezed out.

        Everything below the history is packed from the bottom and is given its
        room first, so whatever is left over is the history's -- and with a big
        portrait above it there was nothing left over at all: at 256px the
        history vanished from the window entirely. Rather than guess a number,
        ask every packed row how much it wants and add it up.
        """
        self.root.update_idletasks()
        # The window's own requested height, which is the packer's answer to
        # the same question -- and unlike adding up the rows, it counts the
        # padding between them.
        #
        # Less the picture, which is not part of any minimum: her height is a
        # share of the window's, so counting it would raise the floor every
        # time the window grew, and the floor would then stop it shrinking back.
        return self.root.winfo_reqheight() - int(self.art.cget("height"))

    def pick_size(self, _event=None):
        """Resize the portrait. Typed sizes land on the nearest Tk can draw."""
        want = _sane_size(self.size_box.get(), self.face_size)
        self.face_size = want
        self.drawn.pop("face", None)
        self.draw_face(self.drawn.get("voice"))
        drawn = int(self.face.cget("width"))
        self.size_box.set(str(drawn))             # say what was actually done
        self.size_box.selection_clear()
        voice_lib.patch_state(panelFace=want)
        self.width = 0
        self._rewrap()
        self.hold_the_floor()

    def paint_power(self, on):
        """The switch, wearing whether the voice is working.

        None is "no engine, so no idea" -- and that has no label of its own,
        because the last one it had is still the truthful thing to press.
        """
        if on is None:
            face, hover = POWER_DEAD
        else:
            face, hover = POWER_ON if on else POWER_OFF
            # A square while it is speaking, a triangle while it is not: the
            # icon is what pressing it does, exactly as the words were.
            self.power.configure(text=self._glyph("stop" if on else "play"))
        self.power.configure(background=face, activebackground=hover)

    def toggle_voice(self):
        """The switch itself: speaking, or not speaking anywhere."""
        on = not self.drawn.get("enabled", True)
        self.hold("enabled")
        self.drawn["enabled"] = on
        self.paint_power(on)
        self.act("/enabled", {"on": on})

    def slide_volume(self, value):
        """Dragging is continuous; the engine only needs where you stopped.

        The hover text follows the handle immediately, because that is what
        makes the thing feel connected to anything at all -- and since the
        number beside the slider went to save a row, it is now the only place
        the level is written down. The POST does not follow: a request per pixel
        would be a hundred round trips for one drag, so it waits until the
        handle has been still for a moment.
        """
        if self.echoing:
            return                   # we moved it ourselves, to match the engine
        level = int(round(float(value)))
        self.drawn["volume"] = level
        self.tips["volume"].now()
        self.hold("volume", 4)       # long enough to cover the send as well
        if self.volume_send is not None:
            self.root.after_cancel(self.volume_send)
        self.volume_send = self.root.after(SEND_MS, self.send_volume)

    def send_volume(self):
        self.volume_send = None
        self.hold("volume")          # the poll already in flight says the old level
        self.act("/volume", {"level": self.drawn.get("volume", 100) / 100.0})

    def flip_voice(self, _event=None):
        """Click the portrait to swap between the two shipped voices."""
        current = (self.drawn.get("voice") or "").lower()
        want = SHIPPED[1] if current == SHIPPED[0] else SHIPPED[0]
        self.hold("voice")
        self.drawn["voice"] = want
        self.draw_face(want)                      # answer the click at once
        shown = next((k for k, vid in self.voice_ids.items() if vid == want), None)
        if shown:
            self.voice_box.set(shown)
        self.act("/set-voice", {"voice": want})

    def fill(self, tree, key, jobs):
        # "direct" is /voice say and the like, which belongs to no session and
        # no folder -- an empty project column reads better there than the word
        # repeated beside itself.
        rows = [(j["id"], j.get("voice"), j.get("when") or "",
                 one_line(j.get("project") or "", 22),
                 one_line(j.get("session") or "direct", 22), one_line(j["text"], 200))
                for j in jobs]
        if self.drawn.get(key) == rows:
            return                                # redrawing loses the selection
        self.drawn[key] = rows
        tree.delete(*tree.get_children())
        for jid, voice, when, project, session, text in rows:
            picture = self.icons.get(voice, ROW_ICON)
            # The utterance id is the row's own name, so a click needs no lookup
            # table to say which line was clicked.
            tree.insert("", "end", iid=str(jid), values=(when, project, session, text),
                        image=picture or "",
                        text="" if picture else f" {initial(voice)}",
                        tags=() if picture else (voice or "?",))
            if picture is None:
                tree.tag_configure(voice or "?", foreground=colour_for(voice))

    def render_sessions(self, sessions, voice=None):
        sessions = sessions[:MAX_SESSIONS]
        # The picture is the voice this session would be read in, so a change
        # of voice has to redraw these as well as the lists.
        shape = [voice] + [s["path"] for s in sessions]
        if shape != self.drawn.get("sessions"):
            self.drawn["sessions"] = shape
            for child in self.sessions.winfo_children():
                child.destroy()
            self.session_vars = {}
            picture = self.icons.get(voice, ROW_ICON)
            for s in sessions:
                var = tk.BooleanVar(value=not s["muted"])
                self.session_vars[s["path"]] = var
                self.check(
                    self.sessions, variable=var, image=picture or "", compound="left",
                    text=f" {named(s.get('label'), s.get('project'))}  {s['when']}",
                    command=lambda p=s["path"], v=var: self.mute(p, v),
                ).pack(fill="x", anchor="w")
            self.hold_the_floor()          # one more session, one more row
            return
        for s in sessions:                        # same sessions, maybe new answers
            var = self.session_vars.get(s["path"])
            if var is not None and not self.held(s["path"]) and var.get() == s["muted"]:
                var.set(not s["muted"])

    def show_saved_voice(self):
        """Draw whoever would speak, before anything has been asked.

        The panel is a view over the engine, and with no engine there was
        nothing to view: it opened on an empty circle and an empty dropdown,
        which says less than the truth. Which voice is set is in the config,
        the catalogue is a directory listing and the pictures are files -- all
        three are knowable without anybody being running, so there is no reason
        to sit there blank until an engine turns up.

        Read once, at startup. The engine's own answer replaces it on the first
        poll that gets one, and that is the one that can say who is *speaking*
        rather than who would.
        """
        state = voice_lib.load_state()
        try:
            voices = [{"id": v["id"], "name": v["name"], "culture": v["culture"],
                       "sex": v["sex"]} for v in voice_lib.catalog(state)]
        except OSError:
            voices = []            # no voices folder; the dropdown stays empty
        self.render_voices({"voices": voices, "voice": state.get("voice")})
        self.draw_face(state.get("voice"))

    def render_voices(self, st):
        voices = st.get("voices") or []
        ids = [v["id"] for v in voices]
        if ids != self.drawn.get("voices"):
            self.drawn["voices"] = ids
            self.voice_ids = voice_labels(voices)
            self.voice_names = {v["id"]: v["name"] for v in voices}
            self.voice_box.configure(values=list(self.voice_ids))
            self.drawn.pop("face", None)          # the name under it may have changed
        current = st.get("voice")
        if current != self.drawn.get("voice") and not self.held("voice"):
            self.drawn["voice"] = current
            shown = next((k for k, vid in self.voice_ids.items() if vid == current), current)
            self.voice_box.set(shown or "")

    # -- what the clicks mean ----------------------------------------------
    def replay(self, event):
        row = self.hist_list.identify_row(event.y)   # "" below the last one
        if row.isdigit():
            self.act("/replay-id", {"id": int(row)})

    def mute(self, path, var):
        self.hold(path)
        self.act("/mute-session", {"path": path, "muted": not var.get()})

    def pick_voice(self, _event=None):
        vid = self.voice_ids.get(self.voice_box.get())
        if vid:
            self.hold("voice")
            self.drawn["voice"] = vid
            self.act("/set-voice", {"voice": vid})

    # -- saying something of your own --------------------------------------
    def open_typer(self):
        """A box to type a line into, and have it read out.

        The panel owns no state, and this comes as close as anything here to
        breaking that: the words are not read back off the engine, they are
        yours. But it is still one POST -- the engine keeps them exactly as it
        keeps a line a hook sent it, and this window forgets them at once.

        It queues rather than barging in. 'voice say' means say this now, and
        cuts off whatever is playing; typing a line here is asking for it to be
        read, which is no reason to throw away what is already waiting.
        """
        if self.typer is not None and self.typer.winfo_exists():
            self.typer.deiconify()               # already open; just come back
            self.typer.lift()
            self.typed.focus_set()
            return

        win = self.typer = tk.Toplevel(self.root)
        win.title("read custom text")
        # Owned by the panel, and floating with it: the panel is on top of
        # everything by default, and a window it opened that it then covered
        # over would be a strange thing to have been given.
        win.transient(self.root)
        win.wm_attributes("-topmost", self.on_top.get())
        win.protocol("WM_DELETE_WINDOW", self.close_typer)
        win.bind("<Escape>", lambda _event: self.close_typer())

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.typed = tk.Text(frame, height=6, width=42, wrap="word", font=FONT,
                             borderwidth=1, relief="solid", highlightthickness=0)
        self.typed.pack(fill="both", expand=True)
        self.typed.bind("<Control-Return>", self.speak_typed)

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(8, 0))
        # Enter puts in a line break, because this takes as many lines as you
        # like -- so the key that sends it has to be written down somewhere.
        hint = ttk.Label(row, text="ctrl+enter reads it", font=FONT_SMALL,
                         foreground=GREY)
        hint.pack(side="left")
        self.dim.append(hint)
        ttk.Button(row, text="read it", width=9,
                   command=self.speak_typed).pack(side="right")
        ttk.Button(row, text="cancel", width=9,
                   command=self.close_typer).pack(side="right", padx=(0, 6))

        self._paint_typer()
        over(win, self.root)
        self.typed.focus_set()

    def _paint_typer(self):
        """The typing box in whichever theme the panel is wearing.

        A tk.Text takes no ttk styles: its background, its words and its caret
        are three separate colours, and left alone in dark the caret is black
        on black -- a box that looks like it is not taking anything you type.
        """
        if self.typer is None or not self.typer.winfo_exists():
            return
        dark = bool(self.dark.get())
        self.typer.configure(background=DARK["bg"] if dark else
                             ttk.Style().lookup("TFrame", "background"))
        self.typed.configure(
            background=DARK["field"] if dark else "SystemWindow",
            foreground=DARK["fg"] if dark else "SystemWindowText",
            insertbackground=DARK["fg"] if dark else "SystemWindowText",
            selectbackground=DARK["sel"] if dark else "SystemHighlight",
            selectforeground=DARK["fg"] if dark else "SystemHighlightText")

    def speak_typed(self, _event=None):
        words = self.typed.get("1.0", "end").strip()
        if words:
            self.act("/speak", {"text": words, "project": TYPED_PROJECT,
                                "queue": True})
            self.close_typer()
        # Or ctrl+enter sends the line and puts a line break in the box behind
        # it, which is only visible if it failed to send.
        return "break"

    def close_typer(self):
        if self.typer is not None:
            try:
                self.typer.destroy()
            except tk.TclError:
                pass
            # Its grey label is in the list the theme repaints, and that list
            # outlives the window; a dead widget left in it breaks the next
            # switch, which then leaves half the panel in the wrong colours.
            self.dim = [w for w in self.dim if w.winfo_exists()]
        self.typer = self.typed = None

    # -- the window's own settings -----------------------------------------
    def open_settings(self):
        """Everything that is about the window rather than about the voice.

        Modelled on the typer, and for its reasons: one at a time, because two
        copies would be two sets of tick boxes over one config file; Esc closes
        it; it opens over the panel rather than wherever the window manager
        would have put it; and it floats with the panel, which is usually on top
        of everything.

        Every row is a tick and a sentence under it saying what ticking it
        means, and that is the point of having a dialog at all. A strip along
        the top could fit the words "auto start" and nothing else, which left
        the difference between that and starting with Windows to be guessed --
        and the hover text standing in for the explanation could only be found
        by resting on a thing you had already decided not to press.
        """
        if self.settings is not None and self.settings.winfo_exists():
            self.settings.deiconify()               # already open; come back to it
            self.settings.lift()
            return

        win = self.settings = tk.Toplevel(self.root)
        win.title("settings")
        win.transient(self.root)
        win.wm_attributes("-topmost", self.on_top.get())
        win.protocol("WM_DELETE_WINDOW", self.close_settings)
        win.bind("<Escape>", lambda _event: self.close_settings())
        # Fixed: the descriptions wrap at a pixel width, and a dialog that can
        # be dragged narrower than that width would cut them off rather than
        # rewrap -- which is a resize handler this window does not need to grow.
        win.resizable(False, False)

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=4, pady=(0, 12))

        self._section(frame, "when it opens")
        # Read again, here, rather than trusted from startup: the folder is the
        # setting, and anything may have happened to it since -- an installer,
        # a tidied Startup folder, the same tick in another copy of this window.
        self.at_login.set(starts_with_windows())
        self._setting(frame, "auto start", self.at_open, self.toggle_at_open,
                      "When this window opens it loads the engine and turns the "
                      "voice on, so the window is the only thing you touch. Not "
                      "about Windows starting — that is the tick below.")
        self._setting(frame, "start with Windows", self.at_login,
                      self.toggle_at_login,
                      "Opens this window when you log in. Tick both and it just "
                      "talks: the window comes up by itself, loads the engine "
                      "and turns the voice on.")

        self._section(frame, "the window")
        self._setting(frame, "dark", self.dark, self.toggle_dark,
                      "Dark colours instead of the ones Windows gave it.")
        self._setting(frame, "on top", self.on_top, self.toggle_top,
                      "Keeps the window above the others, so what is being said "
                      "does not go behind what you are reading.")

        self._section(frame, "how it speaks")
        # Read again here rather than trusted from startup: the CLI writes
        # this too, and a dropdown showing the wrong mode is worse than no
        # dropdown at all.
        self.playback.set(str(voice_lib.load_state().get("playback", "instant")))
        self._choice(frame, "start speaking", self.playback,
                     [m[0] for m in voice_lib.PLAYBACK_MODES],
                     self.pick_playback,
                     ["How much speech to have ready before she starts. Too "
                      "little and she breaks up when the machine is busy."]
                     + [f"• {name} — {says}"
                        for name, _lead, says in voice_lib.PLAYBACK_MODES])

        self._section(frame, "updates")
        row = self._setting(frame, "auto-check for updates", self.auto_update,
                            self.toggle_auto_update,
                            "Looks once a week for a newer claude-voice, and "
                            "once straight away when you tick it. The one thing "
                            "here that touches the network at all — untick it "
                            "and nothing in this program ever contacts anything.")
        # The button the footer used to carry, now in the section it is about
        # and indented under the words that explain it. It says whatever
        # update_saying says rather than "check now", because a check can have
        # been started and answered with no dialog on screen at all -- and one
        # still running leaves it disabled, exactly as it was when it left.
        self.update_btn = ttk.Button(row, text=self.update_saying, width=16,
                                     style="Small.TButton", command=self.press_update)
        self.update_btn.pack(anchor="w", padx=(20, 0), pady=(5, 0))
        if self.update_busy:
            self.update_btn.state(["disabled"])

        self._paint_settings()
        over(win, self.root)

    def _setting(self, parent, text, variable, command, says):
        """One setting: the tick, and under it what ticking it would mean."""
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=(2, 6))
        self.check(row, text=text, variable=variable, command=command).pack(anchor="w")
        # Indented to the tick's words rather than to its box, so the sentence
        # reads as belonging to the label above it. wraplength is in pixels and
        # this window cannot be resized, so the one number does for good.
        note = ttk.Label(row, text=says, font=FONT_SMALL, foreground=GREY,
                         wraplength=300, justify="left")
        note.pack(anchor="w", padx=(20, 0))
        self.dim.append(note)
        return row

    def _choice(self, parent, text, variable, options, command, says):
        """One setting that is a pick rather than a yes: the label, the box,
        and under both what the choice is between.

        Read-only, because every value it can hold is in the list and a
        typed one would only ever be a typo the engine then has to guess at.

        `says` may be one sentence or a list of them, and a list is drawn a
        line each. Four modes described in a paragraph is four things to hold
        in your head at once; a list is four things to glance down.
        """
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=(2, 6))
        line = ttk.Frame(row)
        line.pack(fill="x")
        ttk.Label(line, text=text, font=FONT_SMALL).pack(side="left")
        box = ttk.Combobox(line, state="readonly", width=10, font=FONT_SMALL,
                           values=list(options), textvariable=variable)
        box.pack(side="left", padx=(8, 0))
        box.bind("<<ComboboxSelected>>", lambda _event: command())
        for i, line in enumerate([says] if isinstance(says, str) else says):
            note = ttk.Label(row, text=line, font=FONT_SMALL, foreground=GREY,
                             wraplength=300, justify="left")
            # Tight between the lines of one list, looser above the first,
            # so the block reads as one thing rather than five.
            note.pack(anchor="w", padx=(20, 0), pady=(3 if i == 0 else 1, 0))
            self.dim.append(note)
        return row

    def pick_playback(self):
        """Written straight to the config, and that is the whole of it.

        No route through the engine because there is nothing for it to do:
        it reads this per message rather than at startup, so the next line
        spoken already uses it and a restart is never needed.
        """
        voice_lib.patch_state(playback=self.playback.get())

    def _paint_settings(self):
        """The dialog's own background, which is all that takes no styling.

        Everything in it is either ttk, which follows the styles apply_theme
        sets, or a tick box, which the walk in apply_theme repaints -- a
        Toplevel is a child of root, so that walk reaches into this window
        without being told to. The Toplevel itself is the one widget nobody
        else is going to colour.
        """
        if self.settings is None or not self.settings.winfo_exists():
            return
        dark = bool(self.dark.get())
        self.settings.configure(background=DARK["bg"] if dark else
                                ttk.Style().lookup("TFrame", "background"))

    def toggle_at_login(self):
        """Ticked means the shortcut is in the Startup folder, and nothing else.

        So the box is read back off the folder rather than left where the click
        put it: a create that failed must not leave a tick claiming it worked,
        and neither must a delete. There is nothing else to write -- see
        starts_with_windows for why there is no setting behind this one.
        """
        if self.at_login.get():
            make_startup_shortcut()
        else:
            drop_startup_shortcut()
        self.at_login.set(starts_with_windows())

    def close_settings(self):
        if self.settings is not None:
            try:
                self.settings.destroy()
            except tk.TclError:
                pass
            # Its headings and descriptions are in the list the theme repaints,
            # and that list outlives this window. A dead widget left in it
            # breaks the next dark switch half way through, which leaves the
            # rest of the panel in the wrong colours. The typer learned this.
            self.dim = [w for w in self.dim if w.winfo_exists()]
        self.settings = None
        # And the update button went with it. Everything it was saying is on
        # update_saying, so the next dialog opens saying the same thing.
        self.update_btn = None

    def close(self):
        self.stopping.set()
        try:
            self.root.after_cancel(self.tick)     # else it fires into a dead window
        except Exception:
            pass
        try:
            geometry = self.root.geometry()
            if GEOMETRY.match(geometry):
                voice_lib.patch_state(panelGeometry=geometry)
        except Exception:
            pass
        self.root.destroy()


def over(win, parent):
    """Put a small window in the middle of the one that opened it.

    Tk otherwise puts it wherever the window manager likes, which on a second
    monitor is nowhere near the panel you just clicked. The size has to be
    asked for before it is mapped, or it is 1x1 and the sum is wrong.
    """
    win.update_idletasks()
    wide, high = win.winfo_reqwidth(), win.winfo_reqheight()
    x = parent.winfo_rootx() + (parent.winfo_width() - wide) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - high) // 3
    # Never off the top or the left, whatever shape the parent is.
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


def desktop(root):
    """Every monitor, not just the main one.

    Tk only knows about the primary screen: winfo_screenwidth is *that* one,
    and there is nothing in Tk that says a second monitor exists at all. A
    window on a monitor to the left of the main one therefore has a negative x
    -- perfectly ordinary, and indistinguishable from nonsense to anything
    checking against 0.

    That cost a real afternoon. The panel kept coming back on the middle of the
    main screen instead of where it was left, because the check below read a
    saved x of -428 as a monitor that had been unplugged and threw the position
    away. Windows knows the shape of the whole desktop, so ask it.

    Returns left, top, right, bottom. Falls back to the primary screen, which
    is what Tk would have said on its own.
    """
    try:
        metric = ctypes.windll.user32.GetSystemMetrics
        # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CY...
        left, top, wide, high = (metric(n) for n in (76, 77, 78, 79))
        if wide > 0 and high > 0:
            return left, top, left + wide, top + high
    except Exception:
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def place(root, geometry):
    """Reopen where it was left, unless that is no longer on any screen.

    A window restored onto a monitor that has since been unplugged is simply
    lost -- it is running, on top, and nowhere to be seen. Keep the size in
    that case and let Tk choose the corner.
    """
    match = GEOMETRY.match(geometry or "")
    if not match:
        return root.geometry(DEFAULT_GEOMETRY)
    size, x_sign, x, y_sign, y = match.groups()
    if x_sign is None:
        return root.geometry(size)
    # A '-' offset is measured from the right or bottom edge, so it is on
    # screen by construction; only the '+' form can point at a monitor that
    # is no longer there. The margins let a window that was left overlapping an
    # edge come back overlapping it, rather than being treated as lost.
    left, top, right, bottom = desktop(root)
    on_screen = (x_sign == "-" or left - 60 <= int(x) <= right - 80) and \
                (y_sign == "-" or top - 20 <= int(y) <= bottom - 60)
    root.geometry(geometry if on_screen else size)


def raise_open_panel():
    """Bring an open panel to the front. True if there was one to bring.

    Every way in -- the Desktop shortcut, `voice_cli.py panel`, the update's own
    restart -- just spawns this file, and nothing here ever asked whether a
    window was already up. So a second double click gave you a second identical
    window instead of the one you meant to look at.

    Keyed on the window rather than on a pidfile or a named mutex, because the
    window is the thing being duplicated. reopen_panel destroys this one before
    it spawns its replacement, so the replacement finds nothing to raise even
    though the old process is still winding down -- which a pid would have got
    wrong, and would have left an update unable to reopen its own panel.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32")
        # Window handles are pointer-sized. ctypes hands back a C int unless it
        # is told otherwise, and a truncated handle is a window that quietly
        # cannot be found -- so every signature is spelled out.
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

        hwnd = user32.FindWindowW(None, APP_NAME)
        if not hwnd:
            return False

        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)

        # SetForegroundWindow refuses a caller that is not already in front, and
        # a double click on the Desktop leaves Explorer holding that -- so on its
        # own it would raise nothing and the click would look ignored. Borrowing
        # the foreground thread's input queue for the length of the call is the
        # documented way to be allowed.
        here = ctypes.windll.kernel32.GetCurrentThreadId()
        front = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        lent = bool(front) and front != here and bool(
            user32.AttachThreadInput(front, here, True))
        try:
            user32.SetForegroundWindow(hwnd)
        finally:
            if lent:
                user32.AttachThreadInput(front, here, False)
        return True
    except (OSError, AttributeError, ValueError):
        # Never let raising a window stop a window from opening.
        return False


def main():
    state = voice_lib.load_state()
    ap = argparse.ArgumentParser(description="The claude-voice panel.")
    ap.add_argument("--port", type=int, default=state["port"])
    ap.add_argument("--force", action="store_true",
                    help="open a window even if one is already up")
    args = ap.parse_args()

    if not args.force and raise_open_panel():
        # A no-op under pythonw, where stdout is None; the point of the click was
        # the window, and that is now in front of them.
        print(f"{APP_NAME} was already open -- brought it to the front.")
        return

    root = tk.Tk()
    place(root, state.get("panelGeometry"))
    Panel(root, args.port)
    root.mainloop()


if __name__ == "__main__":
    main()
