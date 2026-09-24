"""
What a game leans on: voices read where they lie, a folder registered once,
the alphabet question asked before speaking, and Pocket offering a clip-only
voice only where it can clone one.

Nothing here loads a model or touches the real config.json -- CONFIG_PATH is
pointed at a temporary file for the voice-root tests, and every voice folder
is made in a temporary directory.

    python test_game_api.py
"""

import json
import os
import shutil
import tempfile
import unittest

import voice_lib


def make_voice(folder, gender=None, culture=None, clip=False):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "embedding.json"), "w") as fh:
        fh.write("{}")
    doc = {"Name": os.path.basename(folder).title()}
    if gender is not None:
        doc["Gender"] = gender
    if culture is not None:
        doc["Culture"] = culture
    with open(os.path.join(folder, "voice.json"), "w") as fh:
        json.dump(doc, fh)
    if clip:
        with open(os.path.join(folder, "breeze-reference.wav"), "wb") as fh:
            fh.write(b"RIFF")
        with open(os.path.join(folder, "breeze-reference.txt"), "w") as fh:
            fh.write("Well met, traveller.")


class Layouts(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def state(self, **extra):
        return {"voicesDir": self.root, "engine": "qwen", **extra}

    def test_a_flat_shelf_takes_sex_and_people_from_voice_json(self):
        make_voice(os.path.join(self.root, "gwen"), gender=1, culture="Battania")
        make_voice(os.path.join(self.root, "aran"), gender=2)
        got = {v["id"]: (v["sex"], v["culture"]) for v in voice_lib.catalog(self.state())}
        self.assertEqual(got["gwen"], ("female", "battania"))
        self.assertEqual(got["aran"], ("male", "other"))

    def test_the_grouped_layout_still_wins_over_the_file(self):
        # A rung is what the person filing the voice chose.
        make_voice(os.path.join(self.root, "female", "vlandia", "livia"), gender=2, culture="empire")
        (hit,) = voice_lib.catalog(self.state())
        self.assertEqual((hit["sex"], hit["culture"]), ("female", "vlandia"))

    def test_housekeeping_folders_are_passed_over(self):
        # The mod's shelf keeps _cache and the like at its top.
        make_voice(os.path.join(self.root, "_cache", "stale"), gender=1)
        make_voice(os.path.join(self.root, "sibylla"), gender=1)
        self.assertEqual([v["id"] for v in voice_lib.catalog(self.state())], ["sibylla"])

    def test_pocket_offers_a_clip_only_where_it_can_clone(self):
        make_voice(os.path.join(self.root, "female", "battania", "gwen"), clip=True)
        without = voice_lib.catalog(self.state(pocketLanguage="english"), "pocket")
        withit = voice_lib.catalog(self.state(pocketLanguage="english", pocketCloning=True), "pocket")
        self.assertNotIn("gwen", {v["id"] for v in without})
        self.assertIn("gwen", {v["id"] for v in withit})

    def test_breeze_needs_the_clip_and_its_words(self):
        make_voice(os.path.join(self.root, "female", "gwen"), clip=True)
        make_voice(os.path.join(self.root, "female", "bree"))
        ids = {v["id"] for v in voice_lib.catalog(self.state(), "breeze")}
        self.assertEqual(ids, {"gwen"})


class Alphabets(unittest.TestCase):
    def test_qwen_reads_cyrillic_and_the_others_do_not(self):
        self.assertTrue(voice_lib.can_read("Добре дошъл.", engine="qwen"))
        self.assertFalse(voice_lib.can_read("Добре дошъл.", engine="pocket"))
        self.assertFalse(voice_lib.can_read("Добре дошъл.", engine="breeze"))
        self.assertTrue(voice_lib.can_read("Well met.", engine="breeze"))


class VoiceRoots(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.was = voice_lib.CONFIG_PATH
        voice_lib.CONFIG_PATH = os.path.join(self.dir, "config.json")

    def tearDown(self):
        voice_lib.CONFIG_PATH = self.was
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_adding_twice_is_harmless_and_removing_takes_it_away(self):
        folder = os.path.join(self.dir, "voices")
        os.makedirs(folder)
        voice_lib.add_voice_root(folder)
        voice_lib.add_voice_root(folder.upper())      # Windows paths are one path, any case
        self.assertEqual(len(voice_lib.load_state()["extraVoicesDirs"]), 1)
        voice_lib.remove_voice_root(folder)
        self.assertEqual(voice_lib.load_state()["extraVoicesDirs"], [])

    def test_a_folder_that_is_not_there_is_refused(self):
        with self.assertRaises(LookupError):
            voice_lib.add_voice_root(os.path.join(self.dir, "nope"))


class Storage(unittest.TestCase):
    """What a game's settings page shows: where each engine's files are, and how much room they take."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_each_engine_says_its_folders_and_their_size(self):
        import speak_server
        studio = os.path.join(self.root, "studio")
        models = os.path.join(self.root, "models")
        breeze = os.path.join(self.root, "breeze", "breeze-tts")
        for folder, size in ((studio, 1000), (models, 2500), (breeze, 400)):
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "blob.bin"), "wb") as fh:
                fh.write(b"x" * size)
        speak_server._SIZES.clear()
        out = speak_server._storage({"studioDir": studio, "modelDir": models, "breezeDir": breeze, "engine": "qwen"})
        self.assertEqual(out["engines"]["qwen"]["dirs"], [studio, models])
        self.assertEqual(out["engines"]["qwen"]["bytes"], 3500)
        # Breeze is reported by the folder that holds all of it, not its code alone.
        self.assertEqual(out["engines"]["breeze"]["dirs"], [os.path.dirname(breeze)])
        self.assertEqual(out["engines"]["breeze"]["bytes"], 400)
        self.assertIn("installed", out["engines"]["pocket"])
        self.assertTrue(out["app"]["dirs"])

    def test_a_folder_that_is_not_there_is_left_out(self):
        import speak_server
        out = speak_server._storage({"studioDir": os.path.join(self.root, "nowhere"), "modelDir": "", "breezeDir": ""})
        self.assertEqual(out["engines"]["qwen"]["dirs"], [])
        self.assertEqual(out["engines"]["breeze"]["dirs"], [])


if __name__ == "__main__":
    unittest.main()
