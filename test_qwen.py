"""Qwen on PyTorch: everything around the model, without loading it.

Studio's DLLs are refused by Smart App Control, and Qwen on PyTorch is how
the Qwen engine runs where they are (2026-10-17). These are the decisions
around it: when Qwen counts as ready, what a voice's files become on the wire,
what the engine says it can do, and what the installer reads off the card.
The server itself needs CUDA and a 4 GB model, so it is not loaded here.

    python test_qwen.py
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import breeze_setup
import qwen_engine
import qwen_setup
import qwen_torch_engine
import voice_lib


def _tree(root, files):
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)


class Installed(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.python = os.path.join(self.dir, "env", "Scripts", "python.exe")
        self.model = os.path.join(self.dir, "weights")
        _tree(self.dir, {"env/Scripts/python.exe": "",
                         "weights/config.json": "{}",
                         "weights/speech_tokenizer/config.json": "{}"})

    def state(self, **more):
        return {"qwenPython": self.python, "qwenModel": self.model, **more}

    def test_it_is_there_only_with_its_python_its_weights_and_its_codec(self):
        self.assertTrue(qwen_torch_engine.available(self.python, self.model))
        os.remove(os.path.join(self.model, "speech_tokenizer", "config.json"))
        self.assertFalse(qwen_torch_engine.available(self.python, self.model))
        self.assertFalse(qwen_torch_engine.available("", self.model))

    def test_installed_it_is_how_qwen_runs_and_qwen_is_ready(self):
        state = self.state()
        self.assertEqual(voice_lib.qwen_backend(state), "torch")
        self.assertTrue(voice_lib.engine_ready("qwen", state))

    def test_on_pytorch_qwen_does_not_claim_to_take_a_mood(self):
        # The Base model has no instruction input; saying otherwise would have
        # sessions writing directions that are silently dropped.
        self.assertFalse(voice_lib.engine_can("qwen", "instruction", self.state()))
        self.assertTrue(voice_lib.engine_can("qwen", "instruction", {}))


class NotInstalled(unittest.TestCase):
    def test_studio_refused_by_smart_app_control_is_not_ready(self):
        studio = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, studio, True)
        _tree(studio, {"qwen3_tts.dll": ""})
        state = {"studioDir": studio}
        with patch.object(qwen_engine, "smart_app_control", lambda: "on"):
            self.assertFalse(voice_lib.engine_ready("qwen", state))
        with patch.object(qwen_engine, "smart_app_control", lambda: "off"):
            self.assertTrue(voice_lib.engine_ready("qwen", state))

    def test_switching_to_a_qwen_that_cannot_run_says_how_to_install_it(self):
        # It used to switch, then fall back to Pocket on its own -- a choice
        # that looked as if it would not stick.
        with patch.object(qwen_engine, "smart_app_control", lambda: "on"), \
                patch.object(voice_lib, "patch_state") as wrote:
            with self.assertRaises(LookupError) as caught:
                voice_lib.set_engine("qwen", {"studioDir": "", "engine": "pocket"})
        self.assertIn("install qwen", str(caught.exception))
        wrote.assert_not_called()


class Voices(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_an_embedding_is_sent_as_it_is(self):
        path = os.path.join(self.dir, "embedding.json")
        self.assertEqual(qwen_torch_engine.voice_request(embedding_path=path),
                         {"embedding": path})

    def test_the_closer_clone_uses_the_clip_and_its_words(self):
        _tree(self.dir, {"icl-prompt.json": "{}", "embedding.json": "[]",
                         "breeze-reference.wav": "", "breeze-reference.txt": " Hello there. \n"})
        got = qwen_torch_engine.voice_request(
            icl_prompt_path=os.path.join(self.dir, "icl-prompt.json"))
        self.assertEqual(got, {"clip": os.path.join(self.dir, "breeze-reference.wav"),
                               "clipText": "Hello there."})

    def test_without_a_clip_it_falls_back_to_the_embedding_not_studios_prompt(self):
        # Studio's icl-prompt.json carries a description where the transcript
        # belongs; Qwen reading against it garbles the line.
        _tree(self.dir, {"icl-prompt.json": "{}", "embedding.json": "[]"})
        got = qwen_torch_engine.voice_request(
            icl_prompt_path=os.path.join(self.dir, "icl-prompt.json"))
        self.assertEqual(got, {"embedding": os.path.join(self.dir, "embedding.json")})

    def test_no_voice_files_at_all_is_an_error_not_silence(self):
        with self.assertRaises(ValueError):
            qwen_torch_engine.voice_request()


class TheMachineCheck(unittest.TestCase):
    CARD = {"name": "RTX", "memory_mib": 16303, "driver": "617.42",
            "capability": (12, 0), "cuda": (13, 4)}

    def test_a_2026_driver_header_is_read(self):
        new = "| NVIDIA-SMI 617.42   KMD Version: 617.42   CUDA UMD Version: 13.4     |"
        old = "| NVIDIA-SMI 560.94   Driver Version: 560.94   CUDA Version: 12.6     |"
        self.assertEqual(breeze_setup.parse_cuda_version(new), (13, 4))
        self.assertEqual(breeze_setup.parse_cuda_version(old), (12, 6))
        self.assertIsNone(breeze_setup.parse_cuda_version("no header"))

    def test_a_16_gb_card_runs_it_in_full(self):
        with patch.object(breeze_setup, "gpus", lambda: [self.CARD]), \
                patch.object(breeze_setup, "free_gb", lambda _p: 500.0), \
                patch.object(qwen_setup.sys, "version_info", (3, 12, 10)):
            report = qwen_setup.check_machine("C:\\x")
        self.assertEqual(report["verdict"], "full", report["rows"])

    def test_no_nvidia_card_means_nothing_is_downloaded(self):
        with patch.object(breeze_setup, "gpus", lambda: []), \
                patch.object(breeze_setup, "free_gb", lambda _p: 500.0):
            self.assertEqual(qwen_setup.check_machine("C:\\x")["verdict"], "no")

    def test_the_pins_are_the_set_seen_to_load(self):
        with open(qwen_setup.REQUIREMENTS, encoding="utf-8") as fh:
            pins = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        self.assertIn("qwen-tts==0.1.1", pins)
        self.assertIn("torch==2.9.1+cu128", pins)
        self.assertTrue(all("==" in p for p in pins), "every package at an exact version")

    def test_it_says_what_the_panel_window_needs(self):
        for name in ("ENGINE", "TITLE", "TAGLINE", "LICENCE", "DOWNLOAD_GB"):
            self.assertTrue(getattr(qwen_setup, name), name)
            self.assertTrue(hasattr(breeze_setup, name), name)


class Splitting(unittest.TestCase):
    def test_long_text_goes_in_sentence_ended_pieces(self):
        text = " ".join(["This is a sentence of some length."] * 40)
        parts = qwen_torch_engine.split_text(text, qwen_torch_engine.MAX_CHARS)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= qwen_torch_engine.MAX_CHARS for p in parts))
        self.assertEqual(" ".join(parts), text)


if __name__ == "__main__":
    unittest.main()
