"""Codex through its `notify` setting, without loading the speech model.

Codex used to be heard by reading its rollouts under ~/.codex/sessions. Now its
notify program (speak_hook.py) is handed the finished turn as a JSON argument
and passes it to the engine like a Claude Stop hook.

    python test_codex_voice.py
"""
import json
import unittest
from unittest.mock import patch

import speak_hook
import speak_server
import voice_lib

voice_lib.notify = voice_lib._unlogged

STATE = {"enabled": True, "engine": "pocket", "voice": "abby", "source": "embedding",
         "port": 8765, "narrate": True, "alerts": True, "maxChars": 4000,
         "fullMaxChars": 4000, "narrateMaxChars": 240}


def turn(text, **extra):
    return json.dumps({"type": "agent-turn-complete", "thread-id": "t-123",
                       "turn-id": "1", "cwd": r"C:\code\abby",
                       "input-messages": ["fix the build please"],
                       "last-assistant-message": text, **extra})


class WhatNotifyHands(unittest.TestCase):
    def test_a_finished_turn_is_a_stop(self):
        body = speak_hook.codex_body(turn("Done."))
        self.assertEqual(body["event"], "Stop")
        self.assertEqual(body["client"], "codex")
        self.assertEqual(body["text"], "Done.")
        self.assertEqual(body["transcript_path"], "codex:t-123")
        self.assertEqual(body["prompt"], "fix the build please")

    def test_anything_else_is_nothing(self):
        for arg in ("not json", json.dumps({"type": "approval-requested"}),
                    turn("   "), json.dumps([1, 2])):
            self.assertIsNone(speak_hook.codex_body(arg), arg)

    def test_the_old_notify_program_is_still_run(self):
        started = []
        with patch("subprocess.Popen", lambda args, **kw: started.append(args)), \
                patch.object(speak_hook, "trace", lambda msg: None):
            speak_hook.pass_on(["--then", "computer-use.exe", "turn-ended", turn("x")])
            speak_hook.pass_on([turn("x")])
        self.assertEqual(started, [["computer-use.exe", "turn-ended", turn("x")]])

    def test_main_sends_it_while_the_voice_is_on(self):
        sent = []
        with patch.object(voice_lib, "load_state", lambda: dict(STATE)), \
                patch.object(speak_hook, "send", lambda state, body: sent.append(body)), \
                patch.object(speak_hook, "trace", lambda msg: None):
            speak_hook.main([turn("Body.\n\n## TL;DR\n\n- Abby is ready.")])
            with patch.object(voice_lib, "load_state", lambda: dict(STATE, enabled=False)):
                speak_hook.main([turn("Again.")])
        self.assertEqual(len(sent), 1)


class WhatTheEngineSays(unittest.TestCase):
    def setUp(self):
        self.jobs = []
        speaker = type("Speaker", (), {"submit": lambda _, job, barge=False:
                                       self.jobs.append(job)})()
        with patch.object(voice_lib, "load_state", lambda: {}):
            self.book = speak_server.SessionBook(speaker)
        for name, value in (("already_spoken", lambda text, remember=True: False),
                            ("resolve", lambda *a, **k: ({"id": "abby", "name": "Abby"}, {}))):
            patcher = patch.object(voice_lib, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_only_the_summary_is_spoken(self):
        body = speak_hook.codex_body(turn("Technical body.\n\n## TL;DR\n\n- Abby is ready."))
        self.book.hook(body, dict(STATE))
        self.assertEqual(self.jobs[-1].text, "Abby is ready.")

    def test_it_appears_in_the_panel_under_its_first_request(self):
        self.book.hook(speak_hook.codex_body(turn("Done.")), dict(STATE))
        row = self.book.sessions(state=dict(STATE))[0]
        self.assertEqual(row["path"], "codex:t-123")
        self.assertEqual(row["label"], "fix the build please")
        self.assertEqual(row["project"], "abby")

    def test_a_muted_task_is_silent(self):
        state = dict(STATE, mutedSessions=["codex:t-123"])
        reply = self.book.hook(speak_hook.codex_body(turn("Done.")), state)
        self.assertEqual(self.jobs, [])
        self.assertIn("muted", reply["why"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
