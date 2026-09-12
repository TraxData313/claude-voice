"""Verify Codex transcript selection without loading the speech model."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import speak_server
import voice_lib


class CodexVoiceTests(unittest.TestCase):
    def setUp(self):
        self.watcher = speak_server.TranscriptWatcher(None)
        self.spoken = []
        self.watcher._say = lambda speech, kind, state, path: self.spoken.append(speech)
        self.state = voice_lib.load_state()

    def message(self, text, phase="final_answer", **extra):
        return json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "assistant", "phase": phase,
            "content": [{"type": "output_text", "text": text}], **extra}})

    def test_only_summary_is_spoken(self):
        self.watcher._consider(self.message("Technical body.\n\n## TL;DR\n\n- Abby is ready."), self.state, "task")
        self.assertEqual(self.spoken, ["Abby is ready."])

    def test_private_and_duplicate_records_are_silent(self):
        for entry in (
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "duplicate"}},
            {"type": "response_item", "payload": {"type": "reasoning", "text": "private"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "tool"}},
        ):
            self.watcher._consider(json.dumps(entry), self.state, "task")
        self.watcher._consider(self.message("private", phase="analysis"), self.state, "task")
        self.watcher._consider(self.message("tool", recipient="functions.exec"), self.state, "task")
        self.assertEqual(self.spoken, [])

    def test_narration_toggle_keeps_final_answers(self):
        state = {**self.state, "narrate": False}
        self.watcher._consider(self.message("Working.", "commentary"), state, "task")
        self.watcher._consider(self.message("Finished."), state, "task")
        self.assertEqual(self.spoken, ["Finished."])

    def test_partial_record_is_retried_without_history_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.jsonl"
            path.write_text(self.message("Old history.") + "\n", encoding="utf-8")
            watcher = self.watcher
            watcher.OFFSETS = str(Path(directory) / "offsets.json")
            watcher.offsets = {}
            watcher._transcripts = lambda: [(str(path), path.stat().st_size, path.stat().st_mtime)]
            with patch.object(voice_lib, "load_state", return_value={**self.state, "enabled": True, "watch": True}):
                watcher._sweep()
                record = (self.message("Café is ready.") + "\n").encode("utf-8")
                with path.open("ab") as output:
                    output.write(record[:-5])
                watcher._sweep()
                self.assertEqual(self.spoken, [])
                with path.open("ab") as output:
                    output.write(record[-5:])
                watcher._sweep()
                watcher._sweep()
                self.assertEqual(self.spoken, ["Café is ready."])

    def test_codex_discovery_without_claude_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            self.watcher.PROJECTS = os.path.join(directory, "absent")
            self.watcher.CODEX_SESSIONS = directory
            path = Path(directory) / "2026" / "09" / "12" / "rollout.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"type": "session_meta", "payload": {
                "cwd": r"C:\work\demo", "source": {"subagent": {}},
                "thread_source": "agent"}}) + "\n", encoding="utf-8")
            with patch.object(voice_lib, "load_state", return_value={"watchCodex": True}):
                self.assertEqual(len(list(self.watcher._transcripts())), 1)
            self.watcher._ensure_label(str(path))
            self.assertTrue(self.watcher.headless[str(path)])

    def test_resumed_old_codex_task_is_followed_by_size(self):
        with tempfile.TemporaryDirectory() as directory:
            watcher = self.watcher
            watcher.PROJECTS = os.path.join(directory, "absent")
            watcher.CODEX_SESSIONS = directory
            watcher.OFFSETS = str(Path(directory) / "offsets.json")
            watcher.offsets = {}
            path = Path(directory) / "2026" / "01" / "01" / "rollout.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"type": "session_meta", "payload": {
                "cwd": r"C:\work\demo", "source": "vscode",
                "thread_source": "user"}}) + "\n", encoding="utf-8")
            old = time.time() - watcher.FRESH_SECONDS - 60
            os.utime(path, (old, old))
            state = {**self.state, "enabled": True, "watch": True, "watchCodex": True}
            with patch.object(voice_lib, "load_state", return_value=state):
                self.assertEqual(list(watcher._transcripts()), [])
                baseline = path.stat().st_size
                with path.open("ab") as output:
                    output.write((self.message("This resumed task speaks.") + "\n").encode("utf-8"))
                # Match Codex Desktop: appending does not make the rollout look new.
                os.utime(path, (old, old))
                found = list(watcher._transcripts())
                self.assertEqual([row[0] for row in found], [str(path)])
                self.assertEqual(watcher.offsets[str(path)], baseline)
                watcher._sweep()
                self.assertEqual(self.spoken, ["This resumed task speaks."])
                self.assertEqual(len(watcher.sessions()), 1)


if __name__ == "__main__":
    unittest.main()
