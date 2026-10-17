"""
Puts claude-voice on this computer as a program, and takes it off again.

    python installer.py install   [--engine pocket|qwen|breeze|none] [--no-claude] ...
    python installer.py uninstall [--quiet] [--remove-data | --keep-data] [--dry-run]
    python installer.py update    [--source FOLDER]       (update_check.py runs this)
    python installer.py stop                               (every process of this install)
    python installer.py status

Nobody types the first one. install.ps1 runs it once it has fetched python.org's
own Python into the program folder (and the Immersive AI mod does the same from
the game); Settings -> Apps runs the second through that Python's pythonw.exe.

THE LAYOUT. One folder for the program, one for what is yours, and nothing of
either inside the other -- so an update replaces the program without touching
your settings, and an uninstall can ask about your voices separately:

    %LOCALAPPDATA%\\Programs\\claude-voice\\     the program (--root)
        runtime\\       python.org's Python 3.12 for Windows, signed by the PSF,
                       private: packages go in here and never in anybody else's
        app\\           this project's code, swapped whole on an update
        install.json   what was installed, and every thing outside this
                       folder it put somewhere -- the uninstaller's list
    %LOCALAPPDATA%\\claude-voice\\               yours (--data)
        config.json, logs\\, voices\\ (the ones you make), where.json

WHY PYTHON AND NOT AN EXE. Smart App Control, on by default on a clean Windows
11, refuses to run an executable or load a DLL that has no signature with a
reputation behind it. An installer built here is unsigned until there is a
certificate (step 7b), so it would be refused; python.org's python.exe and
pythonw.exe are signed and run, and so does this file through them. The same
reason keeps every hidden PowerShell and -ExecutionPolicy Bypass out of it.

A DEVELOPER'S CHECKOUT is never installed into, rewritten or removed: it is a
source this copies FROM. Its registration (Kind=dev) is taken over by the
install, because the newest install is the one games should find, and the
checkout can take it back with `voice_cli.py register`.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.realpath(__file__))
# Run from a staged source folder the script's own folder is sys.path[0]; said
# outright anyway, so these imports never resolve against an older app\.
if HERE not in sys.path:
    sys.path.insert(0, HERE)

PRODUCT = "claude-voice"
PUBLISHER = "TraxData313"
URL = "https://github.com/TraxData313/claude-voice"
ZIP_URL = f"{URL}/archive/refs/heads/main.zip"
# The name on the Start menu and the window title agree, so the pinned icon and
# the running window are one taskbar button (panel.APP_NAME).
SHORTCUT_NAME = "Abby for Claude"
LAYOUT = 1

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# Never copied into app\: a checkout's own history, its machine's settings and
# logs, the build output of the retired setup window, and the tests.
SKIP_DIRS = {".git", ".github", ".claude", ".claude-plugin", ".manager", "logs",
             "__pycache__", "setup-app", "voices-local", "original_Abby_and_Max_generated_pics"}
# .gitignore is never copied: in app\ that name is OLD_SETUP_GUARD, written by
# guard_against_old_setup, and a read-only copy of it must not travel.
SKIP_FILES = {"config.json", "installed.json", "demo.mp4", ".gitignore"}

ENGINES = ("pocket", "qwen", "breeze", "none")

# Qwen-TTS Studio, which only the Qwen engine needs (and Smart App Control refuses:
# its DLLs are unsigned). The same release setup.ps1 fetched.
STUDIO_VERSION = "0.2.9"
STUDIO_URL = ("https://github.com/Danmoreng/qwen-tts-studio/releases/download/"
              f"v{STUDIO_VERSION}/qwen-tts-studio-{STUDIO_VERSION}-windows-cuda-bundled.zip")
QWEN_MODELS_URL = "https://huggingface.co/Serveurperso/Qwen3-TTS-GGUF/resolve/main"
QWEN_MODELS = ("qwen-talker-1.7b-base-Q8_0.gguf", "qwen-tokenizer-12hz-Q8_0.gguf")


class Cancelled(Exception):
    """The setup-cancel file appeared: stop between two steps, losing nothing."""


class Refused(Exception):
    """Something about this machine says no, in a sentence a person can act on."""


# --------------------------------------------------------------------------
# where everything goes
# --------------------------------------------------------------------------

def _local():
    return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")


def _known_folder(csidl, fallback):
    """A shell folder (Start menu, Desktop) where Windows says it is -- it may be
    redirected to OneDrive -- or the usual place when that cannot be asked."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return buf.value
    except (AttributeError, OSError):
        pass
    return fallback


class Layout:
    """Every path and registry name one install uses, from three choices.

    `product` names the registry keys, the Run value and the notes folder, so a
    throwaway install for a test (`--product claude-voice-test-1`) shares
    nothing with the real one. `claude_dir` and `shortcut_dir` exist for the
    same reason: a test points them into a temp folder.
    """

    def __init__(self, root=None, product=PRODUCT, data=None, claude_dir=None,
                 shortcut_dir=None):
        self.product = product or PRODUCT
        self.root = _full(root or os.path.join(_local(), "Programs", self.product))
        self.app = os.path.join(self.root, "app")
        self.runtime = os.path.join(self.root, "runtime")
        self.manifest = os.path.join(self.root, "install.json")
        self.python = os.path.join(self.runtime, "python.exe")
        self.pythonw = os.path.join(self.runtime, "pythonw.exe")
        self.notes = os.path.join(_local(), self.product)
        self.data = _full(data) if data else self.notes
        self.claude_dir = _full(claude_dir or os.path.join(os.path.expanduser("~"), ".claude"))
        self.reg_key = "Software\\" + self.product
        self.uninstall_key = UNINSTALL_KEY + "\\" + self.product
        self.run_value = self.product
        programs = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        self.start_menu = shortcut_dir or _known_folder(0x02, programs)       # CSIDL_PROGRAMS
        self.desktop = shortcut_dir or _known_folder(0x10, desktop)          # CSIDL_DESKTOPDIRECTORY
        name = SHORTCUT_NAME if self.product == PRODUCT else self.product
        self.shortcut_leaf = name + ".lnk"
        self.status_path = os.path.join(self.notes, "setup-status.json")
        self.cancel_path = os.path.join(self.notes, "setup-cancel")
        self.log_path = os.path.join(self.notes, "setup.log")

    @property
    def settings(self):
        return os.path.join(self.claude_dir, "settings.json")

    @property
    def claude_md(self):
        return os.path.join(self.claude_dir, "CLAUDE.md")

    @property
    def command_md(self):
        return os.path.join(self.claude_dir, "commands", "voice.md")

    def manifest_doc(self):
        return read_json(self.manifest)

    @classmethod
    def of_installed(cls, root):
        """The layout an existing install recorded for itself."""
        root = os.path.abspath(root)
        doc = read_json(os.path.join(root, "install.json"))
        if not doc:
            raise Refused(f"{root} has no install.json -- it was not installed by this installer.")
        # An uninstall deletes this folder whole, so a stray install.json of
        # somebody else's (a --root typed wrong) must not be enough to say yes.
        if doc.get("layout") != LAYOUT or not os.path.isdir(os.path.join(root, "app")):
            raise Refused(f"{os.path.join(root, 'install.json')} is not one this installer wrote.")
        claude = doc.get("claude") or {}
        lay = cls(root, doc.get("product") or PRODUCT, doc.get("data"),
                  claude.get("dir"), doc.get("shortcutDir"))
        return lay


def _full(path):
    """An absolute path with Windows' short names (SOMEON~1) spelt out in full,
    so a path written into a hook or a shortcut is the same string every time
    -- comparing them later is how the uninstaller knows what is its own."""
    return os.path.realpath(os.path.abspath(path))


def mentions(text, folder):
    """Whether `text` (a command line, a file) names `folder`, however spelt:
    either slash, either case, short or long names."""
    text = (text or "").replace("\\", "/").replace('"', "").lower()
    forms = {os.path.abspath(folder), _full(folder)}
    return any(f.replace("\\", "/").lower().rstrip("/") + "/" in text + "/" for f in forms if f)


