"""The engine staying reachable, and staying up, on a machine that fights it.

Three things went wrong on Toni's machine in October 2026, and these are them:

- the HTTP threads starved while the engine thread converted audio a sample at
  a time, so /health and /state answered after the caller had given up;
- another program (FaceLab's dev server) held port 8765, and every start said
  "another engine is probably up" and left;
- Smart App Control refused Qwen's unsigned qwen3_tts.dll, and the engine sat
  in an error state saying "java exception during CallLongMethod".

Nothing here loads a model, and the real config.json is never touched.

    python test_robustness.py
"""

import array
import ctypes
import http.server
import json
import os
import queue
import random
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import qwen_engine
import speak_server
import voice_lib


def setUpModule():
    # No test here may map a real DLL -- on a Smart App Control machine each
    # refused one is a toast on the user's screen -- or write the real note.
    folder = tempfile.mkdtemp()
    guards = [patch.object(qwen_engine, "BLOCK_NOTE", os.path.join(folder, "qwen-blocked.json")),
              patch.object(qwen_engine, "_map_without_running",
                           side_effect=AssertionError("a test tried to map a real DLL"))]
    for guard in guards:
        guard.start()
    _CLEANUP.extend(guards)
    _CLEANUP.append(folder)


def tearDownModule():
    for item in _CLEANUP:
        if isinstance(item, str):
            shutil.rmtree(item, True)
        else:
            item.stop()


_CLEANUP = []


# --------------------------------------------------------------------------
# the audio helpers, against the loops they replaced
# --------------------------------------------------------------------------

def _loudest_ref(samples, start, stop):
    loudest = 0.0
    for s in samples[start:stop]:
        a = -s if s < 0 else s
        if a > loudest:
            loudest = a
    return loudest


def trim_head_ref(samples, rate=24000, window=voice_lib.QUIET_WINDOW,
                  floor=voice_lib.QUIET_FLOOR):
    win = max(1, int(window * rate))
    at = 0
    while at + win <= len(samples) and _loudest_ref(samples, at, at + win) <= floor:
        at += win
    return at


def quiet_span_ref(samples, rate=24000, search=voice_lib.QUIET_SEARCH,
                   window=voice_lib.QUIET_WINDOW, ratio=voice_lib.QUIET_RATIO, budget=0.0):
    n = len(samples)
    win = max(1, int(window * rate))
    back = min(n, int(search * rate))
    if back < win * 2:
        return 0, 0
    best, best_at, peak = None, 0, 0.0
    for start in range(n - back, n - win + 1, win):
        loudest = _loudest_ref(samples, start, start + win)
        if loudest > peak:
            peak = loudest
        if best is None or loudest < best:
            best, best_at = loudest, start
    if peak <= 0.0:
        return n, n
    if best > peak * ratio:
        return 0, 0
    level = peak * ratio
    lo, hi = best_at, best_at + win
    while lo - win >= 0 and _loudest_ref(samples, lo - win, lo) <= level:
        lo -= win
    while hi + win <= n and _loudest_ref(samples, hi, hi + win) <= level:
        hi += win
    keep = max(0, (hi - lo) - int(budget * rate))
    return lo + keep, hi


def speechlike(rng, n):
    out = array.array("f")
    while len(out) < n:
        amp = rng.choice([0.0, 0.001, 0.005, 0.3, 0.8, 1.2])
        out.extend(rng.uniform(-amp, amp) for _ in range(rng.randint(200, 6000)))
    return out[:n]


