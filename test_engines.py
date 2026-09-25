"""
The two engines: which voices each offers, what resolve hands the server, and
what happens to an alphabet one of them cannot read.

Nothing here loads a model. The catalogue, the kwargs and the Cyrillic guard
are all decided before any audio exists, which is the point -- they are the
parts that decide whether the right engine gets the right voice, and a test
that needed a GPU would never be run.

    python test_engines.py
"""

import unittest
from unittest.mock import patch

import pocket_engine
import voice_lib

QWEN = {"engine": "qwen", "voicesDir": "voices"}
POCKET = {"engine": "pocket", "voicesDir": "voices"}


class ChoosingAnEngine(unittest.TestCase):
    def test_default_is_the_original(self):
        self.assertEqual(voice_lib.engine_of({}), "qwen")

    def test_a_typo_falls_back_rather_than_raising(self):
        # On the path to speaking. A bad name should cost the wrong engine,
        # never silence with no explanation.
        self.assertEqual(voice_lib.engine_of({"engine": "poket"}), "qwen")

    def test_language_falls_back_the_same_way(self):
        self.assertEqual(voice_lib.engine_language({"pocketLanguage": "klingon"}),
                         "english")
        self.assertEqual(voice_lib.engine_language({"pocketLanguage": "italian"}),
                         "italian")


class WhoSpeaksAfterASwitch(unittest.TestCase):
    """Abby first on every engine that has her. A first switch to Pocket used to
    land on its own default, a man's voice, which the panel draws with Max's face."""

    def switch_to_pocket(self, rows, remembered=None, current=None):
        state = {"engine": "qwen", "voiceByEngine": {"pocket": remembered} if remembered else {}}
        if current:
            state["voice"] = current
        with patch.object(voice_lib, "catalog", return_value=rows), \
             patch.object(pocket_engine, "available", return_value=True), \
             patch.object(voice_lib, "patch_state"), \
             patch.object(voice_lib, "load_state", return_value=state), \
             patch.object(voice_lib, "set_voice", side_effect=lambda vid, _state=None: ({"id": vid}, False)):
            return voice_lib.set_engine("pocket", state)[1]["id"]

    def test_abby_where_the_engine_has_her(self):
        rows = [{"id": "alba", "sex": "male"}, {"id": "abby", "sex": "female"},
                {"id": "anna", "sex": "female"}]
        self.assertEqual(self.switch_to_pocket(rows), "abby")

    def test_a_woman_of_its_own_where_it_cannot_clone(self):
        rows = [{"id": "alba", "sex": "male"}, {"id": "anna", "sex": "female"}]
        self.assertEqual(self.switch_to_pocket(rows), "anna")

    def test_the_last_choice_made_there_wins_where_she_is_missing(self):
        rows = [{"id": "alba", "sex": "male"}, {"id": "abby", "sex": "female"}]
        self.assertEqual(self.switch_to_pocket(rows, remembered="alba", current="sibylla"), "alba")

    def test_the_voice_speaking_now_comes_along(self):
        # Neya on Qwen, switched to Pocket where she was carried across: still
        # Neya, not the Abby Pocket last spoke with.
        rows = [{"id": "abby", "sex": "female"}, {"id": "neya", "sex": "female"}]
        self.assertEqual(self.switch_to_pocket(rows, remembered="abby", current="neya"), "neya")


