"""
Breeze TTS 2, the third engine: everything decided before any audio exists.

Nothing here loads a model or needs a graphics card. What is tested is the
part that decides whether the right words, sounds and mood reach Breeze in the
right spelling -- and the client that talks to its server, against a small
fake one that streams PCM the way the real one does, so hanging up, a busy
server and the derail guard are exercised for real rather than described.

    python test_breeze.py
"""

import array
import email.parser
import http.server
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import breeze_engine
import breeze_setup
import speak_server
import voice_lib

BREEZE = {"engine": "breeze", "voicesDir": "voices"}


class WhatItCanDo(unittest.TestCase):
    def test_the_table_and_the_engine_agree_on_the_sounds(self):
        # Two lists of the same four things drift the first time one is edited.
        self.assertEqual(tuple(voice_lib.engine_can("breeze", "events")),
                         breeze_engine.EVENTS)
        self.assertEqual(voice_lib.EVENT_NAMES, breeze_engine.EVENTS)

    def test_only_breeze_makes_sounds_and_pocket_takes_no_mood(self):
        self.assertEqual(voice_lib.engine_can("qwen", "events"), ())
        self.assertTrue(voice_lib.engine_can("qwen", "instruction"))
        self.assertFalse(voice_lib.engine_can("pocket", "instruction"))
        self.assertTrue(voice_lib.engine_can("breeze", "instruction"))
        self.assertEqual(voice_lib.engine_can("nonsense", "events"), ())

    def test_the_server_passes_a_mood_to_breeze(self):
        self.assertIn("breeze", speak_server.INSTRUCTED_ENGINES)
        self.assertNotIn("pocket", speak_server.INSTRUCTED_ENGINES)

    def test_it_is_an_engine_with_a_description(self):
        self.assertIn("breeze", voice_lib.ENGINES)
        self.assertTrue(voice_lib.engine_info("breeze")["blurb"])


class ItsVoices(unittest.TestCase):
    def test_abby_ships_with_a_clip_and_its_words(self):
        voices = {v["id"]: v for v in voice_lib.catalog(BREEZE)}
        self.assertIn("abby", voices)
        abby = voices["abby"]
        self.assertTrue(abby["breeze"].endswith("breeze-reference.wav"))
        self.assertTrue(abby["breezeText"].startswith("Hii!"))

    def test_the_words_are_the_ones_the_clip_was_rendered_from(self):
        # A transcript that drifts from the audio teaches the model a wrong
        # alignment. The clip was rendered from make_pocket_voice.REFERENCE.
        import make_pocket_voice

        abby = next(v for v in voice_lib.catalog(BREEZE) if v["id"] == "abby")
        self.assertEqual(abby["breezeText"], make_pocket_voice.REFERENCE)

    def test_resolve_hands_over_the_clip_and_the_words(self):
        _, kwargs = voice_lib.resolve("abby", "embedding", BREEZE)
        self.assertEqual(set(kwargs), {"breeze_ref", "breeze_ref_text"})
        self.assertTrue(os.path.exists(kwargs["breeze_ref"]))

    def test_a_voice_without_a_clip_is_not_offered(self):
        # Max has an embedding and nothing Breeze can learn him from yet.
        ids = {v["id"] for v in voice_lib.catalog(BREEZE)}
        if os.path.exists(os.path.join("voices", "male", "max", "breeze-reference.wav")):
            self.skipTest("max has been given a clip")
        self.assertNotIn("max", ids)

    def test_a_clip_with_no_words_beside_it_does_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "breeze-reference.wav"), "wb").close()
            self.assertEqual(voice_lib._breeze_reference(d), (None, ""))
            with open(os.path.join(d, "breeze-reference.txt"), "w", encoding="utf-8") as fh:
                fh.write("Exactly these words.\n")
            clip, words = voice_lib._breeze_reference(d)
            self.assertTrue(clip.endswith("breeze-reference.wav"))
            self.assertEqual(words, "Exactly these words.")