class TheAudioHelpersAgreeWithTheLoops(unittest.TestCase):
    """Same answers as the per-sample loops, with numpy and without it."""

    def each_path(self):
        yield "numpy"
        saved = voice_lib._np
        voice_lib._np = None
        try:
            yield "builtins"
        finally:
            voice_lib._np = saved

    def test_cut_points(self):
        rng = random.Random(7)
        cases = [speechlike(rng, rng.randint(0, 60000)) for _ in range(60)]
        for path in self.each_path():
            for samples in cases:
                for budget in (0.0, 0.2):
                    self.assertEqual(voice_lib.quiet_span(samples, budget=budget),
                                     quiet_span_ref(samples, budget=budget), path)
                self.assertEqual(voice_lib.trim_head(samples), trim_head_ref(samples), path)

    def test_lists_and_numpy_are_read_the_same(self):
        rng = random.Random(3)
        samples = speechlike(rng, 40000)
        want = quiet_span_ref(samples)
        self.assertEqual(voice_lib.quiet_span(list(samples)), want)
        if voice_lib._np is not None:
            self.assertEqual(voice_lib.quiet_span(voice_lib._np.array(samples)), want)

    def test_pcm_is_what_the_loop_wrote(self):
        rng = random.Random(5)
        for amp in (0.5, 1.7):
            samples = array.array("f", (rng.uniform(-amp, amp) for _ in range(3000)))
            peak = max(abs(s) for s in samples)
            scale = (0.99 / peak) if peak > 1.0 else 1.0
            want = bytearray()
            for s in samples:
                want += int(max(-1.0, min(1.0, s * scale)) * 32767).to_bytes(2, "little", signed=True)
            for path in self.each_path():
                self.assertEqual(voice_lib.pcm16(samples), bytes(want), path)
        self.assertEqual(voice_lib.pcm16([]), b"")

    def test_floats_from_every_engine(self):
        ctypes_arr = (ctypes.c_float * 3)(0.25, -0.5, 1.0)
        for path in self.each_path():
            self.assertEqual(list(voice_lib.as_floats(ctypes_arr)), [0.25, -0.5, 1.0], path)
            self.assertEqual(list(voice_lib.as_floats([0.25, -0.5])), [0.25, -0.5], path)
        if voice_lib._np is not None:
            got = voice_lib.as_floats(voice_lib._np.array([0.5, 1.0], dtype="float32"))
            self.assertEqual((got.typecode, list(got)), ("f", [0.5, 1.0]))
        mine = array.array("f", [1.0])
        self.assertIs(voice_lib.as_floats(mine), mine)

    def test_breeze_pcm_decodes_the_same(self):
        import breeze_engine

        pcm = array.array("h", [0, 1, -1, 32767, -32768, 1234])
        want = array.array("f", [s / 32767.0 for s in pcm])
        self.assertEqual(breeze_engine.pcm_to_float(pcm.tobytes()), want)


# --------------------------------------------------------------------------
# Windows refusing Qwen
# --------------------------------------------------------------------------

class SpottingABlock(unittest.TestCase):
    def test_what_counts_as_a_block(self):
        self.assertTrue(speak_server._blocked_by_policy(
            qwen_engine.BlockedByPolicy(r"C:\x\qwen3_tts.dll", "on")))
        err = OSError(22, "An Application Control policy has blocked this file")
        err.winerror = 4551
        self.assertTrue(speak_server._blocked_by_policy(err))
        self.assertTrue(speak_server._blocked_by_policy(RuntimeError(
            "qwen3_tts.dll: An Application Control policy has blocked this file")))
        self.assertFalse(speak_server._blocked_by_policy(RuntimeError("nativeInit returned 0")))
        self.assertFalse(speak_server._blocked_by_policy(FileNotFoundError("no Studio")))

    def test_the_message_names_smart_app_control_only_when_it_is_on(self):
        on = str(qwen_engine.BlockedByPolicy(r"C:\x\qwen3_tts.dll", "on"))
        self.assertIn("Smart App Control", on)
        self.assertIn("qwen3_tts.dll", on)
        other = str(qwen_engine.BlockedByPolicy(r"C:\x\qwen3_tts.dll", "off"))
        self.assertNotIn("Smart App Control", other)
        self.assertIn("Application Control policy", other)
        self.assertEqual(speak_server._sentence(qwen_engine.BlockedByPolicy("q.dll", "on")),
                         "Windows Smart App Control blocked the Qwen engine")

    def test_the_fallback_order(self):
        ready = {"pocket": True, "breeze": True}
        with patch.object(speak_server, "_engine_ready", lambda n, s: ready.get(n, False)):
            self.assertEqual(speak_server.fallback_engine("qwen", {}), "pocket")
            ready["pocket"] = False
            self.assertEqual(speak_server.fallback_engine("qwen", {}), "breeze")
            ready["breeze"] = False
            self.assertIsNone(speak_server.fallback_engine("qwen", {}))