class Catalogues(unittest.TestCase):
    def test_the_engine_decides_what_the_catalogue_contains(self):
        qwen = {v["id"] for v in voice_lib.catalog(QWEN)}
        pocket = {v["id"] for v in voice_lib.catalog(POCKET)}
        self.assertIn("abby", qwen)
        self.assertIn("alba", pocket)
        # A built-in belongs to its own engine and appears nowhere else. The
        # reverse is not true any more: a local voice carried across with
        # make_pocket_voice.py is deliberately in both lists, which is the
        # whole point of carrying it across.
        self.assertNotIn("alba", qwen)

    def test_pocket_rows_have_the_keys_the_panel_draws(self):
        for row in voice_lib.catalog(POCKET):
            for key in ("id", "name", "sex", "culture", "style"):
                self.assertIn(key, row)
            self.assertIn(row["sex"], ("female", "male"))

    def test_a_cloned_voice_comes_first_and_carries_its_file(self):
        # Only meaningful once something has been carried across; skipped
        # rather than failed on a checkout where nothing has been.
        rows = voice_lib.catalog(POCKET)
        cloned = [r for r in rows if r.get("dir")]
        if not cloned:
            self.skipTest("no voice has been cloned into pocket-tts yet")
        self.assertIs(rows[0], cloned[0], "a cloned voice sorts above the built-ins")
        for row in cloned:
            self.assertTrue(row["pocket"].endswith(voice_lib.POCKET_VOICE))
            _, kwargs = voice_lib.resolve(row["id"], "embedding", POCKET)
            self.assertEqual(kwargs["pocket_voice"], row["pocket"])

    def test_language_orders_the_list_and_never_hides_a_voice(self):
        # It used to filter, and that is how Estelle stayed invisible: a voice
        # you cannot see is a voice you cannot pick.
        english = pocket_engine.catalog("english")
        french = pocket_engine.catalog("french")
        self.assertEqual(len(english), len(pocket_engine.VOICES))
        self.assertEqual(len(french), len(pocket_engine.VOICES))
        self.assertEqual(english[0]["culture"], "english")
        self.assertEqual(french[0]["culture"], "french")
        self.assertEqual(english[-1]["culture"], "spanish")

    def test_only_a_foreign_voice_wears_its_language(self):
        rows = {v["id"]: v for v in pocket_engine.catalog("english")}
        self.assertEqual(rows["alba"]["tag"], "")
        self.assertEqual(rows["estelle"]["tag"], "French")
        self.assertEqual(rows["rafael"]["tag"], "Brazilian Portuguese")

    def test_both_sexes_are_represented(self):
        # The panel borrows a portrait per sex, so a list that was all one
        # would quietly make the stand-in face meaningless.
        sexes = {v["sex"] for v in voice_lib.catalog(POCKET)}
        self.assertEqual(sexes, {"female", "male"})


class WhatResolveHandsTheServer(unittest.TestCase):
    def test_qwen_gets_a_path(self):
        _, kwargs = voice_lib.resolve("abby", "embedding", QWEN)
        self.assertIn("embedding_path", kwargs)

    def test_pocket_gets_a_name_and_the_model_it_belongs_to(self):
        _, kwargs = voice_lib.resolve("eve", "embedding", POCKET)
        self.assertEqual(kwargs, {"pocket_voice": "eve",
                                  "pocket_language": "english"})

    def test_a_foreign_voice_brings_its_own_model(self):
        # Picking Estelle has to load the French model. Handing her state to
        # the English one gives nonsense rather than an error, which is why
        # the language travels with the voice rather than sitting in config.
        _, kwargs = voice_lib.resolve("estelle", "embedding", POCKET)
        self.assertEqual(kwargs["pocket_language"], "french")
        _, kwargs = voice_lib.resolve("giovanni", "embedding", POCKET)
        self.assertEqual(kwargs["pocket_language"], "italian")

    def test_a_voice_not_carried_across_is_not_found(self):
        # A voice with an embedding but no pocket.safetensors does not exist on
        # that engine, and saying so is better than speaking in somebody else's
        # voice. Whichever local voice has not been carried across will do.
        local = voice_lib.catalog(QWEN)
        uncloned = next((v["id"] for v in local if not v["pocket"]), None)
        if uncloned is None:
            self.skipTest("every local voice has been carried across")
        with self.assertRaises(LookupError):
            voice_lib.resolve(uncloned, "embedding", POCKET)

    def test_a_built_in_is_not_found_on_the_other_engine(self):
        with self.assertRaises(LookupError):
            voice_lib.resolve("alba", "embedding", QWEN)

    def test_substring_matching_still_works(self):
        voice, _ = voice_lib.resolve("mich", "embedding", POCKET)
        self.assertEqual(voice["id"], "michael")