def read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, doc):
    """Whole or not at all: a reader never sees half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.{os.getpid()}.new"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    for attempt in range(10):
        try:
            os.replace(temp, path)
            return
        except PermissionError:          # a reader holding it for a moment
            if attempt == 9:
                raise
            time.sleep(0.1)


def same_path(a, b):
    if not a or not b:
        return False
    try:
        return (os.path.normcase(os.path.realpath(a)).rstrip("\\/")
                == os.path.normcase(os.path.realpath(b)).rstrip("\\/"))
    except (OSError, ValueError):
        return False


def inside(path, folder):
    """Whether `path` is `folder` or anywhere under it."""
    if not path or not folder:
        return False
    try:
        p = os.path.normcase(os.path.realpath(path))
        f = os.path.normcase(os.path.realpath(folder)).rstrip("\\/")
    except (OSError, ValueError):
        return False
    return p == f or p.startswith(f + os.sep)


def own_version(app):
    return (read_json(os.path.join(app, "version.json")).get("version") or "").strip()


# --------------------------------------------------------------------------
# saying what is happening: the console, a log, and the status file a game reads
# --------------------------------------------------------------------------

PHASES = ("app", "engine", "model", "start")


class Out:
    """One line per step for whoever is watching, the same lines in setup.log,
    and setup-status.json in the shape the Immersive AI mod already draws
    (ClaudeVoiceApp.SetupProgress): state, phase, headline, detail, fractions."""

    def __init__(self, lay, engine="", quiet=False, dry_run=False, status=True):
        self.lay, self.quiet, self.dry_run = lay, quiet, dry_run
        self.status_on = status and not dry_run
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.doc = {"state": "running", "engine": engine, "phase": "app", "headline": "",
                    "detail": "", "phaseFraction": None, "fraction": 0.0, "error": "",
                    "log": lay.log_path, "data": "", "started": now, "phaseStarted": now,
                    "updated": now, "pid": os.getpid(), "quiet": quiet, "final": False}
        if not dry_run:
            os.makedirs(lay.notes, exist_ok=True)

    def say(self, text=""):
        try:
            print(text, flush=True)
        except (OSError, ValueError, AttributeError):
            pass                 # pythonw: there is no console, and that is fine
        if self.dry_run:
            return
        try:
            with open(self.lay.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
        except OSError:
            pass

    def line(self, label, text):
        self.say(f"{label:<14}: {text}")

    def status(self, **fields):
        if not self.status_on:
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if fields.get("phase") and fields["phase"] != self.doc["phase"]:
            self.doc["phaseStarted"] = now
            self.doc["phaseFraction"] = None
        self.doc.update(fields)
        if "phase" in fields and fields["phase"] in PHASES and "fraction" not in fields:
            self.doc["fraction"] = PHASES.index(fields["phase"]) / len(PHASES)
        self.doc["updated"] = now
        try:
            write_json(self.lay.status_path, self.doc)
        except OSError:
            pass

    def check_cancel(self):
        if os.path.exists(self.lay.cancel_path):
            raise Cancelled()


# --------------------------------------------------------------------------
# Windows: shortcuts, processes, the registry -- all through ctypes and winreg,
# so nothing here starts PowerShell or anything else to do it
# --------------------------------------------------------------------------

def _winreg():
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def reg_read(key, value=None):
    """A value, or every value of a key as a dict when `value` is None. None if absent."""
    reg = _winreg()
    if reg is None:
        return None
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, key) as k:
            if value is not None:
                return reg.QueryValueEx(k, value)[0]
            out, i = {}, 0
            while True:
                try:
                    name, data, _ = reg.EnumValue(k, i)
                except OSError:
                    return out
                out[name] = data
                i += 1
    except OSError:
        return None


def reg_write(key, values):
    reg = _winreg()
    with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, key, 0, reg.KEY_SET_VALUE) as k:
        for name, data in values.items():
            if isinstance(data, bool) or isinstance(data, int):
                reg.SetValueEx(k, name, 0, reg.REG_DWORD, int(data))
            else:
                reg.SetValueEx(k, name, 0, reg.REG_SZ, str(data))


def reg_delete_key(key):
    reg = _winreg()
    try:
        reg.DeleteKey(reg.HKEY_CURRENT_USER, key)
        return True
    except OSError:
        return False


def reg_delete_value(key, value):
    reg = _winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, key, 0, reg.KEY_SET_VALUE) as k:
            reg.DeleteValue(k, value)
        return True
    except OSError:
        return False


def _com():
    """The few COM calls a shortcut needs, through ctypes: IShellLinkW and
    IPersistFile, called by their slot in the vtable. A dependency-free project
    has no binding for these, and the old answer -- PowerShell's WScript.Shell --
    is exactly the hidden-PowerShell pattern this installer is here to remove."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort),
                    ("d", ctypes.c_ubyte * 8)]

    ole32 = ctypes.OleDLL("ole32")

    def guid(text):
        g = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g

    def call(obj, slot, restype, argtypes, *args):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[slot])
        return fn(obj, *args)

    return ctypes, wintypes, ole32, guid, call


CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"


def _shell_link(work):
    ctypes, wintypes, ole32, guid, call = _com()
    try:
        ole32.CoInitialize(None)
    except OSError:
        pass                     # already initialised on this thread, another way
    link = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(guid(CLSID_SHELL_LINK)), None, 1,      # CLSCTX_INPROC_SERVER
                           ctypes.byref(guid(IID_SHELL_LINK_W)), ctypes.byref(link))
    persist = ctypes.c_void_p()
    try:
        call(link, 0, ctypes.HRESULT, [ctypes.c_void_p, ctypes.c_void_p],     # QueryInterface
             ctypes.byref(guid(IID_PERSIST_FILE)), ctypes.byref(persist))
        try:
            return work(ctypes, call, link, persist)
        finally:
            call(persist, 2, ctypes.c_ulong, [])                                # Release
    finally:
        call(link, 2, ctypes.c_ulong, [])


def make_shortcut(path, target, args="", workdir="", description="", icon=""):
    """Write a .lnk. IShellLinkW slots: 7 SetDescription, 9 SetWorkingDirectory,
    11 SetArguments, 17 SetIconLocation, 20 SetPath; IPersistFile 6 Save."""
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def work(ctypes, call, link, persist):
        w = ctypes.c_wchar_p
        call(link, 20, ctypes.HRESULT, [w], target)
        call(link, 11, ctypes.HRESULT, [w], args)
        call(link, 9, ctypes.HRESULT, [w], workdir)
        call(link, 7, ctypes.HRESULT, [w], description)
        if icon and os.path.exists(icon):
            call(link, 17, ctypes.HRESULT, [w, ctypes.c_int], icon, 0)
        call(persist, 6, ctypes.HRESULT, [w, ctypes.c_int], path, 1)

    _shell_link(work)
    return path


def read_shortcut(path):
    """(target, arguments) of a .lnk, or None. IPersistFile 5 Load; IShellLinkW
    3 GetPath, 10 GetArguments."""
    if not os.path.isfile(path):
        return None

    def work(ctypes, call, link, persist):
        call(persist, 5, ctypes.HRESULT, [ctypes.c_wchar_p, ctypes.c_ulong], path, 0)
        target = ctypes.create_unicode_buffer(1024)
        args = ctypes.create_unicode_buffer(4096)
        call(link, 3, ctypes.HRESULT, [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong],
             target, 1024, None, 0)
        call(link, 10, ctypes.HRESULT, [ctypes.c_wchar_p, ctypes.c_int], args, 4096)
        return target.value, args.value

    try:
        return _shell_link(work)
    except OSError:
        return None


def processes():
    """[(pid, parent pid, image path)] for every process this user may look at."""
    import ctypes
    from ctypes import wintypes

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)                    # TH32CS_SNAPPROCESS
    out = []
    if not snap or snap == wintypes.HANDLE(-1).value:
        return out
    try:
        entry = ENTRY()
        entry.dwSize = ctypes.sizeof(ENTRY)
        ok = k32.Process32FirstW(wintypes.HANDLE(snap), ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.th32ParentProcessID, _image(k32, entry.th32ProcessID)))
            ok = k32.Process32NextW(wintypes.HANDLE(snap), ctypes.byref(entry))
    finally:
        k32.CloseHandle(wintypes.HANDLE(snap))
    return out


