"""
Voice packs: Pocket TTS states for a whole library of voices, fetched once.

Why this exists. A program with a hundred voices of its own -- the Immersive AI
mod is the first -- registers its folder through POST /voice-roots, and each
voice there is a clip. Pocket can speak a clip only by cloning it, and cloning
needs weights Kyutai gate behind a Hugging Face sign-in: fine for one person,
not something to ask of every player. A baked state (pocket.safetensors) needs
no sign-in at all. But a hundred of them are 200 MB, too big to ship inside the
program that owns the voices, and that program's own deploy wipes its folder.

So the voice root carries a small `pocket-pack.json` saying where a zip of the
baked states can be had, and this module fetches it into the app's own data
folder, checks it, and lays it out where the catalogue can find it. The voice
folder itself is never written to. docs/voice-packs.md is the whole contract.

A pack is used only when the weights it was baked against are the weights this
copy loads. A state from another release is not an error -- it is garbage
audio, said confidently -- so a mismatch is refused and logged, never tried.

Nothing here blocks speech. Fetching runs on a thread of its own; the voices
appear in the catalogue when it is done, and until then they are what they
were before (clip-only, so absent where Pocket cannot clone).
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile

import voice_lib

PACK_FILE = "pocket-pack.json"
SCHEMA = "claude-voice/pocket-pack"
SCHEMA_VERSION = 1
# What a pack's own folder holds once it is in place, written last: a copy of
# the description it was fetched for. Its sha256 matching the root's
# pocket-pack.json is what "present and verified" means.
MARKER = "pack.json"
STATE_FILE = "pocket.safetensors"

# How long a background fetch waits before trying the same pack again. Offline
# is usually over in minutes; a zip that does not match its own checksum will
# not fix itself, so it waits for a restart or someone asking.
RETRY_OFFLINE = 600.0
RETRY_FAILED = 3600.0

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HEX = re.compile(r"^[0-9a-f]+$")


class PackError(ValueError):
    """A pocket-pack.json that cannot be used as it is written."""


def _unlogged(msg):
    pass


# The server points this at its own log; the CLI at print.
log = _unlogged


def packs_dir():
    """Where packs are kept: the app's own data folder, never a voice root.

    %LOCALAPPDATA%\\claude-voice\\pocket-packs for an installed copy, which an
    update leaves alone; beside the code in a checkout (ignored by git), like
    its logs. CLAUDE_VOICE_PACKS_DIR moves it, for tests and second instances.
    """
    return os.environ.get("CLAUDE_VOICE_PACKS_DIR") or os.path.join(voice_lib.DATA_DIR, "pocket-packs")


# --------------------------------------------------------------------------
# the description
# --------------------------------------------------------------------------

def parse(doc):
    """A pocket-pack.json's contents, checked and normalised. Raises PackError."""
    if not isinstance(doc, dict):
        raise PackError("not a JSON object")
    if doc.get("schema") != SCHEMA:
        raise PackError(f"schema is {doc.get('schema')!r}, not {SCHEMA!r}")
    if doc.get("version") != SCHEMA_VERSION:
        raise PackError(f"version {doc.get('version')!r}; this copy reads version {SCHEMA_VERSION}")
    name = doc.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise PackError(f"name {name!r}: letters, digits, '.', '_' and '-', up to 64")
    url = doc.get("url")
    if not isinstance(url, str) or not url.lower().startswith(("https://", "http://", "file:")):
        raise PackError(f"url {url!r} is not http(s) or file")
    sha = str(doc.get("sha256") or "").lower()
    if len(sha) != 64 or not _HEX.match(sha):
        raise PackError("sha256 must be 64 hex digits")
    size = doc.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise PackError("bytes must be a positive whole number")
    w = doc.get("weights")
    if not isinstance(w, dict):
        raise PackError("weights is missing")
    repo, rev = w.get("repo"), str(w.get("revision") or "").lower()
    lang = str(w.get("language") or "").strip().lower()
    if not isinstance(repo, str) or repo.count("/") != 1:
        raise PackError(f"weights.repo {repo!r} is not owner/name")
    if len(rev) != 40 or not _HEX.match(rev):
        raise PackError("weights.revision must be the full 40-digit commit")
    if not lang:
        raise PackError("weights.language is missing")
    voices = doc.get("voices")
    if not isinstance(voices, list) or not voices:
        raise PackError("voices must be a non-empty list")
    clean = []
    for v in voices:
        rel = _rel(v)
        if rel is None:
            raise PackError(f"voice {v!r} is not a relative path inside the root")
        clean.append(rel)
    if len(set(c.lower() for c in clean)) != len(clean):
        raise PackError("a voice is listed twice")
    return {"schema": SCHEMA, "version": SCHEMA_VERSION, "name": name, "url": url,
            "sha256": sha, "bytes": size,
            "weights": {"repo": repo, "revision": rev, "language": lang},
            "voices": clean}