class TheCyrillicGuard(unittest.TestCase):
    """Pocket TTS does not fail on Cyrillic, it runs away -- 11.4 seconds of
    audio for a three-second sentence, measured. So it is taken out first."""

    def test_qwen_is_left_alone(self):
        line = "Сега ще проверя как звучи това."
        self.assertEqual(voice_lib.speakable(line, state=QWEN), (line, None))

    def test_english_is_left_alone_on_either_engine(self):
        line = "All English here, nothing to skip."
        self.assertEqual(voice_lib.speakable(line, state=POCKET), (line, None))

    def test_a_mixed_line_keeps_the_english_and_says_what_went(self):
        speech, note = voice_lib.speakable(
            "Готово! The parser is fixed.", state=POCKET)
        self.assertIn("6 Cyrillic characters", speech)
        self.assertIn("The parser is fixed.", speech)
        self.assertNotIn("Готово", speech)
        self.assertEqual(note, "skipped 6 Cyrillic characters")

    def test_a_whole_cyrillic_line_says_so_and_nothing_else(self):
        speech, note = voice_lib.speakable(
            "Сега ще проверя как звучи това на български.", state=POCKET)
        self.assertEqual(speech, voice_lib.SKIPPED_ALL.format(engine="Pocket TTS"))
        self.assertIn("all Cyrillic", note)

    def test_breeze_is_guarded_too_and_named_in_the_warning(self):
        # English and Chinese only, by its own model card.
        speech, _ = voice_lib.speakable("Сега ще проверя.", state={"engine": "breeze"})
        self.assertIn("Breeze can't read it", speech)
        speech, note = voice_lib.speakable("Готово! The parser is fixed.",
                                           state={"engine": "breeze"})
        self.assertIn("The parser is fixed.", speech)
        self.assertEqual(note, "skipped 6 Cyrillic characters")

    def test_one_character_is_not_called_characters(self):
        speech, _ = voice_lib.speakable("The д key is stuck on this keyboard.",
                                        state=POCKET)
        self.assertIn("one Cyrillic character", speech)
        self.assertNotIn("1 Cyrillic characters", speech)

    def test_whole_words_go_rather_than_letters(self):
        # A stump left behind reads as some other word, which is worse to hear
        # than the word being gone.
        speech, _ = voice_lib.speakable("The файлът is ready.", state=POCKET)
        self.assertNotIn("ът", speech)
        self.assertNotIn("The The", speech)
        self.assertIn("is ready.", speech)

    def test_punctuation_left_behind_is_tidied(self):
        speech, _ = voice_lib.speakable("Сега, добре — ok?", state=POCKET)
        self.assertTrue(speech.endswith("ok?"), speech)
        self.assertNotIn(" ,", speech)