class NeverFetchedBySurprise(unittest.TestCase):
    """Toni's rule: nobody gets eleven gigabytes because an update arrived."""

    def test_an_install_nobody_asked_for_is_not_an_install(self):
        self.assertFalse(voice_lib.engine_ready("breeze", {}))
        self.assertFalse(voice_lib.engine_ready("breeze", {
            "breezePython": "C:\\nowhere\\python.exe", "breezeDir": "C:\\nowhere",
            "breezeModel": "C:\\nowhere"}))

    def test_switching_to_it_uninstalled_says_how_and_changes_nothing(self):
        with patch.object(voice_lib, "patch_state") as wrote:
            with self.assertRaises(LookupError) as caught:
                voice_lib.set_engine("breeze", {"engine": "qwen"})
        self.assertIn("install breeze", str(caught.exception))
        wrote.assert_not_called()

    def test_the_defaults_name_no_install(self):
        for key in ("breezePython", "breezeDir", "breezeModel"):
            self.assertEqual(voice_lib.DEFAULTS[key], "")


class SoundsInTheText(unittest.TestCase):
    def test_the_usual_spellings_all_mean_the_four_sounds(self):
        said = "(laughs) [giggles] *chuckling* (sighing) (coughs) (clears her throat) (ahem)"
        self.assertEqual(voice_lib.events_in(said),
                         ["laugh", "laugh", "laugh", "sigh", "cough",
                          "clears throat", "clears throat"])

    def test_a_word_either_side_is_allowed(self):
        self.assertEqual(voice_lib.events_in("(laughs softly) and (a nervous laugh)"),
                         ["laugh", "laugh"])

    def test_a_bare_word_is_a_word_not_a_stage_direction(self):
        self.assertEqual(voice_lib.events_in("I laugh at that, and sigh."), [])

    def test_breeze_gets_its_own_spelling(self):
        self.assertEqual(voice_lib.perform("*giggles* Okay, fine.", "breeze"),
                         "(laugh) Okay, fine.")
        self.assertEqual(voice_lib.perform("So (Laughing) it kept going.", "breeze"),
                         "So (laugh) it kept going.")

    def test_an_engine_that_cannot_make_it_does_not_read_it_either(self):
        # Said aloud, "(laugh)" is the word laugh -- worse than nothing.
        self.assertEqual(voice_lib.perform("(laugh) Okay, fine.", "qwen"), "Okay, fine.")
        self.assertEqual(voice_lib.perform("So I told it, (laughs) , to stop.", "pocket"),
                         "So I told it, to stop.")
        self.assertEqual(voice_lib.perform("That's it (sigh).", "qwen"), "That's it.")

    def test_a_line_that_is_only_a_sound(self):
        self.assertEqual(voice_lib.perform("(laugh)", "breeze"), "(laugh)")
        self.assertEqual(voice_lib.perform("(laugh)", "qwen"), "")

    def test_text_without_any_is_handed_back_untouched(self):
        line = "Nothing  to see (really) here , honestly."
        self.assertIs(voice_lib.perform(line, "breeze"), line)


