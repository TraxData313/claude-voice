"""
The mood that rides beside the words: where it is kept, and who is allowed to
use it.

Nothing here loads a model, so nothing here can prove that the engine's 0x28
field changes how a sentence sounds -- only that a mood reaches the engine that
has a field for it and reaches no engine that does not. That second half is the
part worth a test: handing Pocket a keyword it never declared would raise on the
sentence rather than be ignored, and the failure would arrive as silence.

    python test_mood.py
"""

import ctypes
import unittest
from unittest.mock import patch

import qwen_engine
import speak_server


def job(instruction=None, **kwargs):
    return speak_server.Job(["hello"], "abby", dict(kwargs), text="hello",
                            instruction=instruction)


class Speaker:
    """Just enough of one to ask _kwargs a question."""

    def __init__(self, engine_name):
        self.engine_name = engine_name

    _kwargs = speak_server.Speaker._kwargs


class WhatAJobKeeps(unittest.TestCase):
    def test_blank_moods_are_no_mood(self):
        for given in (None, "", "   ", "\n"):
            self.assertIsNone(job(given).instruction)

    def test_a_mood_is_kept_in_the_caller_s_own_words(self):
        self.assertEqual(job("  sound daring and brave ").instruction,
                         "sound daring and brave")

    def test_the_panel_can_draw_it(self):
        self.assertEqual(job("sound tired").as_dict()["instruction"], "sound tired")
        self.assertIsNone(job().as_dict()["instruction"])


class WhoGetsIt(unittest.TestCase):
    def test_qwen_is_called_with_it(self):
        called = Speaker("qwen")._kwargs(job("sound tired", embedding_path="a.bin"))
        self.assertEqual(called, {"embedding_path": "a.bin", "instruction": "sound tired"})

    def test_pocket_never_sees_a_keyword_it_does_not_declare(self):
        given = job("sound tired", pocket_voice="abby")
        self.assertEqual(Speaker("pocket")._kwargs(given), {"pocket_voice": "abby"})
        # And the job keeps it, so a replay after an engine swap still has it.
        self.assertEqual(given.instruction, "sound tired")

    def test_an_engine_that_has_not_loaded_yet_gets_nothing_extra(self):
        self.assertEqual(Speaker(None)._kwargs(job("sound tired", embedding_path="a.bin")),
                         {"embedding_path": "a.bin"})

    def test_no_mood_leaves_the_kwargs_exactly_as_they_were(self):
        original = {"embedding_path": "a.bin"}
        given = speak_server.Job(["hi"], "abby", original)
        self.assertIs(Speaker("qwen")._kwargs(given), original)


class WhatTheServerPromises(unittest.TestCase):
    def test_the_answer_follows_the_configured_engine_not_the_loaded_one(self):
        # The engine thread swaps before it speaks, so the config is what the
        # next sentence actually comes out of.
        with patch.object(speak_server.voice_lib, "load_state", return_value={"engine": "qwen"}):
            self.assertTrue(speak_server._takes_instruction())
        with patch.object(speak_server.voice_lib, "load_state", return_value={"engine": "pocket"}):
            self.assertFalse(speak_server._takes_instruction())

    def test_an_unreadable_config_is_a_no_rather_than_a_crash(self):
        with patch.object(speak_server.voice_lib, "load_state", side_effect=OSError):
            self.assertFalse(speak_server._takes_instruction())

    def test_a_mood_cannot_grow_into_the_prompt(self):
        # Whatever else it is, it is a handful of words about delivery. A
        # caller that sends a paragraph gets a handful of words.
        self.assertLessEqual(speak_server.MAX_INSTRUCTION, 400)


class Finder:
    """Just enough of jvm.dll to answer the one question asked of it."""

    def __init__(self, count, rc=0):
        self.count, self.rc, self.asked = count, rc, False

    def __call__(self, buf, size, found):
        self.asked = True
        found._obj.value = self.count
        for i in range(min(self.count, size)):
            buf[i] = 0xBEEF0000 + i
        return self.rc

    @property
    def JNI_GetCreatedJavaVMs(self):
        return self


class ComingBackToQwen(unittest.TestCase):
    """A process gets one JVM. The second Qwen in it has to join the first.

    The joining itself needs a real VM and lives in the engine; what is checked
    here is the half that decides, because that half got it wrong in a way no
    error could report: without argtypes the count never came back written, so
    the lookup said "no VM" whether or not there was one.
    """

    def test_no_vm_yet_means_create_one(self):
        finder = Finder(0)
        self.assertIsNone(qwen_engine._existing_jvm_env(finder))
        self.assertTrue(finder.asked)

    def test_a_refusal_to_answer_also_means_create_one(self):
        self.assertIsNone(qwen_engine._existing_jvm_env(Finder(1, rc=-1)))

    def test_the_lookup_is_typed(self):
        # The regression itself, and the only shape it can be caught in from
        # here. Without these three the count never comes back written, the
        # lookup says "no VM" whichever is true, and the engine tries to create
        # a second one -- which is refused, and that is a dead engine until the
        # process restarts. There is no going further without a real VM: the
        # next step dereferences what the lookup handed back.
        finder = Finder(0)
        self.assertIsNone(qwen_engine._existing_jvm_env(finder))
        self.assertEqual(finder.restype, ctypes.c_int)
        self.assertEqual(len(finder.argtypes), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