class DecidedWithoutLoading(unittest.TestCase):
    """Every refused load is a Windows Security toast, so the answer must come
    from the registry or the note whenever they have one. `probe` stands in
    for mapping a DLL and records whether it was asked at all."""

    def setUp(self):
        self.studio = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.studio, True)
        for name in ("qwen3_tts.dll", "ggml.dll"):
            with open(os.path.join(self.studio, name), "wb") as fh:
                fh.write(b"stand-in")
        self.asked = []
        self.answer = {}

    def probe(self, path):
        self.asked.append(os.path.basename(path))
        return self.answer.get(os.path.basename(path), 0)

    def refusal(self, sac):
        with patch.object(qwen_engine, "smart_app_control", lambda: sac):
            return qwen_engine.refusal(self.studio, probe=self.probe)

    def test_smart_app_control_on_means_no_load_at_all(self):
        got = self.refusal("on")
        self.assertIsInstance(got, qwen_engine.BlockedByPolicy)
        self.assertIn("Smart App Control", str(got))
        self.assertEqual(self.asked, [])

    def test_a_noted_block_is_believed(self):
        self.answer["qwen3_tts.dll"] = 4551
        self.assertIsNotNone(self.refusal("off"))           # found once, by a probe
        self.assertEqual(self.asked, ["qwen3_tts.dll"])
        self.asked.clear()
        self.assertIsNotNone(self.refusal("off"))           # then never asked again
        self.assertEqual(self.asked, [])

    def test_a_new_dll_gets_its_chance(self):
        self.answer["qwen3_tts.dll"] = 4551
        self.refusal("off")
        time.sleep(0.02)
        with open(os.path.join(self.studio, "qwen3_tts.dll"), "wb") as fh:
            fh.write(b"a newer build")
        self.answer.clear()
        self.asked.clear()
        self.assertIsNone(self.refusal("off"))
        self.assertIn("qwen3_tts.dll", self.asked)

    def test_a_sac_block_is_forgotten_once_sac_is_off(self):
        qwen_engine.note_block(os.path.join(self.studio, "qwen3_tts.dll"), "on")
        self.assertIsNotNone(self.refusal("evaluation"))
        self.assertEqual(self.asked, [])
        self.assertIsNone(self.refusal("off"))

    def test_a_broken_dll_is_not_a_block(self):
        self.answer["qwen3_tts.dll"] = 193             # not a valid image: not policy
        self.assertIsNone(self.refusal("off"))
        self.assertIsNone(qwen_engine.noted_block("off"))

    def test_the_engine_stops_before_anything_is_loaded(self):
        os.makedirs(os.path.join(self.studio, "app"))
        os.makedirs(os.path.join(self.studio, "runtime", "bin", "server"))
        with open(os.path.join(self.studio, "runtime", "bin", "server", "jvm.dll"), "wb") as fh:
            fh.write(b"stand-in")
        with patch.object(qwen_engine, "smart_app_control", lambda: "on"), \
                patch.object(qwen_engine.ctypes, "CDLL",
                             side_effect=AssertionError("tried to load a DLL")):
            with self.assertRaises(qwen_engine.BlockedByPolicy):
                qwen_engine.Engine(self.studio, verbose=False)

