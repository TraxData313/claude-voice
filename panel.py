"""
A small window that shows what the voice is doing, and lets you steer it.

    python panel.py            # or: python voice_cli.py panel

It floats on top -- there is a tick box to stop it doing that -- and is
deliberately plain:

    what is playing now, and which session it came from
    stop / play / skip
    what is queued behind it
    what has been said -- click a line to hear it again
    which sessions are heard and which are muted
    which voice is speaking

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
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_lib

POLL_SECONDS = 0.5
DRAIN_MS = 120
DEFAULT_GEOMETRY = "410x580"
# WxH, optionally offset: '+100+80', or '-8+0' meaning 8 in from the right, or
# '+-8+0' meaning 8 past the left edge. Tk writes all three.
GEOMETRY = re.compile(r"^(\d+x\d+)(?:([+-])(-?\d+)([+-])(-?\d+))?$")

FONT = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 8)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_LINK = ("Segoe UI", 8, "underline")
GREY = "#666666"
LINK = "#1a5fb4"
LINK_DARK = "#7aa7ff"

AUTHOR = "TraxData313"
AUTHOR_URL = "https://github.com/TraxData313"
REPO_URL = "https://github.com/TraxData313/claude-voice"

# Dark is a set of colours plus a change of ttk theme. The native Windows theme
# draws real Windows widgets and ignores most colour you ask it for; 'clam' is
# drawn by Tk itself and does as it is told. So light stays native, and dark
# switches theme -- which is also why every style has to be set again on the
# way in: ttk keeps its settings per theme, not per widget.
DARK = {"bg": "#24262b", "field": "#1c1e22", "fg": "#e4e6eb",
        "dim": "#9aa0a6", "sel": "#3a4a63", "line": "#3a3d44"}
ICON_DIR = os.path.join(voice_lib.ROOT, "docs", "icons")
FACE = 128                   # the portrait beside the line being spoken
FACE_SIZES = ("48", "64", "96", "128", "160", "192", "256")
FACE_MIN, FACE_MAX = 24, 320
ROW_ICON = 24                # and the small one on every row
# The two voices the repo ships. Clicking the portrait swaps between them.
SHIPPED = ("abby", "max")
# For the voices with no picture -- which is most of them. Picked from the id,
# so a voice keeps its colour between runs and between rows.
PALETTE = ["#2A9D8F", "#E9A13B", "#4C7FE0", "#8E6FD0", "#D96A82", "#4CA36A"]


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

    def __init__(self):
        self.loaded = {}
        self.on_disk = {}
        self.chose = {}          # which file each drawn size came from, and how

    def get(self, voice_id, size):
        key = (voice_id, size)
        if key not in self.loaded:
            path = os.path.join(ICON_DIR, f"{voice_id}-{size}.png")
            try:
                self.loaded[key] = tk.PhotoImage(file=path) if os.path.exists(path) else None
            except tk.TclError:
                self.loaded[key] = None            # unreadable png; the letter will do
        return self.loaded[key]

    def stored(self, voice_id):
        """Which sizes were drawn for this voice and committed as files."""
        if voice_id not in self.on_disk:
            found = []
            for name in os.listdir(ICON_DIR) if os.path.isdir(ICON_DIR) else []:
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


def one_line(text, width):
    """Collapse to a single line short enough to sit in a list."""
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 1].rstrip() + "…"


def _sane_size(value, fallback=FACE):
    try:
        return max(FACE_MIN, min(FACE_MAX, int(float(value))))
    except (TypeError, ValueError):
        return fallback


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
        self.icons = Icons()
        self.dim = []                # the labels that are grey in either theme
        self.links = []              # and the ones that are clickable
        saved = voice_lib.load_state()
        self.face_size = _sane_size(saved.get("panelFace", FACE))
        self.on_top = tk.BooleanVar(value=bool(saved.get("panelTopmost", True)))
        self.dark = tk.BooleanVar(value=bool(saved.get("panelDark", False)))
        self.native_theme = ttk.Style().theme_use()
        self._build()
        self.apply_theme()
        threading.Thread(target=self._poll_loop, name="poll", daemon=True).start()
        self.tick = self.root.after(DRAIN_MS, self._drain)

    # -- laying it out -----------------------------------------------------
    def _build(self):
        root = self.root
        root.title("claude-voice")
        self.wear_icon()
        root.wm_attributes("-topmost", self.on_top.get())
        root.minsize(330, 400)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Configure>", self._rewrap)

        head = ttk.Frame(root)
        head.pack(fill="x", padx=8, pady=(8, 0))
        top = ttk.Frame(head)
        top.pack(fill="x")
        who = ttk.Frame(top)
        who.pack(side="left", padx=(0, 10))
        self.face = tk.Canvas(who, width=self.face_size, height=self.face_size,
                              highlightthickness=0,
                              cursor="hand2",
                              background=ttk.Style().lookup("TFrame", "background"))
        self.face.pack()
        self.face_name = ttk.Label(who, text="", font=FONT_BOLD, anchor="center",
                                   cursor="hand2")
        self.face_name.pack(fill="x", pady=(2, 0))
        for spot in (self.face, self.face_name):
            spot.bind("<Button-1>", self.flip_voice)
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
        # The master switch first: it is the one reached for most, and the two
        # beside it are about the line being spoken, not about the voice.
        self.power = ttk.Button(bar, text="turn off", width=9, command=self.toggle_voice)
        self.power.pack(side="left", padx=(0, 6))
        self.transport = [self.power]
        for text, route in (("stop", "/stop"), ("skip", "/skip")):
            b = ttk.Button(bar, text=text, width=6, command=lambda r=route: self.act(r))
            b.pack(side="left", padx=(0, 4))
            self.transport.append(b)
        # Only ever shown when there is no engine to talk to.
        self.start_btn = ttk.Button(bar, text="start engine", command=self.start_engine)
        # Floating over everything is right while you are listening to a long
        # answer and wrong while you are reading something behind it, so it is
        # a switch rather than a decision, and it is remembered.
        self.check(bar, text="on top", variable=self.on_top,
                   command=self.toggle_top).pack(side="right")
        self.check(bar, text="dark", variable=self.dark,
                   command=self.toggle_dark).pack(side="right", padx=(0, 8))

        ttk.Separator(root).pack(fill="x", padx=8, pady=(8, 0))

        # The bottom of the window is packed from the bottom up, so that the
        # part which grows -- the history -- gives up room to the parts which
        # must always be visible, rather than shouldering them off the edge.
        # The packer hands out space in the order things were packed, so the
        # credit going first is also what keeps it on screen in a small window.
        credit = ttk.Frame(root)
        credit.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
        made = ttk.Label(credit, text="created by ", font=FONT_SMALL, foreground=GREY)
        made.pack(side="left")
        self.link(credit, AUTHOR, AUTHOR_URL).pack(side="left")
        dot = ttk.Label(credit, text="  ·  ", font=FONT_SMALL, foreground=GREY)
        dot.pack(side="left")
        self.link(credit, "GitHub repo", REPO_URL).pack(side="left")
        self.dim += [made, dot]

        foot = ttk.Frame(root)
        foot.pack(side="bottom", fill="x", padx=8, pady=6)
        ttk.Separator(root).pack(side="bottom", fill="x", padx=8, pady=(8, 0))
        self.sessions = ttk.Frame(root)
        self.sessions.pack(side="bottom", fill="x", padx=8)
        self._section(root, "sessions — ticked means heard").pack_configure(side="bottom")

        self.queue_head = self._section(root, "queued")
        self.queue_list = self._rows(root, height=3)

        self.hist_head = self._section(root, "history — click to replay")
        self.hist_list = self._rows(root, height=6, expand=True)
        self.hist_list.bind("<Button-1>", self.replay)
        name = ttk.Label(foot, text="voice", font=FONT_SMALL, foreground=GREY)
        name.pack(side="left")
        self.voice_box = ttk.Combobox(foot, state="readonly", width=17, font=FONT)
        self.voice_box.pack(side="left", padx=6)
        self.voice_box.bind("<<ComboboxSelected>>", self.pick_voice)
        self.status = ttk.Label(foot, text="", font=FONT_SMALL, foreground=GREY)
        self.status.pack(side="right")

        # Not read-only: pick one of the sizes or type your own.
        size = ttk.Label(foot, text="size", font=FONT_SMALL, foreground=GREY)
        size.pack(side="left", padx=(8, 0))
        self.size_box = ttk.Combobox(foot, width=4, font=FONT_SMALL, values=FACE_SIZES)
        self.size_box.set(str(self.face_size))
        self.size_box.pack(side="left", padx=4)
        for event in ("<<ComboboxSelected>>", "<Return>", "<FocusOut>"):
            self.size_box.bind(event, self.pick_size)
        self.dim += [name, size, self.status]

    def _section(self, parent, title):
        label = ttk.Label(parent, text=title, font=FONT_SMALL, foreground=GREY)
        label.pack(fill="x", padx=8, pady=(8, 2), anchor="w")
        self.dim.append(label)
        return label

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
            style.configure("TCombobox", selectbackground=DARK["sel"],
                            selectforeground=DARK["fg"], arrowcolor=DARK["fg"])
            style.map("TCombobox",
                      fieldbackground=[("readonly", DARK["field"])],
                      foreground=[("readonly", DARK["fg"])],
                      background=[("active", DARK["line"]), ("pressed", DARK["line"])],
                      arrowcolor=[("active", DARK["fg"])])
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
        for label in self.dim:
            label.configure(foreground=DARK["dim"] if dark else GREY)
        for label in self.links:
            label.configure(foreground=LINK_DARK if dark else LINK)
        for widget in _descendants(self.root):
            if isinstance(widget, tk.Checkbutton):     # ttk's are not these
                self._paint_check(widget)
        self.drawn.pop("face", None)              # redraw it on the new background

    def toggle_dark(self):
        self.apply_theme()
        voice_lib.patch_state(panelDark=bool(self.dark.get()))

    @staticmethod
    def _rows(parent, height, expand=False):
        """A list of utterances: picture, time, session, words.

        A Treeview rather than a Listbox because a Listbox holds text and
        nothing else -- no row of it can carry a picture -- and because real
        columns line up without padding every line to a fixed width.
        """
        tree = ttk.Treeview(parent, height=height, style="Voice.Treeview",
                            columns=("when", "session", "text"), show="tree",
                            selectmode="browse")
        # Treeview adds its own indent in front of a row's picture, so this has
        # to be wider than the picture or the time beside it is sat on.
        tree.column("#0", width=ROW_ICON + 22, minwidth=ROW_ICON + 22, stretch=False)
        tree.column("when", width=44, minwidth=44, stretch=False, anchor="w")
        tree.column("session", width=110, minwidth=60, stretch=False)
        tree.column("text", width=180, minwidth=80, stretch=True)
        tree.pack(fill="both" if expand else "x", expand=expand, padx=8)
        return tree

    def _rewrap(self, _event=None):
        """Keep the now-playing line wrapping to the window, not past it."""
        width = self.root.winfo_width()
        if width and width != self.width:
            self.width = width
            self.now.configure(wraplength=max(140, width - self.face_size - 34))

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
            except tk.TclError:
                return                           # the window is going away
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
        self.root.wm_attributes("-topmost", self.on_top.get())
        voice_lib.patch_state(panelTopmost=bool(self.on_top.get()))

    def start_engine(self):
        self.hold("engine", 25)
        self.now.configure(text="starting the engine — the first model load "
                                "takes 40 to 60 seconds")
        self.start_btn.pack_forget()
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
        self.start_btn.pack_forget()

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
        self.power.configure(text="turn off" if on else "turn on")

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
        # winfo_manager, not winfo_ismapped: a minimised window maps nothing,
        # and re-packing on every poll would fight the layout.
        if not self.start_btn.winfo_manager():
            self.start_btn.pack(side="left", padx=(6, 0))
        self.status.configure(text="engine: down")

    def draw_face(self, voice_id):
        """The portrait and the name under it, or a coloured initial for a
        voice with no picture."""
        if self.drawn.get("face") == voice_id:
            return
        self.drawn["face"] = voice_id
        self.face_name.configure(text=self.voice_names.get(voice_id, voice_id or ""))
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

    def toggle_voice(self):
        """The switch itself: speaking, or not speaking anywhere."""
        on = not self.drawn.get("enabled", True)
        self.hold("enabled")
        self.drawn["enabled"] = on
        self.power.configure(text="turn off" if on else "turn on")
        self.act("/enabled", {"on": on})

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
        rows = [(j["id"], j.get("voice"), j.get("when") or "",
                 one_line(j.get("session") or "direct", 22), one_line(j["text"], 200))
                for j in jobs]
        if self.drawn.get(key) == rows:
            return                                # redrawing loses the selection
        self.drawn[key] = rows
        tree.delete(*tree.get_children())
        for jid, voice, when, session, text in rows:
            picture = self.icons.get(voice, ROW_ICON)
            # The utterance id is the row's own name, so a click needs no lookup
            # table to say which line was clicked.
            tree.insert("", "end", iid=str(jid), values=(when, session, text),
                        image=picture or "",
                        text="" if picture else f" {initial(voice)}",
                        tags=() if picture else (voice or "?",))
            if picture is None:
                tree.tag_configure(voice or "?", foreground=colour_for(voice))

    def render_sessions(self, sessions, voice=None):
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
            return
        for s in sessions:                        # same sessions, maybe new answers
            var = self.session_vars.get(s["path"])
            if var is not None and not self.held(s["path"]) and var.get() == s["muted"]:
                var.set(not s["muted"])

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
    # is no longer there.
    on_screen = (x_sign == "-" or -60 <= int(x) <= root.winfo_screenwidth() - 80) and \
                (y_sign == "-" or -20 <= int(y) <= root.winfo_screenheight() - 60)
    root.geometry(geometry if on_screen else size)


def main():
    state = voice_lib.load_state()
    ap = argparse.ArgumentParser(description="The claude-voice panel.")
    ap.add_argument("--port", type=int, default=state["port"])
    args = ap.parse_args()

    root = tk.Tk()
    place(root, state.get("panelGeometry"))
    Panel(root, args.port)
    root.mainloop()


if __name__ == "__main__":
    main()