def _image(k32, pid):
    import ctypes
    from ctypes import wintypes
    h = k32.OpenProcess(0x1000, False, pid)                        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if k32.QueryFullProcessImageNameW(wintypes.HANDLE(h), 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(wintypes.HANDLE(h))


def command_line(pid):
    """A process's command line (NtQueryInformationProcess, class 60), or ""."""
    import ctypes
    from ctypes import wintypes

    class USTR(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                    ("Buffer", ctypes.c_void_p)]

    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = wintypes.HANDLE
    ntdll = ctypes.WinDLL("ntdll")
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        need = ctypes.c_ulong(0)
        ntdll.NtQueryInformationProcess(wintypes.HANDLE(h), 60, None, 0, ctypes.byref(need))
        if not need.value:
            return ""
        buf = ctypes.create_string_buffer(need.value)
        if ntdll.NtQueryInformationProcess(wintypes.HANDLE(h), 60, buf, need, ctypes.byref(need)) != 0:
            return ""
        us = USTR.from_buffer(buf)
        return ctypes.wstring_at(us.Buffer, us.Length // 2) if us.Buffer else ""
    finally:
        k32.CloseHandle(wintypes.HANDLE(h))


def _ancestors(table):
    parents = {pid: ppid for pid, ppid, _ in table}
    chain, pid = set(), os.getpid()
    while pid and pid not in chain:
        chain.add(pid)
        pid = parents.get(pid)
    return chain


def our_processes(folders, scripts=()):
    """[(pid, image, command line)] running out of any of `folders` (the
    runtime, a claimed Breeze), never this process or the ones that started it
    -- the panel runs an update in-process, and must not be killed by it.
    `scripts`: folders whose scripts count too when some other Python runs
    them -- an install before 2.0 ran on the system's own Python."""
    try:
        table = processes()
    except (OSError, AttributeError):
        return []
    mine = _ancestors(table)
    out = []
    for pid, _, image in table:
        if pid in mine or not image:
            continue
        if any(inside(image, f) for f in folders if f):
            out.append((pid, image, command_line(pid)))
        elif scripts and os.path.basename(image).lower() in ("python.exe", "pythonw.exe"):
            cl = command_line(pid)
            flat = (cl or "").replace('"', "").replace("/", "\\").lower()
            if any(_full(f).lower() + "\\" in flat for f in scripts if f):
                out.append((pid, image, cl))
    return out


def terminate(pid):
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x0001 | 0x00100000, False, pid)          # TERMINATE | SYNCHRONIZE
    if not h:
        return False
    try:
        ok = bool(k32.TerminateProcess(wintypes.HANDLE(h), 1))
        k32.WaitForSingleObject(wintypes.HANDLE(h), 3000)
        return ok
    finally:
        k32.CloseHandle(wintypes.HANDLE(h))


def _port_of(lay):
    try:
        return int(read_json(os.path.join(lay.data, "config.json")).get("port") or 8765)
    except (TypeError, ValueError):
        return 8765


def _health(port, timeout=1.5):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            doc = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        return doc if isinstance(doc, dict) and "ready" in doc else None
    except Exception:
        return None


def stop_everything(lay, out, extra=(), scripts=()):
    """Every process of this install, asked first and told second.

    The engine gets /quit, so it lets go of the sound card and its children
    (Breeze's server) the way it always does; whatever still runs from the
    runtime a few seconds later -- the panel, a stuck engine -- is ended.
    Returns whether the panel was among them, so an update can open it again.
    """
    folders = [lay.runtime] + [f for f in extra if f]
    port = _port_of(lay)
    health = _health(port)
    # Only our own engine: another copy (a developer's checkout) may hold the
    # same port. An engine older than "root" in /health is placed by where.json.
    root = (health or {}).get("root") or read_json(os.path.join(lay.notes, "where.json")).get("root")
    if health and inside(root, lay.root):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/quit", data=b"{}",
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=3).read()
            out.line("engine", "asked to stop")
        except Exception:
            pass
    panel = False
    deadline = time.time() + 8
    while True:
        left = our_processes(folders, scripts)
        panel = panel or any("panel.py" in (cl or "") for _, _, cl in left)
        if not left or time.time() > deadline:
            break
        if not any("speak_server.py" in (cl or "") for _, _, cl in left):
            break                    # nothing left that would go by itself
        time.sleep(0.5)
    for pid, image, cl in our_processes(folders, scripts):
        panel = panel or "panel.py" in (cl or "")
        if terminate(pid):
            out.line("stopped", f"{os.path.basename(image)} ({pid})")
    return panel


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------

def _rmtree(path, ignore_errors=False):
    """shutil.rmtree that also removes read-only files (OLD_SETUP_GUARD). Python
    3.12's own leaves them, which would leave app.old behind and then block the
    next swap_in. With ignore_errors it goes on past what will not go (a file a
    running process holds), as shutil's own does."""
    import stat

    def again(func, failed, *_):
        try:
            os.chmod(failed, stat.S_IWRITE)
            func(failed)
        except OSError:
            if not ignore_errors:
                raise

    hook = {"onexc": again} if sys.version_info >= (3, 12) else {"onerror": again}
    try:
        shutil.rmtree(path, **hook)
    except OSError:
        if not ignore_errors:
            raise


# An Immersive AI release from before the registry key (3.4 to 3.6) still
# downloads ClaudeVoiceSetup.exe 1.16 to add an engine. That exe installs into
# the folder where.json names -- app\ for this layout -- by laying the 1.16 code
# over it file by file and then running the 1.16 setup.ps1 there: a 2.0 install
# turned back into half of a 1.16 one, on the system's Python. It copies the
# zip's files in name order, and the first is .gitignore. A read-only file of
# that name makes its very first copy fail with an error it does not catch
# (UnauthorizedAccessException; an IOException it would retry), so it stops
# before it has written anything, and the game says the install did not finish.
OLD_SETUP_GUARD = ".gitignore"
OLD_SETUP_GUARD_TEXT = (
    "# claude-voice 2.0 or later is installed in this folder.\n"
    "#\n"
    "# This file is read-only on purpose. ClaudeVoiceSetup.exe from before 2.0 (which\n"
    "# Immersive AI 3.4 to 3.6 download to add a voice engine) would otherwise copy\n"
    "# its old code over this folder; this file is the first it tries to write, so\n"
    "# it stops there with 'Access to the path ... is denied' and changes nothing.\n"
    "#\n"
    "# To add an engine: the voice app's panel, or run install.ps1 again\n"
    "# (https://github.com/TraxData313/claude-voice/blob/main/docs/install.md).\n"
    "# A newer Immersive AI installs engines without that exe.\n"
)


def guard_against_old_setup(app_dir):
    """Write OLD_SETUP_GUARD into `app_dir`, read-only. Idempotent."""
    import stat
    path = os.path.join(app_dir, OLD_SETUP_GUARD)
    if os.path.exists(path):
        os.chmod(path, stat.S_IWRITE)
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(OLD_SETUP_GUARD_TEXT)
    os.chmod(path, stat.S_IREAD)
    return path


def copy_app(source, dest):
    """This project's files from `source` into a fresh `dest`. Returns the count."""
    if os.path.exists(dest):
        _rmtree(dest)
    count = 0
    for here, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(here, source)
        target = dest if rel == "." else os.path.join(dest, rel)
        os.makedirs(target, exist_ok=True)
        for name in files:
            if (name in SKIP_FILES or name.endswith(".pyc") or name.startswith("config.json.")
                    or (rel == "." and name.startswith("test_") and name.endswith(".py"))):
                continue
            shutil.copy2(os.path.join(here, name), os.path.join(target, name))
            count += 1
    return count


def _retry(fn, *args, tries=20, wait=0.25):
    for attempt in range(tries):
        try:
            return fn(*args)
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(wait)


def swap_in(new, live):
    """Put the folder `new` where `live` is, as two renames, and put the old one
    back if the second fails. The live folder is never half old, half new."""
    old = live + ".old"
    if os.path.exists(old):
        _rmtree(old, ignore_errors=True)
    had = os.path.exists(live)
    if had:
        _retry(os.rename, live, old)
    try:
        _retry(os.rename, new, live)
    except OSError:
        if had:
            os.rename(old, live)
        raise
    if had:
        _rmtree(old, ignore_errors=True)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url, dest, out, label, phase=None):
    """Stream to dest.part and rename; resumes a part-file with a Range request."""
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "claude-voice installer"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have:
            os.replace(part, dest)
            return dest
        raise
    with resp:
        if have and resp.status != 206:
            have = 0                          # the server ignored the Range: start again
        total = int(resp.headers.get("Content-Length") or 0) + have
        out.line(label, f"{total / 1e6:.0f} MB" + (f", resuming at {have / 1e6:.0f}" if have else ""))
        done, last = have, 0.0
        with open(part, "ab" if have else "wb") as fh:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                fh.write(block)
                done += len(block)
                if time.time() - last > 2:
                    last = time.time()
                    out.check_cancel()
                    if total:
                        out.status(detail=f"{label}: {done / 1e6:.0f} of {total / 1e6:.0f} MB",
                                   phaseFraction=done / total)
    if total and os.path.getsize(part) != total:
        raise OSError(f"{label}: got {os.path.getsize(part)} bytes, expected {total}")
    os.replace(part, dest)
    return dest


# --------------------------------------------------------------------------
# the runtime: it is already in place (install.ps1 or the game put it there,
# checked against runtime.json); this makes it ours
# --------------------------------------------------------------------------

SITECUSTOMIZE = '''"""claude-voice's Python is private to it. A user's own site-packages for the
same Python version (%APPDATA%\\\\Python\\\\Python312) is somebody else's, and a
stale numpy there would be loaded before ours -- so it is taken off the path."""
import site
import sys

try:
    if site.USER_SITE in sys.path:
        sys.path.remove(site.USER_SITE)
except Exception:
    pass
'''