def _rel(path):
    """'female/aserai/x' from anything spelled like it, or None if it climbs out."""
    if not isinstance(path, str):
        return None
    p = path.replace("\\", "/").strip().strip("/")
    parts = p.split("/")
    if not p or ":" in p or any(s in ("", ".", "..") for s in parts):
        return None
    return "/".join(parts)


def read(root):
    """The root's pack, parsed, or None when it has none. Raises PackError."""
    path = os.path.join(root, PACK_FILE)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise PackError(f"cannot read {path}: {exc}")
    return parse(doc)


def where(pack):
    """The folder this pack is unpacked into: <packs>\\<name>\\<revision, 12 digits>.

    Twelve, not forty: a library's own folders are deep already
    (female\\battania\\battanian_female_earnest\\pocket.safetensors), and the
    full hash pushed a pack under a long data folder past Windows' 260
    characters -- found the first time one was unpacked in a deep temp folder.
    """
    return os.path.join(packs_dir(), pack["name"], pack["weights"]["revision"][:12])


def is_ready(pack):
    """In place, and the one this description asks for (same zip checksum)."""
    try:
        with open(os.path.join(where(pack), MARKER), encoding="utf-8") as fh:
            return json.load(fh).get("sha256") == pack["sha256"]
    except (OSError, ValueError, AttributeError):
        return False


_WEIGHTS = {}


def _weights_of(language):
    """pocket_engine.weights_of, held for the process: the catalogue asks on
    every line spoken, and the installed package does not change under it."""
    if language not in _WEIGHTS:
        import pocket_engine
        found = pocket_engine.weights_of(language)
        if not found:
            return found                 # not installed yet: ask again next time
        _WEIGHTS[language] = found
    return _WEIGHTS[language]


def weights_match(pack):
    """(True|False|None, why). None: cannot tell, because pocket-tts is not here."""
    w = pack["weights"]
    loads = _weights_of(w["language"])
    if not loads:
        return None, f"pocket-tts is not installed, or has no {w['language']} model"
    if any(repo == w["repo"] and rev == w["revision"] for repo, rev in loads):
        return True, ""
    have = ", ".join(f"{r}@{v[:7]}" for r, v in loads)
    return False, (f"baked on {w['repo']}@{w['revision'][:7]}, but this copy's pocket-tts "
                   f"loads {have} for {w['language']} -- its voices would speak garbage")


# --------------------------------------------------------------------------
# what the catalogue asks
# --------------------------------------------------------------------------

_SAID = set()           # (name, revision, what) logged once a process
_STATUS = {}            # pack key -> {"state": ..., "detail": ..., "when": ...}
_LOCK = threading.Lock()
_RUN = {"busy": False, "again": False}


def _key(root, pack):
    return (os.path.normcase(os.path.abspath(root)), pack["name"], pack["sha256"])


def _say_once(pack, what, msg):
    k = (pack["name"], pack["weights"]["revision"], what)
    if k not in _SAID:
        _SAID.add(k)
        log(msg)


def overlay(state=None):
    """{normcase(voice folder): (pocket.safetensors, language)} for every ready pack.

    What voice_lib.catalog lays over the voices it found: a voice whose folder
    a usable pack covers is Pocket-capable through the pack's file. A pack in
    place but baked against other weights is left out, and said so once.
    """
    out = {}
    for root in voice_lib.voice_roots(state):
        try:
            pack = read(root)
        except PackError as exc:
            log(f"voice pack in {root} ignored: {exc}")
            continue
        if pack is None or not is_ready(pack):
            continue
        ok, why = weights_match(pack)
        if not ok:
            _say_once(pack, "mismatch", f"voice pack {pack['name']} not used: {why}")
            continue
        base = where(pack)
        for rel in pack["voices"]:
            f = os.path.join(base, *rel.split("/"), STATE_FILE)
            if os.path.isfile(f):
                out[os.path.normcase(os.path.join(os.path.abspath(root), *rel.split("/")))] = \
                    (f, pack["weights"]["language"])
    return out


