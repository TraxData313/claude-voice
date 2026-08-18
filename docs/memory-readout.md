# The memory readout — built, removed, and how to put it back

Built in the 1.5.0 work and taken out again before release, at Toni's word: he did not want
the number. Nothing about it failed — every part of it worked and was measured — so this page
is the whole of it, kept so that adding it back is a paste rather than an evening.

**What it did.** The footer read `engine: ready · 11.3 GB` beside the engine's state, and
resting on that line said `8.0 GB claimed in RAM + 3.3 GB on the graphics card`. Live, so you
could watch it climb as a model loaded, and silent when there was nothing to say.

## What was learned, which is the part worth keeping

- **`nvidia-smi` cannot answer this.** `--query-compute-apps=pid,used_gpu_memory` lists our
  pid happily and then says `[N/A]` for the memory, on this card and every other consumer one:
  NVML will not break VRAM down per process while the driver is in WDDM mode, which is every
  Windows machine with a screen plugged into it. It does not error and it does not fail — it
  simply never says a number. Do not spend the evening again.
- **The performance counter Windows keeps *can*.** `\GPU Process Memory(pid_<pid>_*)\Dedicated
  Usage`, which is where Task Manager gets its own per-process GPU figure. Read through PDH by
  ctypes it costs 0.067 ms once warm, measured over four hundred calls — so there is no reason
  for the sampling thread the original plan called for.
- **`PDH_MORE_DATA` compares against a signed return.** `0x800007D2` read as a `c_long` is
  negative, so the two are equal only once one of them is masked. Unmasked, the wildcard
  expansion silently found nothing every time, and looked exactly like a machine with no such
  counter on it.
- **The instance name carries a LUID nothing can predict**, so the counter path is expanded
  from a wildcard rather than built. A process with nothing on the card has no instance at
  all, which is a real answer — the model is not loaded — and not a failure.
- **Committed RAM, not the working set, and it is much larger than the story says.** Measured
  on a warm engine holding the 1.7b model: working set 0.86 GB, then 0.35 GB twenty minutes
  later with nothing whatever having changed, while `PrivateUsage` sat at 8.03 GB and did not
  move. So the working set is the number that lies — and it is the one Task Manager shows.
  The "about 3.5 GB" the engine switch is described in terms of turned out to be the
  *graphics card* (3.28 GB measured), not the RAM. The two added together read 11.3 GB, which
  is the reason a person comparing the panel with Task Manager would think it was broken, and
  why the hover text said "claimed in RAM" rather than "in RAM".
- **Where it fits, measured.** The figure cost the credit row 42 pixels. At what window width
  each footer line stops fitting, with the number on the end: ready 345, speaking 364, ready
  and voice off 398, loading the model 421, a long engine failure 503. The window opens at 440
  and will not go below 348, so at the narrowest the figure is what gets cut — which is what
  putting it last was for. The long failure message already did not fit before any of this.

## Putting it back — the server

`speak_server.py` needs `import ctypes` in the import block and `from ctypes import wintypes`
beside the other `from` imports. Then this section, which sat between `set_volume` and
`class Job`:

```python
# --------------------------------------------------------------------------
# how much memory this is holding
# --------------------------------------------------------------------------
# The engine switch promises about three and a half gigabytes back, and until
# now nothing said whether that was true on this machine today. It is worth a
# live number rather than a remembered one, and cheap to have: this figure is
# flat except while a model is being loaded or handed back, which are exactly
# the two moments anybody looks at it -- so it behaves like a static number and
# still lets you watch it climb as the model comes in.


class _Counters(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS_EX, of which the last field is the one wanted.

    PrivateUsage is what this process has *committed*: what it has claimed from
    Windows and what goes away when it exits. The working set is a different
    question -- how much of that claim is actually in RAM this second -- and
    Windows trims it whenever it feels like it, which would read as a process
    handing memory back while it is still holding every byte.

    Measured on a warm engine holding the 1.7b model, because the difference is
    not academic: working set 0.86 GB, then 0.35 GB twenty minutes later with
    nothing having changed; committed 8.03 GB, unmoving. So the working set is
    the number that lies, and it is the one Task Manager shows -- which is worth
    knowing before somebody compares the two and reports a bug.

    One correction to the plan this came from, which said committed would be the
    "about 3.5 GB" the engine switch is described in terms of. It is not: 3.5 GB
    is what the model takes on the *graphics card*, measured at 3.28 GB here.
    The RAM claim is a good deal larger than that, and the two together are what
    the panel shows.
    """

    _fields_ = [("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


# Spelled out rather than left to ctypes' defaults, for the reason the panel
# spells its own out: a handle is pointer-sized, ctypes hands back a C int
# unless it is told otherwise, and the pseudo-handle meaning "this process" is
# all ones -- which truncated is a handle to nothing in particular.
try:
    _kernel32 = ctypes.WinDLL("kernel32")
    _psapi = ctypes.WinDLL("psapi")
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(_Counters), wintypes.DWORD]
    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
except (OSError, AttributeError):     # not Windows, or a Windows without psapi
    _psapi = None


def ram_used():
    """Bytes this process has committed, or None if Windows would not say.

    Cheap enough to ask per request -- one call, reading counters the kernel is
    keeping anyway -- so there is nothing here to cache and nothing to go stale.
    """
    if _psapi is None:
        return None
    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    try:
        ok = _psapi.GetProcessMemoryInfo(_kernel32.GetCurrentProcess(),
                                         ctypes.byref(counters), counters.cb)
    except OSError:
        return None
    return int(counters.PrivateUsage) if ok else None


# The card is asked through the performance counters Windows keeps, which is
# where Task Manager gets its own per-process GPU figure.
#
# The obvious route does not work, and finding that out cost an evening:
# `nvidia-smi --query-compute-apps=pid,used_gpu_memory` lists our pid happily
# and then answers `[N/A]` for the memory, on this card and every other
# consumer one -- NVML cannot break VRAM down per process while the driver is
# in WDDM mode, which is every Windows machine with a screen plugged into it.
# It is not an error and it does not fail; it just never says a number.
#
# Two things about the counter that replaced it. The instance name carries a
# LUID nothing here can predict, so the path is expanded from a wildcard rather
# than built; and a process with nothing on the card has no instance at all,
# which is a real answer -- the model is not loaded -- rather than a failure.
PDH_MORE_DATA = 0x800007D2
PDH_FMT_LARGE = 0x00000400
# What the counter is called, and it is called that in English. Windows
# localises counter names, so a German install expands nothing here and the
# panel simply shows no graphics figure. Which is the right way to be wrong:
# the alternative is looking the name up by index, and the failure it saves us
# from is showing no number to somebody who would have got one.
VRAM_COUNTER = r"\GPU Process Memory(pid_{pid}_*)\Dedicated Usage"


class _Value(ctypes.Union):
    _fields_ = [("longValue", ctypes.c_long),
                ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong),
                ("stringValue", ctypes.c_void_p)]


class _CounterValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _Value)]


try:
    _pdh = ctypes.WinDLL("pdh")
    _pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t,
                                   ctypes.POINTER(wintypes.HANDLE)]
    _pdh.PdhAddCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR,
                                    ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
    _pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
    _pdh.PdhGetFormattedCounterValue.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                 wintypes.LPDWORD,
                                                 ctypes.POINTER(_CounterValue)]
    _pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
    _pdh.PdhExpandWildCardPathW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                            wintypes.LPWSTR, wintypes.LPDWORD,
                                            wintypes.DWORD]
    # Every one of these answers PDH_STATUS, which is a signed long -- and the
    # interesting values have the top bit set. Left as ctypes' default int the
    # comparisons below still work, but only by accident; said out loud they
    # keep working when somebody adds another status to check for.
    for _fn in ("PdhOpenQueryW", "PdhAddCounterW", "PdhCollectQueryData",
                "PdhGetFormattedCounterValue", "PdhCloseQuery",
                "PdhExpandWildCardPathW"):
        getattr(_pdh, _fn).restype = ctypes.c_long
except (OSError, AttributeError):
    _pdh = None


def _expanded(wildcard):
    """Every counter path matching a wildcard, asked for size first.

    PDH answers PDH_MORE_DATA rather than filling anything in until it is given
    a buffer, and that constant compares against a *signed* return: 0x800007D2
    read as a long is negative, so the two are only equal once one of them is
    masked. Unmasked, this silently found nothing at all.
    """
    size = wintypes.DWORD(0)
    rc = _pdh.PdhExpandWildCardPathW(None, wildcard, None, ctypes.byref(size), 0)
    if (rc & 0xFFFFFFFF) not in (PDH_MORE_DATA, 0) or not size.value:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if _pdh.PdhExpandWildCardPathW(None, wildcard, buf, ctypes.byref(size), 0):
        return []
    # A run of strings one after another, ending in an empty one.
    return [part for part in buf[:size.value].split("\0") if part]


def vram_used():
    """Bytes this process holds on the graphics card, or None if it holds none.

    Under a millisecond once the counter library is warm -- measured at 0.43ms
    over four hundred calls -- so it is read per request like the RAM figure
    rather than sampled on a thread. Summed over instances, because a machine
    with two cards in it reports one per card and the question is how much of
    our three and a half gigabytes is on a card at all.
    """
    if _pdh is None:
        return None
    paths = _expanded(VRAM_COUNTER.format(pid=os.getpid()))
    if not paths:
        return None                     # nothing of ours is on the card
    query = wintypes.HANDLE()
    if _pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)):
        return None
    try:
        counters = []
        for path in paths:
            handle = wintypes.HANDLE()
            if not _pdh.PdhAddCounterW(query, path, 0, ctypes.byref(handle)):
                counters.append(handle)
        if not counters or _pdh.PdhCollectQueryData(query):
            return None
        total = 0
        for handle in counters:
            got = _CounterValue()
            if not _pdh.PdhGetFormattedCounterValue(handle, PDH_FMT_LARGE, None,
                                                    ctypes.byref(got)):
                total += int(got.value.largeValue)
        return total
    finally:
        _pdh.PdhCloseQuery(query)
```