def prepare_runtime(lay, out, dry_run):
    if not os.path.isfile(lay.python):
        raise Refused(f"there is no Python at {lay.python}. install.ps1 puts python.org's there first; "
                      "run that rather than this file.")
    if dry_run:
        out.line("runtime", f"{lay.runtime} (would add sitecustomize.py)")
        return
    with open(os.path.join(lay.runtime, "Lib", "sitecustomize.py"), "w", encoding="utf-8") as fh:
        fh.write(SITECUSTOMIZE)
    pin = read_json(os.path.join(lay.runtime, "claude-voice-runtime.json"))
    out.line("runtime", f"Python {pin.get('version') or '?'} at {lay.runtime}")


# --------------------------------------------------------------------------
# what was here before
# --------------------------------------------------------------------------

def old_layout(root, product=PRODUCT):
    """True when `root` holds a pre-2.0 install: the code straight in the folder
    (setup.ps1 and ClaudeVoiceSetup.exe put it there), not under app\\.

    Moving one aside deletes its files, so the code being there is not enough:
    an installer must have left its mark (installed.json, or the Settings ->
    Apps entry naming this folder), and a copy registered as a developer's is
    never one."""
    if not os.path.isfile(os.path.join(root, "voice_lib.py")) or is_dev_checkout(root):
        return False
    key = reg_read("Software\\" + product) or {}
    if key.get("Kind") == "dev" and key.get("InstallDir") and same_path(key["InstallDir"], root):
        return False
    if read_json(os.path.join(root, "installed.json")).get("kind") == "dev":
        return False
    if os.path.isfile(os.path.join(root, "installed.json")):
        return True
    where = (reg_read(UNINSTALL_KEY + "\\" + product) or {}).get("InstallLocation")
    return bool(where) and same_path(where, root)


def is_dev_checkout(folder):
    # exists, not isdir: a git worktree or submodule has a .git FILE, and is
    # every bit as much somebody's own folder.
    return os.path.exists(os.path.join(folder, ".git"))


def previous_copy(lay):
    """The folder of the copy games found before this install, if any -- its
    settings are worth bringing across to a first install's empty data folder."""
    found = reg_read(lay.reg_key) or {}
    root = found.get("InstallDir")
    if not root:
        root = read_json(os.path.join(lay.notes, "where.json")).get("root")
    if root and os.path.isdir(root) and not same_path(root, lay.app):
        return root
    return None


def own_voice_files(old_voices, shipped_voices):
    """[(file, relative path)] under an older install's voices folder that the
    new release does not ship: the voices that install's `clone` made, which
    went in beside the shipped ones (voices/<sex>/<culture>/<id>) before 2.0."""
    found = []
    for here, _, files in os.walk(old_voices):
        for name in files:
            path = os.path.join(here, name)
            rel = os.path.relpath(path, old_voices)
            if not os.path.isfile(os.path.join(shipped_voices, rel)):
                found.append((path, rel))
    return found


def migrate_old_layout(lay, out, dry_run, source=None):
    """Move a pre-2.0 install in this same folder aside: its settings and any
    voice it made go to the data folder, its claims into the new manifest, and
    its files away. Returns the claims (what it had fetched)."""
    root = lay.root
    claims = read_json(os.path.join(root, "installed.json"))
    out.line("older install", f"found in {root}: moving it to the new layout")
    if dry_run:
        return {k: v for k, v in claims.items() if k in ("studio", "models", "breeze", "qwen")}
    cfg = os.path.join(root, "config.json")
    if os.path.isfile(cfg) and not os.path.isfile(os.path.join(lay.data, "config.json")):
        os.makedirs(lay.data, exist_ok=True)
        shutil.copy2(cfg, os.path.join(lay.data, "config.json"))
        out.line("settings", "carried over from the older install")
    # Before the files go: voices this install made lived in its own voices\.
    old_voices = os.path.join(root, "voices")
    if os.path.isdir(old_voices):
        mine = own_voice_files(old_voices, os.path.join(source or HERE, "voices"))
        for path, rel in mine:
            dest = os.path.join(lay.data, "voices", rel)
            if not os.path.exists(dest):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(path, dest)
        if mine:
            out.line("your voices", f"{len(mine)} file(s) carried over to {os.path.join(lay.data, 'voices')}")
    keep = {"app", "app.new", "runtime", "runtime.new", "install.json"}
    for name in os.listdir(root):
        if name in keep:
            continue
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path):
                _rmtree(path)
            else:
                os.remove(path)
        except OSError as exc:
            out.line("left", f"{path}: {exc}")
    return {k: v for k, v in claims.items() if k in ("studio", "models", "breeze", "qwen")}


def sweep_parked(lay):
    """Runtimes an earlier uninstall could not delete while it ran on them (see
    park_runtime): gone by now, since nothing runs from them any more."""
    temp = os.environ.get("TEMP") or ""
    try:
        names = os.listdir(temp)
    except OSError:
        return
    for name in names:
        if name.startswith(lay.product + "-removed-"):
            _rmtree(os.path.join(temp, name), ignore_errors=True)


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

def fresh_config(app):
    """config.example.json without its comments and its C:\\Users\\you placeholders."""
    doc = read_json(os.path.join(app, "config.example.json"))
    for key in list(doc):
        if key.startswith("_"):
            doc.pop(key)
    for key in ("studioDir", "modelDir", "talker"):
        doc.pop(key, None)
    return doc


def write_config(lay, out, engine, port=None, studio=None, dry_run=False):
    """The data folder's config.json: made on a first install (from the copy
    games found before, when there is one, else the example), and on every
    install only the engine and any paths this run found are written into it."""
    path = os.path.join(lay.data, "config.json")
    have = read_json(path)
    fresh = not have
    if fresh:
        before = previous_copy(lay)
        carried = read_json(os.path.join(before, "config.json")) if before else {}
        have = carried or fresh_config(lay.app if os.path.isdir(lay.app) else HERE)
        out.line("settings", f"taken from {before}" if carried else "new")
        for key in ("mutedSessions",):
            have.pop(key, None)
    if engine in ("pocket", "qwen"):
        have["engine"] = engine
    elif fresh:
        have["engine"] = "pocket"        # Breeze names itself once it is really there
    if port:
        have["port"] = int(port)
        have.pop("portHome", None)
    if studio:
        have.update(studio)
    if not dry_run:
        write_json(path, have)
    return have


# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------

def _no_console_flags():
    """0 when this process has a console its children can share. Without one --
    pythonw, started by the panel's update button or by Settings > Apps -- a
    python.exe child would open a console window of its own for every step; it
    is asked not to, as the engine's own start already is (voice_lib)."""
    try:
        import ctypes
        return 0 if ctypes.windll.kernel32.GetConsoleWindow() else 0x08000000   # CREATE_NO_WINDOW
    except (AttributeError, OSError):
        return 0


def run(cmd, out, cwd=None, check=True):
    """A child process in this console (none for a quiet install), its lines
    copied into the log as they come. No hidden windows: a quiet install is
    the game's, which started this one without a console to begin with."""
    out.say("  > " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                            creationflags=_no_console_flags())
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-20:]
        if line.startswith("Progress ") and " of " in line:     # pip --progress-bar raw
            try:
                done, total = line.split()[1], line.split()[3]
                out.status(phaseFraction=int(done) / max(1, int(total)))
            except (IndexError, ValueError):
                pass
            continue
        out.say("    " + line)
        out.check_cancel()
    code = proc.wait()
    if check and code != 0:
        raise Refused(f"{os.path.basename(cmd[0])} {' '.join(cmd[1:3])} stopped with exit code {code}:\n"
                      + "\n".join(tail[-5:]))
    return code


def pip_install(lay, out, *packages):
    run([lay.python, "-m", "pip", "install", "--disable-pip-version-check",
         "--no-warn-script-location", "--progress-bar", "raw", *packages], out)


def install_pocket(lay, out, dry_run, claims):
    """pocket-tts into the private Python. Its weights come later, fetched by the
    engine into the shared Hugging Face cache the first time it loads -- so they
    are claimed (removed by the uninstall) only when none were there before this
    install: another copy, or another program, may be using them."""
    claims.setdefault("pocketWeights", not _hf_pocket_weights())
    out.status(phase="engine", headline="Installing Pocket TTS",
               detail="pocket-tts and torch's CPU build, from PyPI")
    found = subprocess.run([lay.python, "-c", "import importlib.util,sys;"
                            "sys.exit(0 if importlib.util.find_spec('pocket_tts') else 1)"],
                           capture_output=True).returncode == 0 if os.path.isfile(lay.python) else False
    if found:
        out.line("pocket-tts", "installed")
        return
    if dry_run:
        out.line("would install", "pocket-tts into the private Python, with pip from PyPI")
        return
    out.line("pocket-tts", "installing with pip into the private Python -- torch's CPU build "
             "comes with it, a few hundred MB")
    # Exact versions: Smart App Control allows a compiled module by its file, and
    # these are the files seen to load with it on (requirements-pocket.txt).
    lock = os.path.join(lay.app, "requirements-pocket.txt")
    pip_install(lay, out, *(["--only-binary=:all:", "-r", lock] if os.path.isfile(lock) else ["pocket-tts"]))