def status(state=None):
    """Every registered root's pack and how it stands -- what /voice-packs says.

    state is one of: ready, downloading, absent (not fetched yet), offline,
    failed, mismatch (baked on other weights), waiting (pocket-tts is not
    installed, so whether it fits cannot be told), invalid (the description).
    """
    rows = []
    for root in voice_lib.voice_roots(state):
        try:
            pack = read(root)
        except PackError as exc:
            rows.append({"root": root, "state": "invalid", "detail": str(exc)})
            continue
        if pack is None:
            continue
        row = {"root": root, "name": pack["name"], "url": pack["url"], "bytes": pack["bytes"],
               "revision": pack["weights"]["revision"], "language": pack["weights"]["language"],
               "voices": len(pack["voices"]), "dir": where(pack)}
        ok, why = weights_match(pack)
        if ok is False:
            row.update(state="mismatch", detail=why)
        elif is_ready(pack):
            row.update(state="ready", detail="")
        else:
            seen = _STATUS.get(_key(root, pack)) or {}
            if seen.get("state") == "ready":         # it was, but is no longer on disk
                seen = {}
            row.update(state=seen.get("state") or ("waiting" if ok is None else "absent"),
                       detail=seen.get("detail") or (why if ok is None else ""))
            if "done" in seen:
                row["done"] = seen["done"]
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def _note(root, pack, state, detail="", **more):
    _STATUS[_key(root, pack)] = {"state": state, "detail": detail, "when": time.time(), **more}