class ThePocketEngineItself(unittest.TestCase):
    def test_it_reports_the_rate_everything_downstream_assumes(self):
        self.assertEqual(pocket_engine.SAMPLE_RATE, 24000)

    def test_the_sexes_are_known_for_every_shipped_voice(self):
        for row in pocket_engine.VOICES:
            self.assertEqual(len(row), 4, row)
            self.assertIn(pocket_engine.sex_of(row[0]), ("female", "male"), row[0])

    def test_alba_is_a_man(self):
        # The one the obvious shortcut gets wrong, and the reason this table is
        # transcribed rather than inferred. Pinned so nobody "tidies" it back.
        self.assertEqual(pocket_engine.sex_of("alba"), "male")

    def test_styles_are_only_the_two_we_know_or_blank(self):
        for vid, _, style, _ in pocket_engine.VOICES:
            self.assertIn(style, ("reading", "conversation", ""), vid)
        self.assertEqual(pocket_engine.style_of("stuart_bell"), "reading")
        self.assertEqual(pocket_engine.style_of("michael"), "conversation")
        self.assertEqual(pocket_engine.style_of("cosette"), "")

    def test_french_takes_the_only_french_model_there_is(self):
        # Asking for "french" fails with a message telling you to ask for
        # french_24l. Fine to read once, poor to hit mid-answer.
        self.assertEqual(pocket_engine.model_for("french"), "french_24l")
        self.assertEqual(pocket_engine.model_for("english"), "english")

    def test_every_language_maps_to_a_config_that_exists(self):
        names = pocket_engine._config_names()
        if not names:
            self.skipTest("cannot see the package's config folder")
        for lang in pocket_engine.LANGUAGES:
            self.assertIn(pocket_engine.model_for(lang), names, lang)

    def test_a_failed_reload_keeps_the_engine_able_to_speak(self):
        # It used to set model to None first, so a load that raised left every
        # later answer saying "load_models() first" instead of what went wrong.
        eng = pocket_engine.Engine(language="english")
        eng.model, eng._states = object(), {"alba": "state"}
        eng.load_models = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))
        with self.assertRaises(RuntimeError):
            eng.ensure_language("italian")
        self.assertIsNotNone(eng.model)
        self.assertEqual(eng.language, "english")
        self.assertEqual(eng._states, {"alba": "state"})

    def test_an_unknown_voice_is_a_lookup_error_not_a_crash(self):
        eng = pocket_engine.Engine()
        eng.model = object()          # far enough in to reach the name check
        with self.assertRaises(LookupError):
            eng.voice_state("nobody-by-that-name")


class AFailedStartIsNotTheEnd(unittest.TestCase):
    """2026-09-25: a Breeze install stopped halfway left the config naming a
    Breeze that was not there. The engine failed to start, its thread ended,
    and picking Pocket afterwards changed nothing until a restart. Now the
    thread carries on, and a pick made while nothing runs wakes it."""

    def setUp(self):
        import queue
        import threading

        import speak_server

        self.speak_server = speak_server
        self.live = {"engine": "breeze"}
        self.builds = []

        def build(state):
            self.builds.append(state.get("engine"))
            if state.get("engine") == "breeze":
                raise RuntimeError("Breeze TTS 2 is not where the config says")
            return state.get("engine"), object()

        sp = self.sp = speak_server.Speaker.__new__(speak_server.Speaker)
        sp.state = dict(self.live)
        sp.jobs = queue.Queue()
        sp.lock = threading.Lock()
        sp.current = None
        sp.error = None
        sp.ready = threading.Event()
        sp.nudge = threading.Event()
        sp.engine_name = None
        sp._live = lambda: dict(self.live)
        for target, name, value in ((speak_server, "build_engine", build),
                                    (speak_server, "log", lambda *_a, **_k: None),
                                    (speak_server, "rss_mb", lambda: 0),
                                    (voice_lib, "engine_of", lambda s: s.get("engine"))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.thread = threading.Thread(target=sp._engine_loop, daemon=True)
        self.thread.start()
        self.assertTrue(sp.ready.wait(3))

    def wait_for(self, check, seconds=5.0):
        import time
        until = time.time() + seconds
        while time.time() < until:
            if check():
                return True
            time.sleep(0.05)
        return False

    def test_the_thread_outlives_a_failed_start(self):
        self.assertIsNotNone(self.sp.error)
        self.assertTrue(self.thread.is_alive())

    def test_a_working_pick_loads_without_anything_said(self):
        self.live["engine"] = "pocket"
        self.sp.nudge.set()
        self.assertTrue(self.wait_for(lambda: self.sp.error is None and self.sp.engine_name == "pocket"))
        self.assertEqual(self.builds, ["breeze", "pocket"])

    def test_no_nudge_no_load(self):
        # A pick is otherwise loaded on the next thing said, as before: waking
        # is only for when nothing is running at all.
        self.live["engine"] = "pocket"
        self.assertFalse(self.wait_for(lambda: len(self.builds) > 1, seconds=1.5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