class Moods(unittest.TestCase):
    def test_a_name_becomes_the_whole_instruction(self):
        self.assertEqual(voice_lib.mood_instruction("sad"),
                         ("sad", "Speak slowly and sadly, quiet and downcast, "
                                 "with long pauses."))
        self.assertEqual(voice_lib.mood_instruction("  Whispering ")[0], "whisper")

    def test_an_unknown_one_is_no_mood(self):
        self.assertEqual(voice_lib.mood_instruction("grumpy-ish"), (None, ""))
        self.assertEqual(voice_lib.mood_instruction(None), (None, ""))

    def test_one_slip_in_the_spelling_is_forgiven(self):
        # "wisper" was typed into the panel on 2026-09-24, and went out as if
        # no mood had been asked for.
        for typed, mood in (("wisper", "whisper"), ("exited", "excited"),
                            ("suprised", "surprised"), ("serius", "serious"),
                            ("whipser", "whisper"), ("playfull", "playful"),
                            ("cheerfull", "happy"), ("angery", "angry")):
            self.assertEqual(voice_lib.mood_name(typed), mood, typed)

    def test_a_short_word_one_letter_off_is_a_different_word(self):
        for word in ("said", "made", "wary", "tire", "tried", "sappy", "series",
                     "calmer", "whistle"):
            self.assertIsNone(voice_lib.mood_name(word), word)

    def test_no_spelling_of_a_sound_is_ever_taken_for_a_mood(self):
        for aliases in voice_lib._EVENT_ALIASES.values():
            for sound in aliases:
                self.assertIsNone(voice_lib.mood_name(sound), sound)

    def test_a_mood_with_nothing_else_said_about_the_sound(self):
        for typed, mood in (("wisper this line", "whisper"), ("very sad, please", "sad"),
                            ("in a whisper", "whisper"), ("say it sadly", "sad"),
                            ("Excited!!", "excited")):
            self.assertEqual(voice_lib.mood_name(typed), mood, typed)

    def test_anything_more_is_somebody_s_own_instruction(self):
        # Slowly is not in the whisper they would be given instead, "don't" is
        # the opposite of asking, and two moods are a mix no preset is.
        for typed in ("Whisper it slowly, like a secret", "don't whisper",
                      "sad and slow", "whispering sadly", "please", ""):
            self.assertIsNone(voice_lib.mood_name(typed), typed)

    def test_every_mood_is_a_whole_instruction_not_one_word(self):
        # The lesson of docs/engines.md: one adjective steers nothing.
        for name, words in voice_lib.MOODS.items():
            self.assertGreaterEqual(len(words.split()), 6, name)
            self.assertLessEqual(len(words), speak_server.MAX_INSTRUCTION, name)

    def test_the_ones_heard_are_moods(self):
        for name in voice_lib.MOODS_HEARD:
            self.assertIn(name, voice_lib.MOODS)


class HowLongItMayRun(unittest.TestCase):
    def test_plain_text_is_judged_exactly_as_before(self):
        line = "Okay, the tests pass now. All forty-two of them."
        self.assertAlmostEqual(voice_lib.expected_seconds(line),
                               voice_lib.GRACE_SECONDS + len(line) / voice_lib.CHARS_PER_SECOND)

    def test_a_written_sound_gets_time_of_its_own(self):
        plain = voice_lib.expected_seconds("Okay, fine.")
        laughed = voice_lib.expected_seconds("(laugh) Okay, fine.")
        self.assertAlmostEqual(laughed - plain, voice_lib.EVENT_SECONDS, places=1)

    def test_the_slow_sad_take_that_was_heard_is_not_a_derail(self):
        # Measured 2026-09-24: 11.44 s for this line under the sad mood, past
        # the plain ceiling. Stopping it would have cut her off mid-sentence.
        line = "I looked everywhere, and I couldn't find it. I'm sorry."
        self.assertEqual(voice_lib.audio_verdict(line, 11.44), "derail")
        self.assertEqual(voice_lib.audio_verdict(line, 11.44, voice_lib.INSTRUCTED_SLACK),
                         "ok")

    def test_the_server_only_loosens_it_for_a_mood_the_engine_performs(self):
        job = speak_server.Job(["hi"], "abby", {}, text="hi", instruction="sad")

        class Speaker:
            _slack = speak_server.Speaker._slack

            def __init__(self, name):
                self.engine_name = name

        self.assertEqual(Speaker("breeze")._slack(job), voice_lib.INSTRUCTED_SLACK)
        self.assertEqual(Speaker("pocket")._slack(job), 1.0)


class WhatACallerIsTold(unittest.TestCase):
    def test_capabilities_say_what_the_next_line_can_do(self):
        caps = speak_server._capabilities({"engine": "breeze", "voice": "abby"}, "qwen")
        self.assertEqual(caps["engine"], "breeze")
        self.assertEqual(caps["engineLoaded"], "qwen")
        self.assertTrue(caps["instruction"])
        self.assertEqual(caps["events"], list(breeze_engine.EVENTS))
        self.assertIn("sad", caps["moods"])
        self.assertEqual(set(caps["engines"]), set(voice_lib.ENGINES))

    def test_no_moods_are_offered_where_none_would_be_performed(self):
        caps = speak_server._capabilities({"engine": "pocket"}, "pocket")
        self.assertFalse(caps["instruction"])
        self.assertEqual(caps["moods"], {})
        self.assertEqual(caps["events"], [])