def _download(url, part, expect, progress=None):
    """Stream url to part, resuming one left by an earlier try. Returns its sha256."""
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > expect:
        os.remove(part)
        have = 0
    req = urllib.request.Request(url, headers={"User-Agent": "claude-voice"})
    if have and not url.lower().startswith("file:"):
        req.add_header("Range", f"bytes={have}-")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have == expect:
            resp = None
        else:
            raise
    if resp is not None:
        with resp:
            if have and getattr(resp, "status", 200) != 206:
                have = 0                      # no Range here: start again
            with open(part, "ab" if have else "wb") as fh:
                done, last = have, 0.0
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    if done > expect:
                        raise PackError(f"the download is bigger than the {expect} bytes it should be")
                    if progress and time.monotonic() - last > 1:
                        last = time.monotonic()
                        progress(done)
    h = hashlib.sha256()
    with open(part, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _unpack(zpath, pack, dest):
    """Each listed voice's pocket.safetensors out of the zip into dest. Nothing
    else is taken, and every name is checked to stay inside dest."""
    wanted = {f"{rel}/{STATE_FILE}".lower(): rel for rel in pack["voices"]}
    got = set()
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = _rel(info.filename)
            if info.is_dir() or name is None or name.lower() not in wanted:
                continue
            rel = wanted[name.lower()]
            target = os.path.join(dest, *rel.split("/"), STATE_FILE)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            got.add(rel)
    missing = [rel for rel in pack["voices"] if rel not in got]
    if missing:
        raise PackError(f"the zip lacks {len(missing)} of its {len(pack['voices'])} voices, "
                        f"first {missing[0]}/{STATE_FILE}")


def fetch(root, pack, progress=None):
    """Bring one pack into place. Returns the state it ends in (see status).

    Never raises for the expected troubles -- offline, a bad checksum, other
    weights -- because it runs where nobody is waiting on it; it says what
    happened in the log and in the status instead.
    """
    if is_ready(pack):
        _note(root, pack, "ready")
        return "ready"
    ok, why = weights_match(pack)
    if ok is False:
        _say_once(pack, "mismatch", f"voice pack {pack['name']} not fetched: {why}")
        _note(root, pack, "mismatch", why)
        return "mismatch"
    if ok is None:
        _note(root, pack, "waiting", why)
        return "waiting"

    final = where(pack)
    parent = os.path.dirname(final)
    os.makedirs(parent, exist_ok=True)
    rev = pack["weights"]["revision"][:12]
    part = os.path.join(parent, f"{rev}.{pack['sha256'][:12]}.zip.part")
    log(f"voice pack {pack['name']}: fetching {pack['bytes'] / 1e6:.0f} MB from {pack['url']}")
    _note(root, pack, "downloading", "", done=0)

    def moved(n):
        _note(root, pack, "downloading", f"{n / 1e6:.0f} of {pack['bytes'] / 1e6:.0f} MB", done=n)
        if progress:
            progress(n, pack["bytes"])

    try:
        got = _download(pack["url"], part, pack["bytes"], moved)
    except urllib.error.HTTPError as exc:
        msg = f"{pack['url']} answered {exc.code}"
        log(f"voice pack {pack['name']}: {msg}")
        _note(root, pack, "failed", msg)
        return "failed"
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        reason = getattr(exc, "reason", exc)
        log(f"voice pack {pack['name']}: offline? {reason} -- trying again later")
        _note(root, pack, "offline", str(reason))
        return "offline"
    except (OSError, PackError) as exc:
        log(f"voice pack {pack['name']}: download failed: {exc}")
        _note(root, pack, "failed", str(exc))
        _drop(part)
        return "failed"
    size = os.path.getsize(part)
    if size != pack["bytes"] or got != pack["sha256"]:
        why = (f"got {size} bytes with sha256 {got[:12]}..., expected {pack['bytes']} "
               f"with {pack['sha256'][:12]}... -- not used")
        log(f"voice pack {pack['name']}: {why}")
        _note(root, pack, "failed", why)
        _drop(part)
        return "failed"

    # Unpacked beside its final place and renamed in, so the catalogue sees
    # either the whole old pack, or the whole new one, never half of either.
    tmp = os.path.join(parent, f".new-{os.getpid()}")
    old = os.path.join(parent, f".old-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _unpack(part, pack, tmp)
        with open(os.path.join(tmp, MARKER), "w", encoding="utf-8") as fh:
            json.dump({**pack, "root": root,
                       "installedUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                      fh, indent=2)
        if os.path.exists(final):
            os.replace(final, old)
        os.replace(tmp, final)
    except (OSError, PackError, zipfile.BadZipFile) as exc:
        log(f"voice pack {pack['name']}: could not unpack: {exc}")
        _note(root, pack, "failed", f"could not unpack: {exc}")
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.exists(old) and not os.path.exists(final):
            os.replace(old, final)
        return "failed"
    shutil.rmtree(old, ignore_errors=True)
    _drop(part)
    log(f"voice pack {pack['name']}: {len(pack['voices'])} voices ready in {final}")
    _note(root, pack, "ready")
    return "ready"


def _drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


def fetch_all(state=None, force=False, progress=None):
    """Every registered root's pack, one after another. [(name, state)].

    Without force, a pack that failed or found no network a short while ago is
    left until its wait is up, so a server asked again and again does not
    hammer a host that is down.
    """
    done = []
    for root in voice_lib.voice_roots(state):
        try:
            pack = read(root)
        except PackError as exc:
            log(f"voice pack in {root} ignored: {exc}")
            continue
        if pack is None:
            continue
        seen = _STATUS.get(_key(root, pack)) or {}
        wait = {"offline": RETRY_OFFLINE, "failed": RETRY_FAILED}.get(seen.get("state"))
        if not force and wait and time.time() - seen.get("when", 0) < wait and not is_ready(pack):
            done.append((pack["name"], seen["state"]))
            continue
        done.append((pack["name"], fetch(root, pack, progress)))
    return done


def ensure_soon(state=None, on_done=None, force=False):
    """fetch_all on a thread of its own; returns at once. A call made while one
    is running asks it to go round once more instead of starting a second."""
    with _LOCK:
        if _RUN["busy"]:
            _RUN["again"] = True
            return False
        _RUN["busy"] = True

    def work():
        try:
            while True:
                _RUN["again"] = False
                results = fetch_all(state, force)
                if on_done and any(s == "ready" for _, s in results):
                    try:
                        on_done(results)
                    except Exception as exc:          # noqa: BLE001
                        log(f"voice pack: {exc}")
                with _LOCK:
                    if not _RUN["again"]:
                        _RUN["busy"] = False
                        return
        except Exception as exc:                      # noqa: BLE001 -- a thread nobody joins
            log(f"voice pack fetch stopped: {exc}")
            with _LOCK:
                _RUN["busy"] = False

    threading.Thread(target=work, name="voice-packs", daemon=True).start()
    return True