def _studio_ok(folder):
    return bool(folder) and os.path.isfile(os.path.join(folder, "runtime", "bin", "server", "jvm.dll"))


def _engine_before(lay):
    """"qwen" when the settings this install will take on come from before 2.0,
    never named an engine (Qwen was the default then, and a config is saved
    lean) and Studio is really there; else None (voice_lib.old_default_engine)."""
    have = read_json(os.path.join(lay.data, "config.json"))
    if not have and old_layout(lay.root, lay.product):
        have = read_json(os.path.join(lay.root, "config.json"))
    if not have:
        before = previous_copy(lay)
        have = read_json(os.path.join(before, "config.json")) if before else {}
    if not have or "engine" in have:
        return None
    studio = have.get("studioDir") or os.path.join(_local(), "Programs", "qwen-tts-studio")
    return "qwen" if _studio_ok(studio) else None


def install_qwen(lay, out, big, dry_run):
    """Studio and the 1.7b model, each skipped when already on this machine.
    Only where Smart App Control is off: with it on, install_qwen_torch."""
    out.status(phase="engine", headline="Fetching Qwen-TTS Studio")
    claims, config = {}, {}
    studio = os.path.join(big, "qwen-tts-studio") if big else os.path.join(_local(), "Programs", "qwen-tts-studio")
    found = next((d for d in (studio, os.path.join(_local(), "Programs", "qwen-tts-studio"),
                              os.path.join(os.environ.get("ProgramFiles", ""), "qwen-tts-studio"))
                  if _studio_ok(d)), None)
    if found:
        studio = found
        out.line("studio", f"already at {studio}")
    elif dry_run:
        out.line("would fetch", f"{STUDIO_URL} -> {studio}")
    else:
        pkg = download(STUDIO_URL, os.path.join(os.environ.get("TEMP", lay.notes), "qwen-studio-pkg",
                                                os.path.basename(STUDIO_URL)), out, "studio")
        staging = studio + ".unpack"
        _rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(pkg) as z:
            z.extractall(staging)
        top = staging if _studio_ok(staging) else next(
            (os.path.join(staging, d) for d in os.listdir(staging) if _studio_ok(os.path.join(staging, d))), None)
        if not top:
            raise Refused(f"unpacked {os.path.basename(pkg)} but found no runtime\\bin\\server\\jvm.dll in it")
        if os.path.exists(studio):
            _rmtree(studio)
        os.rename(top, studio)
        _rmtree(staging, ignore_errors=True)
        claims["studio"] = studio
        out.line("studio", studio)
    models = os.path.join(big, "models") if big else os.path.join(os.path.expanduser("~"), ".qwen-tts-studio", "models")
    usual = os.path.join(os.path.expanduser("~"), ".qwen-tts-studio", "models")
    if all(os.path.isfile(os.path.join(usual, m)) for m in QWEN_MODELS):
        models = usual
    out.status(phase="model", headline="Fetching the Qwen voice model")
    for name in QWEN_MODELS:
        dest = os.path.join(models, name)
        if os.path.isfile(dest):
            out.line("model", f"have {name}")
        elif dry_run:
            out.line("would fetch", f"{name} -> {models}")
        else:
            download(f"{QWEN_MODELS_URL}/{name}", dest, out, name)
            claims["models"] = models
    config.update(studioDir=studio, modelDir=models, talker=QWEN_MODELS[0])
    return claims, config


def install_qwen_torch(lay, out, big, dry_run):
    """Qwen on PyTorch, for a machine where Smart App Control refuses Studio:
    through the installer the panel and `voice_cli install qwen` share."""
    out.status(phase="engine", headline="Installing Qwen on PyTorch", detail="about 7 GB")
    if dry_run:
        out.line("would install", "Qwen on PyTorch (voice_cli install qwen --yes)")
        return {}
    had = read_json(os.path.join(lay.data, "config.json")).get("qwenModel")
    cmd = [lay.python, os.path.join(lay.app, "voice_cli.py"), "install", "qwen", "--yes"]
    if big and not had:
        cmd += ["--to", os.path.join(big, "qwen")]
    run(cmd, out, cwd=lay.data)
    run([lay.python, os.path.join(lay.app, "voice_cli.py"), "engine", "qwen"], out, cwd=lay.data)
    now = read_json(os.path.join(lay.data, "config.json")).get("qwenModel")
    return {"qwen": os.path.dirname(now)} if now and not had else {}


def _sac_on():
    """Smart App Control enforcing or evaluating: Studio's DLLs would be refused."""
    import qwen_engine

    return qwen_engine.smart_app_control() in ("on", "evaluation")


def install_breeze(lay, out, big, dry_run):
    """Through the installer the panel and `voice_cli install breeze` share, so
    the machine check and the folder cannot differ between the roads."""
    out.status(phase="engine", headline="Installing Breeze TTS 2", detail="about 11 GB")
    if dry_run:
        out.line("would install", "Breeze TTS 2 (voice_cli install breeze --yes)")
        return {}
    had = read_json(os.path.join(lay.data, "config.json")).get("breezeDir")
    cmd = [lay.python, os.path.join(lay.app, "voice_cli.py"), "install", "breeze", "--yes"]
    if big and not had:
        cmd += ["--to", os.path.join(big, "breeze")]
    run(cmd, out, cwd=lay.data)
    run([lay.python, os.path.join(lay.app, "voice_cli.py"), "engine", "breeze"], out, cwd=lay.data)
    now = read_json(os.path.join(lay.data, "config.json")).get("breezeDir")
    return {"breeze": os.path.dirname(now)} if now and not had else {}


# --------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------

def bash_word(path):
    """A path as bash reads it (voice_lib.bash_word): forward slashes, quoted
    when it holds a space -- a user name with a space in it is common."""
    path = path.replace("\\", "/")
    if all(c.isalnum() or c in "_./:+,=@-" for c in path):
        return path
    return '"' + path.replace("$", "\\$").replace("`", "\\`") + '"'


def wire_claude(lay, out, dry_run):
    """Hooks into the user's settings.json through voice_cli (one definition of
    what our hooks are, shared with `hooks remove`), and the /voice command.
    `hooks install` also cuts the block versions before 2.0 spliced into
    CLAUDE.md -- nothing writes a new one: the SessionStart hook says it all."""
    if dry_run:
        out.line("would write", f"hooks into {lay.settings}, /voice into {lay.command_md}")
        return
    run([lay.python, os.path.join(lay.app, "voice_cli.py"), "hooks", "install",
         "--python", lay.python, "--settings", lay.settings, "--claude-md", lay.claude_md], out,
        cwd=lay.data)
    template = os.path.join(lay.app, "templates", "voice.md")
    try:
        with open(template, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return
    text = text.replace("__PYTHON__", bash_word(lay.python)).replace("__REPO__", lay.app.replace("\\", "/"))
    os.makedirs(os.path.dirname(lay.command_md), exist_ok=True)
    with open(lay.command_md, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    out.line("slash command", lay.command_md)


# --------------------------------------------------------------------------
# registration: Settings -> Apps, the key games read, the Start menu, login
# --------------------------------------------------------------------------

def folder_kb(folder):
    total = 0
    for here, _, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(here, name))
            except OSError:
                pass
    return total // 1024


def uninstall_values(lay, version, size_kb):
    """The Settings -> Apps entry: signed pythonw running our uninstaller."""
    command = f'"{lay.pythonw}" "{os.path.join(lay.app, "installer.py")}" uninstall'
    return {
        "DisplayName": lay.product if lay.product != PRODUCT else "claude-voice",
        "DisplayVersion": version,
        "Publisher": PUBLISHER,
        "DisplayIcon": os.path.join(lay.app, "docs", "icons", "abby.ico"),
        "InstallLocation": lay.root,
        "UninstallString": command,
        "QuietUninstallString": command + " --quiet",
        "URLInfoAbout": URL,
        "HelpLink": URL + "/blob/main/docs/install.md",
        "InstallDate": time.strftime("%Y%m%d"),
        "EstimatedSize": int(size_kb),
        "NoModify": 1,
        "NoRepair": 1,
    }


def login_command(lay):
    return f'"{lay.pythonw}" "{os.path.join(lay.app, "panel.py")}"'


def drop_old_shortcuts(lay, keep, out):
    """Shortcuts an older setup made for a copy in this same folder (Desktop,
    Start menu, the panel's old Startup-folder one): they open code that is
    no longer there."""
    startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu",
                           "Programs", "Startup")
    for place in dict.fromkeys((lay.desktop, lay.start_menu, startup)):
        for leaf in (SHORTCUT_NAME + ".lnk", "claude-voice.lnk"):
            stale = os.path.join(place, leaf)
            if stale in keep:
                continue
            got = read_shortcut(stale)
            if got and mentions(got[0] + " " + got[1], lay.root) and not mentions(got[1], lay.app):
                _quiet_remove(stale)
                out.line("replaced", stale)