class Splitting(unittest.TestCase):
    def test_short_text_is_one_generation(self):
        self.assertEqual(breeze_engine.split_text("Hello there."), ["Hello there."])

    def test_long_text_splits_at_sentence_ends_and_loses_nothing(self):
        sentence = "This sentence is about sixty characters long, give or take. "
        text = (sentence * 20).strip()
        parts = breeze_engine.split_text(text)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), breeze_engine.MAX_CHARS)
            self.assertTrue(part.endswith("."), part)
        self.assertEqual(" ".join(parts), text)

    def test_a_sentence_with_no_end_is_cut_at_a_comma_or_a_space(self):
        text = ", ".join(["on and on"] * 120)
        parts = breeze_engine.split_text(text)
        for part in parts:
            self.assertLessEqual(len(part), breeze_engine.MAX_CHARS)
        self.assertEqual(" ".join(parts).replace(",", "").split(),
                         text.replace(",", "").split())


class TheWire(unittest.TestCase):
    def test_multipart_carries_the_fields_and_the_clip(self):
        body, kind = breeze_engine.multipart(
            {"text": "(laugh) Ha — it works", "cfg_scale": 4.0},
            {"ref_audio": ("reference.wav", b"RIFF1234", "audio/wav")})
        msg = email.parser.BytesParser().parsebytes(
            b"Content-Type: " + kind.encode() + b"\r\n\r\n" + body)
        parts = {p.get_param("name", header="content-disposition"): p
                 for p in msg.get_payload()}
        self.assertEqual(parts["text"].get_payload(decode=True).decode("utf-8"),
                         "(laugh) Ha — it works")
        self.assertEqual(parts["cfg_scale"].get_payload(decode=True), b"4.0")
        self.assertEqual(parts["ref_audio"].get_payload(decode=True), b"RIFF1234")

    def test_pcm_comes_back_as_the_floats_the_server_scaled_from(self):
        pcm = array.array("h", [0, 32767, -32767, 16384]).tobytes()
        self.assertEqual(list(breeze_engine.pcm_to_float(pcm)),
                         [0.0, 1.0, -1.0, array.array("f", [16384 / 32767.0])[0]])


class FakeBreeze(http.server.BaseHTTPRequestHandler):
    """Streams PCM in pieces, the way breeze_infer.api does, and can be made
    to say it is busy, as the real one does just after being hung up on."""

    busy_left = 0
    seen = []
    pieces = 10
    samples_per_piece = 1920          # 80 ms at 24 kHz

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeBreeze.seen.append(body)
        if FakeBreeze.busy_left > 0:
            FakeBreeze.busy_left -= 1
            self.send_response(409)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/pcm")
        self.end_headers()
        piece = array.array("h", [1000] * self.samples_per_piece).tobytes()
        try:
            for _ in range(self.pieces):
                self.wfile.write(piece)
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass                       # hung up on, which is the point of some tests


class TheClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeBreeze)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.eng = breeze_engine.Engine("", "", "")
        cls.eng.port = cls.server.server_address[1]
        cls.clip = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        cls.clip.write(b"RIFF-not-really")
        cls.clip.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        os.unlink(cls.clip.name)

    def setUp(self):
        FakeBreeze.busy_left, FakeBreeze.seen = 0, []

    def speak(self, text="Hello.", on_piece=None, **kwargs):
        got = []
        total = self.eng.synthesize_streaming(
            text, on_piece or (lambda s, _c: got.append(len(s)) or True),
            breeze_ref=self.clip.name, breeze_ref_text="Exactly these words.", **kwargs)
        return total, got

    def test_everything_streamed_arrives(self):
        total, got = self.speak()
        self.assertEqual(total, FakeBreeze.pieces * FakeBreeze.samples_per_piece)
        self.assertEqual(sum(got), total)

    def test_a_mood_travels_with_guidance_and_a_plain_line_without(self):
        self.speak(instruction="Whisper quietly.")
        self.speak()
        directed, plain = FakeBreeze.seen
        self.assertIn(b"Whisper quietly.", directed)
        self.assertIn(b'name="cfg_scale"\r\n\r\n4.0', directed)
        self.assertNotIn(b'name="instruction"', plain)
        self.assertIn(b'name="cfg_scale"\r\n\r\n1.0', plain)

    def test_each_line_gets_a_fresh_seed(self):
        # With the API's own fixed seed, a take that missed its laugh would
        # miss it every time the same line was asked for.
        self.speak()
        self.speak()
        seeds = [body.split(b'name="seed"\r\n\r\n')[1].split(b"\r\n")[0]
                 for body in FakeBreeze.seen]
        self.assertNotEqual(seeds[0], seeds[1])

    def test_returning_false_hangs_up(self):
        total, _ = self.speak(on_piece=lambda s, _c: False)
        self.assertLess(total, FakeBreeze.pieces * FakeBreeze.samples_per_piece)

    def test_a_busy_server_is_waited_out(self):
        # Just after a hang-up the real server says 409 for about two seconds.
        FakeBreeze.busy_left = 3
        total, _ = self.speak()
        self.assertEqual(total, FakeBreeze.pieces * FakeBreeze.samples_per_piece)
        self.assertEqual(len(FakeBreeze.seen), 4)

    def test_the_derail_guard_stops_at_the_ceiling(self):
        # The guard allows the ceiling, a tenth over it and a second of grace,
        # so the stream has to run well past all three to be stopped.
        with patch.object(FakeBreeze, "pieces", 60):
            total, _ = self.speak(max_seconds=0.5)
        self.assertLess(total, 60 * FakeBreeze.samples_per_piece)
        # Checked once per read, and one read can carry up to 64 KB of PCM.
        self.assertLessEqual(total, int(0.5 * 24000 * 1.1) + 24000 + 65536 // 2)

    def test_long_text_goes_as_several_requests_into_one_stream(self):
        text = ("This sentence is about sixty characters long, give or take. " * 12).strip()
        self.speak(text)
        self.assertEqual(len(FakeBreeze.seen), len(breeze_engine.split_text(text)))

    def test_a_voice_with_no_words_is_refused_before_anything_is_sent(self):
        with self.assertRaises(ValueError):
            self.eng.synthesize_streaming("Hello.", lambda s, c: True,
                                          breeze_ref=self.clip.name, breeze_ref_text="")
        self.assertEqual(FakeBreeze.seen, [])


class TheMachineCheck(unittest.TestCase):
    CARD = {"name": "NVIDIA GeForce RTX 5080 Laptop GPU", "memory_mib": 16303,
            "driver": "591.91", "capability": (12, 0), "cuda": (13, 1)}

    def check(self, card, free=100.0):
        with patch.object(breeze_setup, "gpus", return_value=[card] if card else []), \
                patch.object(breeze_setup, "free_gb", return_value=free):
            return breeze_setup.check_machine("D:\\somewhere\\breeze")

    def test_the_laptop_it_was_measured_on_runs_it_at_full_speed(self):
        self.assertEqual(self.check(self.CARD)["verdict"], "full")

    def test_a_12_gb_card_runs_it_slowly_and_is_told_so(self):
        report = self.check({**self.CARD, "memory_mib": 12282})
        self.assertEqual(report["verdict"], "slow")
        self.assertIn("slower than speech", " ".join(r["have"] for r in report["rows"]))

    def test_no_card_too_small_too_old_or_no_room_is_a_no(self):
        self.assertEqual(self.check(None)["verdict"], "no")
        self.assertEqual(self.check({**self.CARD, "memory_mib": 8188})["verdict"], "no")
        self.assertEqual(self.check({**self.CARD, "capability": (7, 5)})["verdict"], "no")
        self.assertEqual(self.check({**self.CARD, "cuda": (12, 4)})["verdict"], "no")
        self.assertEqual(self.check(self.CARD, free=12.0)["verdict"], "no")

    def test_the_pins_keep_the_cuda_build(self):
        self.assertIn("torch==2.9.1+cu128", breeze_setup.CONSTRAINTS)
        self.assertTrue(breeze_setup.TORCH_INDEX.endswith("/cu128"))

    def test_everything_goes_inside_the_one_folder(self):
        where = "D:\\claude-voice\\breeze"
        for path in breeze_setup.paths(where).values():
            self.assertTrue(path.startswith(where), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