class AFallbackInsteadOfAnErrorState(unittest.TestCase):
    """The engine thread, with build_engine replaced: Qwen is blocked by SAC."""

    def start(self, engine="qwen", ready=("pocket", "breeze"), fail=None):
        self.live = {"engine": engine}
        self.builds = []
        self.switched = []

        def build(state, progress=None):
            self.builds.append(state.get("engine"))
            if state.get("engine") == "qwen":
                raise fail or qwen_engine.BlockedByPolicy(r"C:\s\qwen3_tts.dll", "on")
            return state.get("engine"), object()

        def set_engine(name, state=None):
            self.switched.append(name)
            self.live["engine"] = name
            return name, None

        sp = self.sp = speak_server.Speaker.__new__(speak_server.Speaker)
        sp.state = dict(self.live)
        sp.jobs = queue.Queue()
        sp.lock = threading.Lock()
        sp.current = None
        sp.error = None
        sp.notice = None
        sp.ready = threading.Event()
        sp.nudge = threading.Event()
        sp.engine_name = None
        sp._live = lambda: dict(self.live)
        for target, name, value in (
                (speak_server, "build_engine", build),
                (speak_server, "log", lambda *_a, **_k: None),
                (speak_server, "rss_mb", lambda: 0),
                (speak_server, "_engine_ready", lambda n, s: n in ready),
                (voice_lib, "set_engine", set_engine),
                (voice_lib, "engine_of", lambda s: s.get("engine"))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        threading.Thread(target=sp._engine_loop, daemon=True).start()
        self.assertTrue(sp.ready.wait(3))

    def test_blocked_qwen_comes_up_as_pocket(self):
        self.start()
        self.assertIsNone(self.sp.error)
        self.assertEqual(self.sp.engine_name, "pocket")
        self.assertEqual(self.switched, ["pocket"])        # written to the config
        self.assertEqual(self.builds, ["qwen", "pocket"])
        self.assertIn("Smart App Control", self.sp.notice)
        self.assertIn("Pocket", self.sp.notice)

    def test_breeze_when_there_is_no_pocket(self):
        self.start(ready=("breeze",))
        self.assertEqual(self.sp.engine_name, "breeze")
        self.assertIn("Breeze", self.sp.notice)

    def test_nothing_to_fall_back_on_says_so(self):
        self.start(ready=())
        self.assertIsNotNone(self.sp.error)
        self.assertIn("No other engine", self.sp.notice)
        self.assertEqual(self.switched, [])

    def test_an_ordinary_failure_is_not_routed_around(self):
        # A Studio that is missing, a GPU that is busy: the user's choice
        # stands and the error says what happened, as before.
        self.start(fail=RuntimeError("nativeInit returned 0"))
        self.assertIsNotNone(self.sp.error)
        self.assertEqual(self.switched, [])
        self.assertIsNone(self.sp.notice)

    def test_picking_qwen_again_tries_again(self):
        self.start()
        self.live["engine"] = "qwen"
        self.sp.nudge.set()
        deadline = time.time() + 3
        while time.time() < deadline and self.builds.count("qwen") < 2:
            time.sleep(0.02)
        self.assertEqual(self.builds.count("qwen"), 2)


# --------------------------------------------------------------------------
# the port
# --------------------------------------------------------------------------

class _Stranger(http.server.BaseHTTPRequestHandler):
    """A dev server: answers everything with a page that is not ours."""
    body = b"<html>404</html>"
    code = 404

    def do_POST(self):
        self.send_response(self.code)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_a):
        pass


class _Ours(_Stranger):
    body = json.dumps({"ready": True, "pid": 1}).encode()
    code = 200