def register_everything(lay, out, options, dry_run):
    """Returns the shortcuts written, for the manifest."""
    version = own_version(lay.app) or own_version(HERE)
    if dry_run:
        out.line("would register", f"HKCU\\{lay.uninstall_key}, HKCU\\{lay.reg_key}, "
                 f"a Start menu shortcut{', a Desktop one' if options.get('desktop') else ''}"
                 f"{', start with Windows' if options.get('startup') else ''}")
        return []
    reg_write(lay.uninstall_key, uninstall_values(lay, version, folder_kb(lay.root)))
    out.line("settings apps", f"listed as {lay.product} (HKCU\\{lay.uninstall_key})")
    # The key games read first (docs/discovery.md). Written by the app itself, so
    # the contract has one writer; InstallDir is app\, Launcher the private pythonw.
    before = reg_read(lay.reg_key) or {}
    run([lay.python, os.path.join(lay.app, "voice_cli.py"), "register", "--kind", "installed",
         "--python", lay.pythonw], out, cwd=lay.data)
    if before.get("InstallDir") and not same_path(before["InstallDir"], lay.app):
        out.line("games", f"now find this install, not {before['InstallDir']} "
                 f"({before.get('Kind') or '?'}); `voice_cli.py register` there takes it back")
    links = []
    icon = os.path.join(lay.app, "docs", "icons", "abby.ico")
    places = ([lay.start_menu] if options.get("startMenu", True) else []) + \
             ([lay.desktop] if options.get("desktop") else [])
    for place in dict.fromkeys(places):
        path = os.path.join(place, lay.shortcut_leaf)
        try:
            make_shortcut(path, lay.pythonw, f'"{os.path.join(lay.app, "panel.py")}"', lay.data,
                          "Abby, and what she is saying -- the claude-voice panel", icon)
            links.append(path)
            out.line("shortcut", path)
        except OSError as exc:
            out.line("shortcut", f"could not write {path}: {exc}")
    if options.get("startup"):
        reg_write(RUN_KEY, {lay.run_value: login_command(lay)})
        out.line("at login", f"the panel opens (HKCU\\...\\Run\\{lay.run_value})")
    return links


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

