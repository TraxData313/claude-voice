"""Voice packs: a root's pocket-pack.json, fetched, checked and laid over the catalogue.

Everything happens in a temp folder, served by a local HTTP server on a free
port; CLAUDE_VOICE_PACKS_DIR points the packs there, so the real data folder is
never touched and no engine is started. The states in the zip are tiny
stand-ins unless POCKET_PACK points at a real baked pack (a folder of
<sex>/<culture>/<id>/pocket.safetensors with its manifest.json), in which case
three of its voices are used.

    python test_packs.py
"""

import hashlib
import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch

import pocket_engine
import pocket_packs
import voice_lib

REAL_PACK = os.environ.get("POCKET_PACK") or ""
REVISION = "983151f13aaeab1b13c1e5e3c2c383d49a9edf3f"
WEIGHTS = [("kyutai/pocket-tts", REVISION),
           ("kyutai/pocket-tts-without-voice-cloning", "e7205b6ee50e654a5ea19f0e9df2b0813b05e921")]
VOICES = ["female/aserai/aserai_female_curt", "male/battania/battania_male_gruff",
          "female/vlandia/vlandia_female_warm"]


class _Served(http.server.SimpleHTTPRequestHandler):
    hits = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        _Served.hits.append(self.path)
        return super().do_GET()


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Packs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cv-packs-")
        cls.www = os.path.join(cls.tmp, "www")
        os.makedirs(cls.www)
        voices, src = VOICES, None
        if REAL_PACK and os.path.isfile(os.path.join(REAL_PACK, "manifest.json")):
            with open(os.path.join(REAL_PACK, "manifest.json"), encoding="utf-8") as fh:
                rows = json.load(fh)["voices"][:3]
            voices = [os.path.dirname(r["file"]) for r in rows]
            src = REAL_PACK
        cls.voices = voices
        cls.zip = os.path.join(cls.www, "pack.zip")
        with zipfile.ZipFile(cls.zip, "w") as z:
            for rel in voices:
                name = f"{rel}/pocket.safetensors"
                if src:
                    z.write(os.path.join(src, *name.split("/")), name)
                else:
                    z.writestr(name, b"state of " + rel.encode())
            z.writestr("manifest.json", "{}")                   # extra files are ignored
            z.writestr("../escape/pocket.safetensors", b"no")   # and climbing out is never taken
        handler = lambda *a, **k: _Served(*a, directory=cls.www, **k)  # noqa: E731
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.case = tempfile.mkdtemp(dir=self.tmp)
        self.root = os.path.join(self.case, "Voices")
        for rel in self.voices + ["male/sturgia/sturgia_male_not_in_pack"]:
            d = os.path.join(self.root, *rel.split("/"))
            os.makedirs(d)
            open(os.path.join(d, "breeze-reference.wav"), "wb").close()
            with open(os.path.join(d, "breeze-reference.txt"), "w") as fh:
                fh.write("words")
        self.packs = os.path.join(self.case, "packs")
        self.env = patch.dict(os.environ, {"CLAUDE_VOICE_PACKS_DIR": self.packs})
        self.env.start()
        self.weights = patch.object(pocket_engine, "weights_of", lambda lang: list(WEIGHTS))
        self.weights.start()
        pocket_packs._WEIGHTS.clear()
        pocket_packs._STATUS.clear()
        pocket_packs._SAID.clear()
        self.logged = []
        self.log = patch.object(pocket_packs, "log", self.logged.append)
        self.log.start()
        _Served.hits.clear()
        own = os.path.join(self.case, "own")
        os.makedirs(own)
        self.state = {"engine": "pocket", "voicesDir": own, "extraVoicesDirs": [self.root],
                      "pocketCloning": False, "pocketLanguage": "english"}

    def tearDown(self):
        self.log.stop()
        self.weights.stop()
        self.env.stop()

    def _pack(self, **over):
        doc = {"schema": pocket_packs.SCHEMA, "version": 1, "name": "calradia",
               "url": f"http://127.0.0.1:{self.port}/pack.zip",
               "sha256": _sha(self.zip), "bytes": os.path.getsize(self.zip),
               "weights": {"repo": "kyutai/pocket-tts", "revision": REVISION, "language": "english"},
               "voices": list(self.voices)}
        doc.update(over)
        with open(os.path.join(self.root, pocket_packs.PACK_FILE), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return pocket_packs.read(self.root)

    def _catalog(self):
        return {v["id"]: v for v in voice_lib.catalog(self.state, "pocket")}

    def _states(self):
        return [row["state"] for row in pocket_packs.status(self.state)]

    # -- the happy road ---------------------------------------------------
    def test_a_fetched_pack_makes_its_voices_speak_on_pocket_without_cloning(self):
        ids = [rel.split("/")[-1] for rel in self.voices]
        self.assertFalse(set(ids) & set(self._catalog()), "clip-only voices are hidden without cloning")
        self._pack()
        self.assertEqual(self._states(), ["absent"])
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "ready")])
        got = self._catalog()
        for vid, rel in zip(ids, self.voices):
            self.assertIn(vid, got)
            self.assertTrue(got[vid]["pocket"].startswith(self.packs))
            self.assertTrue(got[vid]["pocket"].endswith(os.path.join(*rel.split("/"), "pocket.safetensors")))
            self.assertEqual(got[vid]["pocketLanguage"], "english")
        self.assertNotIn("sturgia_male_not_in_pack", got)
        _, kw = voice_lib.resolve(ids[0], state=self.state)
        self.assertEqual(kw["pocket_voice"], got[ids[0]]["pocket"])
        self.assertEqual(self._states(), ["ready"])
        self.assertFalse(os.path.exists(os.path.join(self.packs, "escape")))
        self.assertFalse(os.path.exists(os.path.join(self.case, "escape")))
        # Nothing is written into the voice root; nothing but the pack is left in packs.
        self.assertEqual(sorted(os.listdir(self.root)), ["female", "male", "pocket-pack.json"])
        self.assertEqual(os.listdir(os.path.join(self.packs, "calradia")), [REVISION])

    def test_present_and_verified_is_not_fetched_again(self):
        self._pack()
        pocket_packs.fetch_all(self.state)
        self.assertEqual(len(_Served.hits), 1)
        self.assertEqual(pocket_packs.fetch_all(self.state, force=True), [("calradia", "ready")])
        self.assertEqual(len(_Served.hits), 1)

    def test_a_new_zip_for_the_same_revision_replaces_the_old_whole(self):
        self._pack()
        pocket_packs.fetch_all(self.state)
        marker = os.path.join(self.packs, "calradia", REVISION, pocket_packs.MARKER)
        with open(marker, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["sha256"] = "0" * 64                      # as if an older zip were in place
        with open(marker, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        self.assertEqual(self._states(), ["absent"])
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "ready")])
        self.assertEqual(len(_Served.hits), 2)
        self.assertEqual(sorted(os.listdir(os.path.join(self.packs, "calradia"))), [REVISION])

    # -- refused ----------------------------------------------------------
    def test_a_checksum_mismatch_is_refused_and_left_alone_for_a_while(self):
        self._pack(sha256="ab" * 32)
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "failed")])
        self.assertEqual(self._states(), ["failed"])
        self.assertIn("sha256", pocket_packs.status(self.state)[0]["detail"])
        self.assertFalse(os.path.exists(os.path.join(self.packs, "calradia", REVISION)))
        self.assertEqual([f for f in os.listdir(os.path.join(self.packs, "calradia"))], [],
                         "the bad download is not kept")
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "failed")])
        self.assertEqual(len(_Served.hits), 1, "no second try straight away")
        self.assertNotIn("aserai_female_curt", self._catalog())

    def test_another_weights_revision_is_never_fetched_nor_used(self):
        other = "1" * 40
        self._pack(weights={"repo": "kyutai/pocket-tts", "revision": other, "language": "english"})
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "mismatch")])
        self.assertEqual(_Served.hits, [])
        self.assertTrue(any("garbage" in m for m in self.logged), self.logged)
        # Even one already in place -- left by an older copy of this app -- is not used.
        self._pack()
        pocket_packs.fetch_all(self.state)
        with patch.object(pocket_engine, "weights_of", lambda lang: [("kyutai/pocket-tts", other)]):
            pocket_packs._WEIGHTS.clear()
            self.assertEqual(self._states(), ["mismatch"])
            self.assertNotIn(self.voices[0].split("/")[-1], self._catalog())

    def test_offline_says_so_and_waits(self):
        dead = http.server.HTTPServer(("127.0.0.1", 0), _Served)
        port = dead.server_address[1]
        dead.server_close()                            # a port nobody answers on
        self._pack(url=f"http://127.0.0.1:{port}/pack.zip")
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "offline")])
        self.assertEqual(self._states(), ["offline"])
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "offline")])
        self.assertTrue(any("offline" in m for m in self.logged))

    def test_a_missing_file_on_the_server_is_a_failure(self):
        self._pack(url=f"http://127.0.0.1:{self.port}/nothing.zip")
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "failed")])
        self.assertIn("404", pocket_packs.status(self.state)[0]["detail"])

    def test_a_zip_without_a_listed_voice_is_refused_whole(self):
        self._pack(voices=list(self.voices) + ["male/khuzait/khuzait_male_absent"])
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "failed")])
        self.assertFalse(os.path.exists(os.path.join(self.packs, "calradia", REVISION)))

    def test_no_pocket_tts_means_waiting_not_downloading(self):
        self._pack()
        with patch.object(pocket_engine, "weights_of", lambda lang: []):
            pocket_packs._WEIGHTS.clear()
            self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "waiting")])
        self.assertEqual(_Served.hits, [])

    def test_a_file_url_works_too(self):
        self._pack(url="file:///" + self.zip.replace("\\", "/"))
        self.assertEqual(pocket_packs.fetch_all(self.state), [("calradia", "ready")])

    def test_the_background_fetch_returns_at_once_and_reports(self):
        self._pack()
        done = threading.Event()
        self.assertTrue(pocket_packs.ensure_soon(self.state, on_done=lambda r: done.set()))
        self.assertTrue(done.wait(20))
        self.assertEqual(self._states(), ["ready"])

    # -- the description --------------------------------------------------
    def test_a_bad_description_is_refused_with_its_reason(self):
        good = {"schema": pocket_packs.SCHEMA, "version": 1, "name": "x", "url": "https://h/x.zip",
                "sha256": "a" * 64, "bytes": 1,
                "weights": {"repo": "kyutai/pocket-tts", "revision": REVISION, "language": "english"},
                "voices": ["female/a/b"]}
        self.assertEqual(pocket_packs.parse(good)["voices"], ["female/a/b"])
        self.assertEqual(pocket_packs.parse({**good, "voices": ["female\\a\\b\\"]})["voices"], ["female/a/b"])
        for bad in ({"version": 2}, {"schema": "other"}, {"name": "../x"}, {"url": "ftp://h/x"},
                    {"sha256": "z" * 64}, {"bytes": 0}, {"voices": []}, {"voices": ["../x"]},
                    {"voices": ["C:/x"]}, {"voices": ["a/b", "A/B"]},
                    {"weights": {"repo": "kyutai/pocket-tts", "revision": "983151f", "language": "english"}}):
            with self.assertRaises(pocket_packs.PackError, msg=bad):
                pocket_packs.parse({**good, **bad})

    def test_an_unreadable_description_costs_nothing_else(self):
        self._pack()
        with open(os.path.join(self.root, pocket_packs.PACK_FILE), "w") as fh:
            fh.write("{ not json")
        self.assertEqual(self._states(), ["invalid"])
        self.assertIn("sturgia_male_not_in_pack", {v["id"] for v in voice_lib.catalog(
            {**self.state, "pocketCloning": True}, "pocket")})

    def test_the_packs_folder_is_the_apps_own(self):
        self.env.stop()
        try:
            self.assertEqual(pocket_packs.packs_dir(), os.path.join(voice_lib.DATA_DIR, "pocket-packs"))
        finally:
            self.env.start()


if __name__ == "__main__":
    unittest.main(verbosity=2)