def _serve(handler):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class KnowingWhoHoldsThePort(unittest.TestCase):
    def test_a_stranger_is_not_alive_and_is_called_a_stranger(self):
        httpd = _serve(_Stranger)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        self.assertIsNone(voice_lib.server_alive(port))
        self.assertEqual(voice_lib.port_probe(port)[0], "stranger")

    def test_json_without_ready_is_a_stranger_too(self):
        class Json(_Stranger):
            body, code = b'{"status": "ok"}', 200
        httpd = _serve(Json)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        self.assertIsNone(voice_lib.server_alive(port))
        self.assertEqual(voice_lib.port_probe(port)[0], "stranger")

    def test_ours_is_ours(self):
        httpd = _serve(_Ours)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        self.assertTrue(voice_lib.server_alive(port)["ready"])
        kind, health = voice_lib.port_probe(port)
        self.assertEqual((kind, health["pid"]), ("ours", 1))

    def test_a_closed_port_is_free(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertEqual(voice_lib.port_probe(port, timeout=3)[0], "free")


class BindingThePort(unittest.TestCase):
    """bind_port with the socket and the probes replaced by a table."""

    def bind(self, taken, home=8765, asked=None, owners=None, tries=3):
        made = []

        def make(port):
            if port in taken:
                raise OSError(10048, "Only one usage of each socket address")
            made.append(port)
            return f"server:{port}"

        def probe(port):
            return taken[port], ({"ready": True} if taken[port] == "ours" else None)

        with patch.object(speak_server, "log", lambda *_a, **_k: None):
            return speak_server.bind_port(home, asked, make=make, probe=probe,
                                          owner=lambda p: (owners or {}).get(p, ""),
                                          tries=tries)

    def test_a_free_home_is_simply_taken(self):
        self.assertEqual(self.bind({}), ("server:8765", 8765, None))

    def test_a_stranger_moves_us_along_and_is_named(self):
        server, port, note = self.bind({8765: "stranger"}, owners={8765: "node.exe (pid 42)"})
        self.assertEqual((server, port), ("server:8766", 8766))
        self.assertIn("node.exe (pid 42)", note)
        self.assertIn("8766", note)

    def test_an_engine_of_ours_means_leave(self):
        self.assertEqual(self.bind({8765: "ours"}), (None, 8765, None))

    def test_a_silent_python_may_be_us_stuck(self):
        got = self.bind({8765: "silent"}, owners={8765: "pythonw.exe (pid 7)"})
        self.assertEqual(got, (None, 8765, None))

    def test_a_silent_stranger_is_routed_around(self):
        got = self.bind({8765: "silent"}, owners={8765: "java.exe (pid 9)"})
        self.assertEqual(got[1], 8766)

    def test_a_reserved_port_is_skipped(self):
        # Refused, and nobody listening: Windows has reserved it.
        self.assertEqual(self.bind({8765: "free"})[1], 8766)

    def test_back_home_once_it_is_free(self):
        # Moved to 8766 earlier: the config says 8766 and portHome 8765.
        self.assertEqual(self.bind({}, home=8765, asked=8766), ("server:8765", 8765, None))

    def test_the_last_resort_is_the_port_we_were_last_on(self):
        server, port, _note = self.bind({8765: "stranger"}, home=8765, asked=8770)
        self.assertEqual(port, 8770)

    def test_everything_taken_says_what_to_do(self):
        taken = {p: "stranger" for p in range(8765, 8770)}
        with self.assertRaises(speak_server.PortsTaken) as caught:
            self.bind(taken)
        self.assertIn("config.json", str(caught.exception))


# --------------------------------------------------------------------------
# the polled routes
# --------------------------------------------------------------------------

class CheapPolls(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.was = voice_lib.CONFIG_PATH
        voice_lib.CONFIG_PATH = os.path.join(self.dir, "config.json")
        self.addCleanup(setattr, voice_lib, "CONFIG_PATH", self.was)
        speak_server._CONFIG["hit"] = None
        self.addCleanup(speak_server._CONFIG.__setitem__, "hit", None)

    def test_the_config_is_read_once_until_it_changes(self):
        voice_lib.save_state({"voice": "neya"})
        reads = []
        real = voice_lib.load_state

        def counting():
            reads.append(1)
            return real()

        with patch.object(voice_lib, "load_state", counting):
            first = speak_server._config_now()
            self.assertIs(speak_server._config_now(), first)
            self.assertEqual(len(reads), 1)
            time.sleep(0.02)
            voice_lib.save_state({"voice": "abby", "engine": "pocket"})
            self.assertEqual(speak_server._config_now()["voice"], "abby")
            self.assertEqual(len(reads), 2)

    def test_a_button_throws_the_cached_state_away(self):
        speak_server._STATE["hit"] = (time.monotonic(), None, b"{}")
        speak_server._SESSIONS["hit"] = (time.monotonic(), [])
        speak_server._forget_polled()
        self.assertIsNone(speak_server._STATE["hit"])
        self.assertIsNone(speak_server._SESSIONS["hit"])
        self.assertNotIn("/pause", speak_server.READ_ONLY_ROUTES)
        self.assertIn("/state", speak_server.READ_ONLY_ROUTES)


class ADriverTooOldForStudiosCuda(unittest.TestCase):
    """An RTX 5070 on driver 577.13 against Studio's CUDA 13 ran Qwen on the
    CPU with nothing but "(null)" in the log to say why."""

    def setUp(self):
        self.studio = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.studio, True)

    def ship(self, name):
        open(os.path.join(self.studio, name), "wb").close()

    def test_an_old_driver_is_named_with_the_one_needed(self):
        self.ship("cudart64_13.dll")
        said = qwen_engine.gpu_problem(self.studio, ("NVIDIA GeForce RTX 5070", "577.13"))
        self.assertIn("580 or newer", said)
        self.assertIn("577.13", said)

    def test_a_new_enough_driver_says_nothing(self):
        self.ship("cudart64_13.dll")
        self.assertIsNone(qwen_engine.gpu_problem(self.studio, ("RTX", "581.42")))

    def test_no_cuda_runtime_in_studio_says_nothing(self):
        self.assertIsNone(qwen_engine.gpu_problem(self.studio, ("RTX", "300.00")))

    def test_the_runtime_major_is_read_off_its_name(self):
        self.ship("cudart64_12.dll")
        self.assertEqual(qwen_engine.studio_cuda_major(self.studio), 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
