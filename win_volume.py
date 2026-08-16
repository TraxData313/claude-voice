"""How loud the voice is, using the slider Windows already keeps for this app.

winsound has no volume of its own: PlaySound plays a wav at whatever level it
was written at, and nothing else. Scaling the samples instead would work, but
only for the next sentence -- the one already sounding would carry on at the
old level, and every wav kept for replay would be stuck at the level it was
written with. Windows keeps a volume per application, the one in the mixer
under our own name, and setting that is instant, applies mid-sentence, applies
to replayed audio, and survives nothing at all when the process ends. That is
what a volume slider should do, so that is what this drives.

Reaching it means COM, and Core Audio ships no Python binding, so this is
ctypes against the vtables directly: the device enumerator, the default
playback device, that device's session manager, and finally ISimpleAudioVolume
-- this process's own slider.

Things worth knowing before changing any of it:

- **A slot number is the whole contract.** There is no name checking here: slot
  4 of the session manager is GetSimpleAudioVolume because the interface says
  so, and a wrong number calls a different method with the wrong arguments.
  The slots are counted from 3, because 0, 1 and 2 are always IUnknown's.
- **A null session guid means 'this process'.** It is the session every
  PlaySound here lands in, and the one the volume mixer draws under our name.
- **COM belongs to a thread**, so it is set up and torn down inside each call.
  Everything made is released before returning, so there is nothing living
  across the boundary and no apartment to marshal between.
- Not every non-zero HRESULT is a failure -- 0x0889000D, for one, is Core Audio
  saying yes with a footnote. ctypes knows the difference and raises only on
  the real ones, which is why HRESULT is declared as the return type.
"""

import contextlib
import ctypes
from ctypes import POINTER, byref, c_float, c_int, c_uint32, c_void_p

ole32 = ctypes.windll.ole32

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioSessionManager = "{BFA971F1-4D5E-40BB-935E-967039BFBEE4}"

CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0
RPC_E_CHANGED_MODE = -2147417850      # this thread is already in the other kind
RENDER, CONSOLE = 0, 0                # eRender, eConsole


class GUID(ctypes.Structure):
    _fields_ = [("d1", c_uint32), ("d2", ctypes.c_uint16),
                ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        if ole32.CLSIDFromString(text, byref(self)):
            raise OSError(f"not a guid: {text}")


def _call(ptr, slot, *args, types=()):
    """Call one method of a COM interface by its place in the vtable.

    An interface pointer points at its vtable, which is an array of function
    pointers; ctypes needs to be told the shape of the one being called, and
    every method takes the interface itself as its first argument.
    """
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    signature = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *types)
    return signature(vtable[slot])(ptr, *args)


def _release(ptr):
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtable[2])(ptr)


@contextlib.contextmanager
def _slider():
    """This process's own volume, for the length of one call."""
    started = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    # S_OK and S_FALSE both have to be undone; a thread that was already in
    # COM the other way round is not ours to shut down.
    ours = started in (0, 1)
    made = []
    try:
        devices = c_void_p()
        if ole32.CoCreateInstance(byref(GUID(CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
                                  byref(GUID(IID_IMMDeviceEnumerator)), byref(devices)):
            raise OSError("no audio device enumerator")
        made.append(devices)

        speakers = c_void_p()                                # GetDefaultAudioEndpoint
        _call(devices, 4, RENDER, CONSOLE, byref(speakers),
              types=(c_int, c_int, POINTER(c_void_p)))
        made.append(speakers)

        sessions = c_void_p()                                # IMMDevice::Activate
        _call(speakers, 3, byref(GUID(IID_IAudioSessionManager)), CLSCTX_ALL, None,
              byref(sessions), types=(c_void_p, c_uint32, c_void_p, POINTER(c_void_p)))
        made.append(sessions)

        volume = c_void_p()                                  # GetSimpleAudioVolume
        _call(sessions, 4, None, 0, byref(volume),
              types=(c_void_p, c_int, POINTER(c_void_p)))
        made.append(volume)
        yield volume
    finally:
        for ptr in reversed(made):
            _release(ptr)
        if ours:
            ole32.CoUninitialize()


def clamp(level, fallback=1.0):
    """Anything that came out of a config file or off the wire, made sane."""
    try:
        return max(0.0, min(1.0, float(level)))
    except (TypeError, ValueError):
        return fallback


def set_level(level):
    """Set this process's volume, 0.0 to 1.0. Raises if Windows will not."""
    with _slider() as volume:
        _call(volume, 3, c_float(clamp(level)), None, types=(c_float, c_void_p))


def get_level():
    """What Windows currently has us at -- which the user may have dragged
    in the mixer themselves."""
    with _slider() as volume:
        level = c_float()
        _call(volume, 4, byref(level), types=(POINTER(c_float),))
        return level.value