def install(lay, source, out, engine=None, claude=True, big=None, port=None, options=None,
            dry_run=False, start=True, panel=True):
    options = dict(options or {})
    source = os.path.abspath(source)
    if not os.path.isfile(os.path.join(source, "voice_lib.py")):
        raise Refused(f"{source} is not a copy of claude-voice (no voice_lib.py in it).")
    if is_dev_checkout(lay.root) or inside(lay.root, source) and is_dev_checkout(source):
        raise Refused(f"{lay.root} is a developer's checkout; an install goes in a folder of its own.")
    prior = lay.manifest_doc()
    if not prior and not old_layout(lay.root, lay.product):
        try:
            foreign = [n for n in os.listdir(lay.root) if not _ours(n)]
        except OSError:
            foreign = []
        if foreign:
            # Somebody's own folder: an uninstall would later leave it alone,
            # but the program does not belong mixed in with it either.
            raise Refused(f"{lay.root} already holds other files ({', '.join(sorted(foreign)[:3])}); "
                          "an install goes in a folder of its own.")
    if prior and (prior.get("product") or PRODUCT) != lay.product:
        raise Refused(f"{lay.root} holds an install of {prior.get('product')}, not {lay.product}.")
    engine = engine or (prior.get("options") or {}).get("engine") or _engine_before(lay) or "pocket"
    out.doc["engine"] = engine
    out.doc["data"] = big or ""
    out.status(phase="app", headline="Installing the voice app", detail=lay.root)
    out.line("program", lay.root)
    out.line("your data", lay.data)
    out.line("engine", engine)
    out.check_cancel()

    prepare_runtime(lay, out, dry_run)
    claims = dict(prior.get("claims") or {})
    copied = None
    if not dry_run and inside(source, lay.root) and not inside(source, lay.app):
        # An older install's own files are the source (its setup.ps1 was run):
        # copied out first, since moving that install aside deletes them.
        copy = os.path.join(os.environ.get("TEMP") or lay.notes, f"{lay.product}-source-{os.getpid()}")
        copy_app(source, copy)
        source = copied = copy
    if old_layout(lay.root, lay.product):
        if not dry_run:
            # Its engine and panel ran on the system's Python: they hold the
            # port and its logs open, and nothing below would stop them.
            stop_everything(lay, out, scripts=[lay.root])
        claims.update(migrate_old_layout(lay, out, dry_run, source))
    sweep_parked(lay)

    # The program: copied whole beside the live one, compiled to prove every
    # file parses on this Python, and only then swapped in.
    if not dry_run:
        os.makedirs(lay.data, exist_ok=True)
        os.chdir(lay.data)               # never stand in the folder about to be swapped
        staged = lay.app + ".new"
        n = copy_app(source, staged)
        run([lay.python, "-m", "compileall", "-q", staged], out)
        guard_against_old_setup(staged)
        out.line("app", f"{n} files from {source}")
        if os.path.isdir(lay.app):
            # The panel, when it was open and not the one running this (an
            # update from the CLI or the game), is opened again at the end.
            panel = stop_everything(lay, out, [claims.get("breeze"), claims.get("qwen")]) or panel
        swap_in(staged, lay.app)
        write_json(os.path.join(lay.app, "installed.json"),
                   {"root": lay.app, "kind": "installed", "program": lay.root, "data": big or "",
                    "note": "Written by installer.py; the full record is ..\\install.json."})
    else:
        out.line("would copy", f"{source} -> {lay.app}")
    out.check_cancel()

    # The manifest goes down before anything outside the folder is touched, so
    # an install that stops half way can still be uninstalled.
    manifest = {"layout": LAYOUT, "product": lay.product, "root": lay.root, "data": lay.data,
                "shortcutDir": options.get("shortcutDir"),
                "options": {"engine": engine, "claude": claude, "big": big or "",
                            "desktop": bool(options.get("desktop")),
                            "startMenu": options.get("startMenu", True),
                            "startup": bool(options.get("startup"))},
                # A game's install says --no-claude; Claude Code wired by an
                # earlier install stays on record, or its uninstall would leave
                # the hooks behind.
                "claude": {"dir": lay.claude_dir} if claude else prior.get("claude"),
                "claims": claims, "shortcuts": prior.get("shortcuts") or [],
                "installed": prior.get("installed") or time.strftime("%Y-%m-%d %H:%M"),
                "updated": time.strftime("%Y-%m-%d %H:%M"),
                "version": own_version(source),
                "runtime": read_json(os.path.join(lay.runtime, "claude-voice-runtime.json")).get("version")}
    if not dry_run:
        write_json(lay.manifest, manifest)
        # Listed in Settings -> Apps at once: whatever stops this install from
        # here on, it can be taken off the way any program is.
        reg_write(lay.uninstall_key, uninstall_values(lay, manifest["version"], folder_kb(lay.root)))
    if copied:
        _rmtree(copied, ignore_errors=True)      # app\ has it all now

    studio = None
    if engine == "pocket":
        install_pocket(lay, out, dry_run, claims)
    elif engine == "qwen" and not _sac_on():
        got, studio = install_qwen(lay, out, big, dry_run)
        claims.update(got)
    # Qwen with Smart App Control on is Qwen on PyTorch: Studio's unsigned DLLs
    # would only be refused. Pocket is set first, so the voice speaks while the
    # 7 GB arrive and if they never do.
    torch_qwen = engine == "qwen" and _sac_on()
    write_config(lay, out, "pocket" if torch_qwen else engine, port, studio, dry_run)
    if engine == "breeze":
        claims.update(install_breeze(lay, out, big, dry_run))
    if torch_qwen:
        claims.update(install_qwen_torch(lay, out, big, dry_run))
    out.check_cancel()

    if claude:
        try:
            wire_claude(lay, out, dry_run)
        except (Refused, OSError, ValueError) as exc:
            # A settings.json or CLAUDE.md this cannot read is the user's to
            # fix; the voice itself is installed all the same.
            out.line("claude code", f"not connected: {exc}")
            out.say("                fix that, then run: "
                    f"\"{lay.python}\" \"{os.path.join(lay.app, 'voice_cli.py')}\" hooks install")
    else:
        out.line("claude code", "left alone (--no-claude)")
    manifest["shortcuts"] = register_everything(lay, out, manifest["options"], dry_run) or manifest["shortcuts"]
    manifest["claims"] = claims
    if not dry_run:
        write_json(lay.manifest, manifest)
        drop_old_shortcuts(lay, manifest["shortcuts"], out)

    if dry_run:
        out.say("")
        out.say("--dry-run: nothing was written.")
        return manifest
    out.status(phase="start", headline="Starting the voice", final=True)
    if start:
        health = _health(_port_of(lay))
        theirs = health and health.get("root") and not inside(health["root"], lay.root)
        if theirs:
            out.line("engine", f"another copy ({health['root']}) answers on port {_port_of(lay)}; "
                     "stop it (voice_cli.py kill there) and this one starts on the next use")
        else:
            run([lay.python, os.path.join(lay.app, "voice_cli.py"), "start"], out,
                cwd=lay.data, check=False)
            # Said plainly rather than left for the first silent answer to reveal.
            health = _health(_port_of(lay), timeout=3) or {}
            if health.get("error"):
                out.line("engine", f"is up, but could not load its voice: {health['error']}")
                if "Application Control" in health["error"]:
                    out.say("                Smart App Control refused one of its files. The details are in "
                            "Event Viewer > Applications and Services > Microsoft > Windows > CodeIntegrity.")
            elif health.get("ready"):
                out.line("engine", f"ready on port {health.get('port')}")
    if panel:
        subprocess.Popen([lay.pythonw, os.path.join(lay.app, "panel.py")], cwd=lay.data,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out.status(state="done", headline="Installed", fraction=1.0, phaseFraction=1.0)
    out.say("")
    out.say("Installed. " + ("Restart Claude Code -- hooks are read when a session starts. Then: /voice on"
                             if claude else "The voice is ready for programs that use it."))
    return manifest


# --------------------------------------------------------------------------
# uninstall
# --------------------------------------------------------------------------

def _hf_pocket_weights():
    hub = os.path.join(os.environ["HF_HOME"], "hub") if os.environ.get("HF_HOME") else \
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    try:
        return [os.path.join(hub, d) for d in os.listdir(hub) if d.startswith("models--kyutai--pocket")]
    except OSError:
        return []


def user_data(lay):
    """What in the data folder is somebody's own work rather than the program's
    working files: their settings and the voices they made."""
    found = []
    cfg = os.path.join(lay.data, "config.json")
    if os.path.isfile(cfg):
        found.append(cfg)
    voices = os.path.join(lay.data, "voices")
    if os.path.isdir(voices) and os.listdir(voices):
        found.append(voices)
    return found


def plan_uninstall(lay, doc):
    """[(what, action)] -- everything this install put on the machine, each
    checked to still be ours before it is listed."""
    plan = []
    claude = doc.get("claude")
    if claude:
        plan.append((f"its Claude Code hooks in {lay.settings} (and any old note in {lay.claude_md})",
                     lambda: subprocess.run([lay.python, os.path.join(lay.app, "voice_cli.py"), "hooks",
                                             "remove", "--mine", "--settings", lay.settings,
                                             "--claude-md", lay.claude_md],
                                            cwd=os.environ.get("TEMP"), capture_output=True,
                                            creationflags=_no_console_flags())))
        try:
            with open(lay.command_md, encoding="utf-8") as fh:
                mine = mentions(fh.read(), lay.app)
        except OSError:
            mine = False
        if mine:
            plan.append((f"the /voice command: {lay.command_md}", lambda: os.remove(lay.command_md)))
    for link in doc.get("shortcuts") or []:
        got = read_shortcut(link)
        if got and inside(got[0], lay.root):
            plan.append((f"the shortcut: {link}", lambda p=link: os.remove(p)))
    run_cmd = reg_read(RUN_KEY, lay.run_value)
    if run_cmd and mentions(str(run_cmd), lay.root):
        plan.append(("starting with Windows", lambda: reg_delete_value(RUN_KEY, lay.run_value)))
    found = reg_read(lay.reg_key) or {}
    if found.get("InstallDir") and inside(found["InstallDir"], lay.root):
        plan.append((f"the registry key games find it by: HKCU\\{lay.reg_key}",
                     lambda: reg_delete_key(lay.reg_key)))
    where = os.path.join(lay.notes, "where.json")
    if inside(read_json(where).get("root"), lay.root):
        plan.append((f"the note games find it by: {where}", lambda: os.remove(where)))
    claims = doc.get("claims") or {}
    if claims.get("breeze") and os.path.isdir(claims["breeze"]):
        plan.append((f"Breeze, the engine that laughs (about 11 GB): {claims['breeze']}",
                     lambda: _rmtree(claims["breeze"], ignore_errors=True)))
    if claims.get("qwen") and os.path.isdir(claims["qwen"]):
        plan.append((f"Qwen on PyTorch (about 10 GB): {claims['qwen']}",
                     lambda: _rmtree(claims["qwen"], ignore_errors=True)))
    if claims.get("studio") and _studio_ok(claims["studio"]):
        plan.append((f"Qwen-TTS Studio: {claims['studio']}",
                     lambda: _rmtree(claims["studio"], ignore_errors=True)))
    if claims.get("models"):
        files = [os.path.join(claims["models"], m) for m in QWEN_MODELS
                 if os.path.isfile(os.path.join(claims["models"], m))]
        if files:
            plan.append((f"the Qwen voice model (about 2 GB): {claims['models']}",
                         lambda: [os.remove(f) for f in files]))
    if claims.get("pocketWeights"):
        for w in _hf_pocket_weights():
            plan.append((f"Pocket's voice model: {w}", lambda p=w: _rmtree(p, ignore_errors=True)))
    if reg_read(lay.uninstall_key) is not None:
        plan.append((f"its entry in Settings > Apps", lambda: reg_delete_key(lay.uninstall_key)))
    plan.append((f"the program itself: {lay.root}", None))
    return plan


def park_runtime(lay, out):
    """Delete the program folder, the Python this is running on included.

    A running python.exe and the DLLs it loaded cannot be deleted, but the
    folder holding them can be renamed -- once this process stands somewhere
    else. So: move runtime\\ into %TEMP% as one rename, delete everything that
    lets go (all but a handful of files, a few MB), and the next install sweeps
    the rest (sweep_parked). No helper process, no cmd /c rmdir, no reboot.
    """
    os.chdir(os.environ.get("TEMP") or os.path.expanduser("~"))
    parked = None
    if os.path.isdir(lay.runtime):
        target = os.path.join(os.environ.get("TEMP") or os.path.dirname(lay.root),
                              f"{lay.product}-removed-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            os.rename(lay.runtime, target)
            parked = target
        except OSError as exc:
            out.say(f"could not move the runtime aside ({exc}); deleting what lets go")
    # Only what an install puts there: `--root D:\Tools` must not take D:\Tools
    # and everything else in it along.
    for path in our_children(lay.root):
        _rmtree(path, ignore_errors=True) if os.path.isdir(path) else _quiet_remove(path)
    try:
        os.rmdir(lay.root)
    except OSError:
        pass
    if parked:
        _rmtree(parked, ignore_errors=True)
    return parked, not our_children(lay.root)


def _ours(name):
    name = name.lower()
    return (name in ("app", "runtime", "install.json")
            or name.startswith("app.") or name.startswith("runtime."))


def our_children(root):
    """The entries of a program folder an install made: app\\, runtime\\, their
    staged and parked siblings (app.new, runtime.old, ...) and install.json."""
    try:
        return [os.path.join(root, n) for n in os.listdir(root) if _ours(n)]
    except OSError:
        return []


def _ask(question, quiet, default):
    """Yes or no: from a console when there is one (python.exe), else a window
    (pythonw.exe, which is what Settings -> Apps runs)."""
    if quiet:
        return default
    if sys.stdin is not None and sys.stdin.isatty():
        answer = input(question + (" [Y/n] " if default else " [y/N] ")).strip().lower()
        return default if not answer else answer.startswith("y")
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        try:
            return messagebox.askyesno("claude-voice", question, default="yes" if default else "no")
        finally:
            root.destroy()
    except Exception:
        return default


def _tell(text, quiet):
    if quiet:
        return
    if sys.stdout is not None and sys.stdin is not None and sys.stdin.isatty():
        print(text)
        return
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo("claude-voice", text)
        root.destroy()
    except Exception:
        pass


def uninstall(lay, out, quiet=False, remove_data=None, dry_run=False):
    doc = lay.manifest_doc()
    if not doc:
        raise Refused(f"{lay.root} has no install.json; nothing here was installed by this installer.")
    if is_dev_checkout(lay.root) or is_dev_checkout(lay.app):
        raise Refused(f"{lay.root} is a developer's checkout, which is never uninstalled.")
    plan = plan_uninstall(lay, doc)
    mine = user_data(lay)
    listing = "\n".join("  - " + what for what, _ in plan)
    if dry_run:
        out.say("Would remove:\n" + listing)
        if mine:
            out.say("Would ask about (kept unless you say otherwise):\n" + "\n".join("  - " + m for m in mine))
        return plan
    if not _ask(f"Remove claude-voice from this computer?\n\nThis takes away:\n{listing}\n\n"
                "Nothing that was here before claude-voice is touched.", quiet, True):
        return None
    if remove_data is None:
        remove_data = bool(mine) and _ask(
            "Also delete your own settings and voices?\n\n" + "\n".join("  - " + m for m in mine) +
            "\n\nKept, they are used again if claude-voice is ever installed again.", quiet, False)
    stop_everything(lay, out, [(doc.get("claims") or {}).get("breeze"),
                               (doc.get("claims") or {}).get("qwen")])
    failed = []
    for what, act in plan:
        if act is None:
            continue
        out.say(f"removing: {what}")
        try:
            act()
        except Exception as exc:
            failed.append(f"{what}: {exc}")
    parked, gone = park_runtime(lay, out)
    if not gone:
        failed.append(f"some of {lay.root} (in use?)")
    out.say("removed" + (f"; a few MB of the running Python wait in {parked}"
                         if parked and os.path.exists(parked) else ""))
    # The data folder, by name only: it is also where a Breeze from `voice_cli
    # install breeze` lands by default, and where a checkout's engine leaves its
    # notes, so nothing is swept that this list does not name.
    names = ["logs", "setup-status.json", "setup-cancel", "setup.log", "config.json.lock",
             "qwen-blocked.json"]
    if remove_data:
        names += ["config.json", "voices"]
    for folder in dict.fromkeys((lay.data, lay.notes)):
        for name in names:
            path = os.path.join(folder, name)
            _rmtree(path, ignore_errors=True) if os.path.isdir(path) else _quiet_remove(path)
        try:
            os.rmdir(folder)           # only when nothing else is in it
        except OSError:
            pass
    # Big folders this install did not fetch itself (a Breeze from `voice_cli
    # install breeze` lands in the data folder): left, but said, so gigabytes
    # are not forgotten on the disk.
    claimed = (doc.get("claims") or {}).get("breeze")
    big = [p for p in (os.path.join(lay.data, "breeze"),)
           if os.path.isdir(p) and not (claimed and same_path(p, claimed))]
    left = ("\n\nNot removed, as this install did not put it there: " + ", ".join(big)) if big else ""
    if failed:
        _tell("claude-voice is removed, except for:\n\n" + "\n".join(failed) + left, quiet)
    else:
        _tell("claude-voice is removed." + ("" if remove_data or not mine else
              f"\n\nYour settings and voices are kept in {lay.data}.") + left, quiet)
    return plan


def _quiet_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# update: the new code staged beside the old, swapped in, everything else kept
# --------------------------------------------------------------------------

def fetch_source(url, out):
    """GitHub's zip of a branch, unpacked into a fresh temp folder. Returns the
    folder inside it that holds voice_lib.py."""
    work = os.path.join(os.environ.get("TEMP") or _local(), f"{PRODUCT}-update-{os.getpid()}")
    _rmtree(work, ignore_errors=True)
    os.makedirs(work)
    pkg = download(url, os.path.join(work, "source.zip"), out, "download")
    with zipfile.ZipFile(pkg) as z:
        z.extractall(os.path.join(work, "src"))
    for here, dirs, files in os.walk(os.path.join(work, "src")):
        if "voice_lib.py" in files and "installer.py" in files:
            return here
    raise Refused("the download holds no installer.py -- that is not a release of this.")


def update(lay, source, out, dry_run=False):
    """Install `source` over this install with the options it was made with.
    A release that wants another Python says so rather than half-doing it: the
    runtime this runs on cannot be swapped by itself."""
    doc = lay.manifest_doc()
    if not doc:
        raise Refused(f"{lay.root} was not installed by this installer.")
    pin = read_json(os.path.join(source, "runtime.json"))
    have = read_json(os.path.join(lay.runtime, "claude-voice-runtime.json"))
    if pin.get("sha256") and have.get("sha256") and pin["sha256"] != have["sha256"]:
        raise Refused(f"this release runs on Python {pin.get('version')}, and this install has "
                      f"{have.get('version')}. Run install.ps1 from the new release; it keeps your settings.")
    opts = doc.get("options") or {}
    was_running = bool(_health(_port_of(lay)))
    return install(lay, source, out, engine=opts.get("engine"), claude=bool(doc.get("claude")),
                   big=opts.get("big") or None, options={**opts, "shortcutDir": doc.get("shortcutDir")},
                   dry_run=dry_run, start=was_running, panel=False)


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="installer.py", description="Install, update or remove claude-voice.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("install", "uninstall", "update", "stop", "status"):
        p = sub.add_parser(name)
        p.add_argument("--root", help="the program folder (default %%LOCALAPPDATA%%\\Programs\\claude-voice; "
                                      "for uninstall/update/stop, the one this file is in)")
        p.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
        p.add_argument("--quiet", action="store_true", help="no questions, no windows (a game's road)")
        if name in ("install", "update"):
            p.add_argument("--source", help="a folder holding this project (default: this file's own)")
        if name == "install":
            p.add_argument("--product", default=PRODUCT, help=argparse.SUPPRESS)
            p.add_argument("--data", help="your settings, logs and voices (default %%LOCALAPPDATA%%\\claude-voice)")
            p.add_argument("--engine", choices=ENGINES)
            p.add_argument("--no-claude", action="store_true", help="touch nothing of Claude Code's")
            p.add_argument("--claude-dir", help="Claude Code's folder (default ~\\.claude)")
            p.add_argument("--big-data", help="one folder for the big engine files (Studio, model, Breeze)")
            p.add_argument("--desktop", action="store_true", help="a Desktop shortcut as well")
            p.add_argument("--no-start-menu", action="store_true")
            p.add_argument("--startup", action="store_true", help="open the panel when you log in")
            p.add_argument("--shortcut-dir", help=argparse.SUPPRESS)
            p.add_argument("--port", type=int, help=argparse.SUPPRESS)
            p.add_argument("--no-start", action="store_true", help="do not start the engine at the end")
            p.add_argument("--no-panel", action="store_true", help="do not open the panel at the end")
            p.add_argument("--for", dest="for_", help=argparse.SUPPRESS)
        if name == "uninstall":
            g = p.add_mutually_exclusive_group()
            g.add_argument("--remove-data", action="store_true", help="delete your settings and voices too")
            g.add_argument("--keep-data", action="store_true", help="keep them (the default when --quiet)")
    a = ap.parse_args(argv)

    if a.cmd == "install":
        lay = Layout(a.root, a.product, a.data, a.claude_dir, a.shortcut_dir)
    else:
        root = a.root or os.path.dirname(HERE)
        try:
            lay = Layout.of_installed(root)
        except Refused as exc:
            print(exc)
            return 1

    if a.cmd == "uninstall":
        # Its own log outside everything it is about to delete, so a removal
        # that went wrong can still be read afterwards.
        lay.log_path = os.path.join(os.environ.get("TEMP") or _local(), f"{lay.product}-uninstall.log")
    out = Out(lay, quiet=a.quiet, dry_run=a.dry_run, status=a.cmd in ("install", "update"))
    if a.cmd in ("install", "update") and not a.dry_run:
        running = read_json(lay.status_path)
        if running.get("state") == "running" and running.get("pid") not in (None, os.getpid()) \
                and any(pid == running["pid"] for pid, _, _ in processes()):
            print("Another install is already running.")
            return 2
        _quiet_remove(lay.cancel_path)
    try:
        if a.cmd == "install":
            install(lay, a.source or HERE, out, engine=a.engine, claude=not a.no_claude,
                    big=a.big_data, port=a.port, dry_run=a.dry_run,
                    options={"desktop": a.desktop, "startMenu": not a.no_start_menu,
                             "startup": a.startup, "shortcutDir": a.shortcut_dir},
                    start=not a.no_start, panel=not (a.no_panel or a.quiet))
        elif a.cmd == "update":
            update(lay, a.source or fetch_source(ZIP_URL, out), out, dry_run=a.dry_run)
        elif a.cmd == "uninstall":
            remove = True if a.remove_data else (False if a.keep_data or a.quiet else None)
            uninstall(lay, out, quiet=a.quiet, remove_data=remove, dry_run=a.dry_run)
        elif a.cmd == "stop":
            stop_everything(lay, out, [(lay.manifest_doc().get("claims") or {}).get("breeze")])
        elif a.cmd == "status":
            print(json.dumps(lay.manifest_doc(), indent=2))
        return 0
    except Cancelled:
        out.status(state="cancelled", headline="Stopped", error="")
        out.say("Stopped. Running it again carries on from where it got to.")
        return 3
    except (Refused, OSError, subprocess.SubprocessError) as exc:
        out.status(state="failed", headline="The install stopped", error=str(exc))
        out.say(f"\nStopped: {exc}")
        if a.cmd == "uninstall":
            _tell(f"claude-voice could not be removed:\n\n{exc}", a.quiet)
        return 1


if __name__ == "__main__":
    sys.exit(main())