And in the `/state` branch, alongside `"enabled"`:

```python
                # What pressing the engine switch would actually hand back.
                # Both are read here and now: neither costs enough to be worth
                # caching, and the moment worth watching is a model loading.
                "memory": {"ram": ram_used(), "vram": vram_used()},
```

## Putting it back — the panel

Two module-level helpers, which sat above `starts_with_windows`:

```python
def _gigs(count):
    """Bytes as one decimal of a gigabyte, which is all anybody reads here."""
    return f"{count / 1024 ** 3:.1f} GB"


def _held(memory):
    """The sum of the memory figures the engine actually reported.

    The sum of what is *known*, not of everything asked about: a machine that
    will not say what is on the graphics card still knows what it has in RAM,
    and showing nothing at all because half the answer is missing would be the
    worse of the two. An engine too old to mention memory reports neither, and
    then this is zero and nothing is shown -- the same lesson as the volume
    slider, which never invents a level for a process that did not report one.
    """
    memory = memory or {}
    return sum(n for n in (memory.get("ram"), memory.get("vram"))
               if isinstance(n, (int, float)))
```

A method, which sat above `_power_says`:

```python
    def _memory_says(self):
        """The split behind the total in the corner, as it stands right now.

        A callable rather than a string, because it changes -- and empty when
        there is nothing to say, which Tip already reads as "no tooltip" rather
        than drawing an empty box. An engine too old to report memory gets that
        empty answer, and so does one whose Windows will not say what is on the
        card: a figure invented for either would be worse than the silence.
        """
        memory = self.drawn.get("memory") or {}
        said = []
        if isinstance(memory.get("ram"), (int, float)):
            # "claimed" rather than "in": this is what the engine has asked
            # Windows for, not what is resident this second. Task Manager shows
            # the resident figure and it can be a fifth of this one, so the word
            # is there to keep somebody from reading the difference as a fault.
            said.append(f"{_gigs(memory['ram'])} claimed in RAM")
        if isinstance(memory.get("vram"), (int, float)):
            said.append(f"{_gigs(memory['vram'])} on the graphics card")
        return " + ".join(said)
```

A tooltip on the status label, right after it is packed in `_build`:

```python
        # The one number in that line is a total, and a total invites the
        # question it cannot answer in the room it has. Tip takes a callable
        # for exactly this, and says nothing at all when there is nothing to
        # say. It binds to a label as happily as to a button.
        self.tips["memory"] = Tip(self.status, self._memory_says, self.dark.get)
```

At the end of `render`, after the `not watching` branch and before
`self.status.configure(text=note)`:

```python
        # And what it is holding, last in the line because it is the least
        # urgent thing in it and so the first that should go when the window is
        # narrow. One total and one decimal: the split is worth knowing and is
        # not worth this much of the footer, so it is in the hover text.
        self.drawn["memory"] = st.get("memory") or {}
        held = _held(self.drawn["memory"])
        if held:
            note += f" · {_gigs(held)}"
```

And in `render_down`, so a tooltip does not outlive the engine:

```python
        # Nothing is holding anything, so the hover text has nothing to split.
        self.drawn["memory"] = {}
```

## Checking it afterwards

The engine has to be restarted before any of it appears — `speak_server.py` is imported once,
at startup. The panel side can be checked without one, by stopping the poller and calling
`render` with a state of your own:

- both figures, RAM only, card only, `memory` absent, `memory` empty — the last three must
  show nothing at all rather than a zero
- `render_down`, which must clear the hover text
- a narrow window, where the figure is the first thing to be cut

Stop the poll thread first (`panel.stopping.set()` and `after_cancel` the drain tick) or the
live engine's own answer paints over every synthetic state within a tenth of a second. That
cost twenty minutes the first time and looked exactly like a bug in the sum.

To read the figures for an engine that is already running, from outside it: `OpenProcess`
with `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ`, then the same `GetProcessMemoryInfo` call
with that handle instead of `GetCurrentProcess()`, and the same `_expanded` with its pid.
