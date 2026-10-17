"""The hooks voice_lib writes into Claude Code's settings, and what 'status' says.

A bare "python speak_hook.py" ran perfectly until the day the folder moved.
Then python exited with 2 -- "can't open file" -- and 2 is the one exit code
Claude Code reads as "block": every prompt refused, every tool call refused, a
whole session locked out by a speech hook (2026-09-25). So the line is a guard
now, a sliver of Python that runs the hook as python would and exits 0 when
the file is gone. It lives in voice_lib (HOOK_GUARD, hook_command), which is
what the installer runs, so the test and the installer cannot drift apart.

Nothing here loads a model or touches the real settings. Scratch folders only.

    python test_hooks.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import voice_lib

HERE = os.path.dirname(os.path.abspath(__file__))
BASH = next((p for p in (r"C:\Program Files\Git\bin\bash.exe",
                         r"C:\Program Files (x86)\Git\bin\bash.exe") if os.path.isfile(p)), None)


def installer_guard():
    """The Python the installer puts after -c."""
    return voice_lib.HOOK_GUARD


STUB = ("import os, sys\n"
        "print(repr(sys.argv[0]))\n"
        "print(repr(sys.path[0]))\n"
        "print(repr(__name__))\n"
        "print(repr(sys.stdin.read()))\n"
        "sys.exit(7)\n")


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cv hooks ")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.guard = installer_guard()

    def run_guard(self, hook, payload="", cwd=None):
        r = subprocess.run([sys.executable, "-c", self.guard, hook], input=payload.encode(),
                           capture_output=True, cwd=cwd or self.dir, timeout=60)
        return r.returncode, r.stdout.decode(), r.stderr.decode()

    def test_a_missing_hook_is_silence_not_a_block(self):
        rc, out, err = self.run_guard(os.path.join(self.dir, "gone", "speak_hook.py"))
        self.assertEqual((rc, out, err), (0, "", ""))

    def test_a_bare_command_would_have_blocked(self):
        # The reason for all of this, kept so nobody "simplifies" the guard away.
        r = subprocess.run([sys.executable, os.path.join(self.dir, "gone", "speak_hook.py")],
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 2)

    def test_the_hook_runs_as_python_would_run_it(self):
        hook = os.path.join(self.dir, "speak_hook.py").replace("\\", "/")
        with open(hook, "w") as fh:
            fh.write(STUB)
        rc, out, err = self.run_guard(hook, "PAYLOAD")
        self.assertEqual(rc, 7, err)
        self.assertEqual(out.splitlines()[:4],
                         [repr(hook), repr(os.path.dirname(hook)), "'__main__'", "'PAYLOAD'"])

    def test_the_project_folder_cannot_shadow_the_stdlib(self):
        # -c puts the working folder on sys.path, and a hook's working folder is
        # whatever project Claude Code has open. "python speak_hook.py" never did.
        project = os.path.join(self.dir, "project")
        os.makedirs(project)
        with open(os.path.join(project, "json.py"), "w") as fh:
            fh.write("raise SystemExit('shadowed')\n")
        hook = os.path.join(self.dir, "speak_hook.py")
        with open(hook, "w") as fh:
            fh.write("import json\nprint('ok')\n")
        rc, out, err = self.run_guard(hook, cwd=project)
        self.assertEqual((rc, out.strip()), (0, "ok"), err)

    @unittest.skipUnless(BASH, "Git Bash is what Claude Code runs hooks through on Windows")
    def test_through_bash_with_an_awkward_folder_name(self):
        folder = os.path.join(self.dir, "O'Brien (x86) $HOME")
        os.makedirs(folder)
        hook = os.path.join(folder, "speak_hook.py").replace("\\", "/")
        with open(hook, "w") as fh:
            fh.write(STUB)
        # The whole line, exactly as install_hooks writes it.
        line = voice_lib.hook_command(python=sys.executable, hook=hook)
        r = subprocess.run([BASH, "-c", line], input=b"PAYLOAD", capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 7, r.stderr.decode())
        self.assertEqual(r.stdout.decode().splitlines()[0], repr(hook))
        gone = line.replace("/speak_hook.py", "/gone.py")
        r = subprocess.run([BASH, "-c", gone], capture_output=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))


class HealthTest(unittest.TestCase):
    def health(self, command):
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        hooks = {e: [{"hooks": [{"type": "command", "command": command}]}]
                 for e in voice_lib.HOOK_EVENTS}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"hooks": hooks}, fh)
        return voice_lib.hook_health(paths=[path], log_path=os.devnull)

    def test_a_bare_command_is_called_unguarded(self):
        found = self.health("C:/py/python.exe C:/cv/speak_hook.py")
        self.assertTrue(found["unguarded"])
        self.assertFalse(found["backslashed"])

    def test_the_installed_line_is_not(self):
        found = self.health('C:/py/python.exe -c "' + installer_guard() + '" C:/cv/speak_hook.py')
        self.assertFalse(found["unguarded"])
        self.assertEqual(found["missing"], [])


class InstallTest(unittest.TestCase):
    """install_hooks / remove_hooks against a scratch settings file."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "settings.json")
        self.theirs = {"type": "command", "command": "other-tool --log"}
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"model": "opus", "hooks": {
                "Stop": [{"hooks": [self.theirs]},
                         {"hooks": [{"type": "command",
                                     "command": "python C:/old/speak_hook.py"}]}],
                "PostToolUse": [{"matcher": "*", "hooks": [self.theirs]}]}}, fh)

    def read(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_install_is_idempotent_and_leaves_others_alone(self):
        voice_lib.install_hooks(self.path, "CMD speak_hook.py", narrate_lines=False)
        once = self.read()
        voice_lib.install_hooks(self.path, "CMD speak_hook.py", narrate_lines=False)
        self.assertEqual(self.read(), once)
        self.assertEqual(once["model"], "opus")
        self.assertEqual(sorted(once["hooks"]), sorted(set(voice_lib.HOOK_EVENTS) | {"PostToolUse"}))
        self.assertIn({"hooks": [self.theirs]}, once["hooks"]["Stop"])
        self.assertEqual(len(once["hooks"]["Stop"]), 2, "the old line of ours replaced, not kept")

    def test_the_speaking_hooks_never_hold_claude_code_up(self):
        voice_lib.install_hooks(self.path, "CMD speak_hook.py", narrate_lines=False)
        hooks = self.read()["hooks"]
        for event in ("Stop", "Notification", "PreToolUse"):
            self.assertTrue(hooks[event][-1]["hooks"][0]["async"], event)
        for event in ("SessionStart", "UserPromptSubmit"):
            self.assertNotIn("async", hooks[event][-1]["hooks"][0], event)
        self.assertEqual(hooks["PreToolUse"][-1]["matcher"], "AskUserQuestion")
        self.assertNotIn("matcher", hooks["Notification"][-1])

    def test_reading_lines_off_the_transcript_widens_pretooluse(self):
        voice_lib.install_hooks(self.path, "CMD speak_hook.py", narrate_lines=True)
        self.assertEqual(self.read()["hooks"]["PreToolUse"][-1]["matcher"], "*")

    def test_remove_takes_only_ours(self):
        voice_lib.install_hooks(self.path, "CMD speak_hook.py", narrate_lines=False)
        self.assertTrue(voice_lib.remove_hooks(self.path))
        self.assertEqual(self.read(), {"model": "opus", "hooks": {
            "Stop": [{"hooks": [self.theirs]}],
            "PostToolUse": [{"matcher": "*", "hooks": [self.theirs]}]}})
        self.assertFalse(voice_lib.remove_hooks(self.path), "nothing left to take")

    def test_a_missing_file_is_created_and_removal_of_nothing_writes_nothing(self):
        path = os.path.join(self.dir, "new", "settings.json")
        self.assertFalse(voice_lib.remove_hooks(path))
        self.assertFalse(os.path.exists(path))
        voice_lib.install_hooks(path, "CMD speak_hook.py", narrate_lines=False)
        self.assertTrue(voice_lib.remove_hooks(path))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {})


class OldNoteTest(unittest.TestCase):
    """remove_notes: the CLAUDE.md block older versions wrote, taken back out."""

    def run_on(self, text):
        path = os.path.join(tempfile.mkdtemp(), "CLAUDE.md")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        cut = voice_lib.remove_notes(path)
        with open(path, encoding="utf-8") as fh:
            return cut, fh.read()

    def test_only_the_block_goes(self):
        cut, text = self.run_on("# Mine\n\nkeep this\n\n<!-- claude-voice -->\nrules\n"
                                "<!-- current-voice -->\nNeya\n<!-- /current-voice -->\n"
                                "<!-- /claude-voice -->\n\n## Also mine\n")
        self.assertTrue(cut)
        self.assertEqual(text, "# Mine\n\nkeep this\n\n## Also mine\n")

    def test_no_markers_no_edit(self):
        self.assertEqual(self.run_on("just mine\n"), (False, "just mine\n"))

    def test_its_line_ends_are_kept(self):
        path = os.path.join(tempfile.mkdtemp(), "CLAUDE.md")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        with open(path, "wb") as fh:
            fh.write(b"# Mine\n\n<!-- claude-voice -->\nrules\n<!-- /claude-voice -->\n\nmore\n")
        self.assertTrue(voice_lib.remove_notes(path))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), b"# Mine\n\nmore\n")

    def test_a_file_that_is_not_utf8_is_left_alone(self):
        path = os.path.join(tempfile.mkdtemp(), "CLAUDE.md")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        raw = "caf\xe9 <!-- claude-voice -->x<!-- /claude-voice -->\n".encode("cp1252")
        with open(path, "wb") as fh:
            fh.write(raw)
        self.assertFalse(voice_lib.remove_notes(path))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), raw)


class LoginValueTest(unittest.TestCase):
    """open_at_login(False) takes away only a Run value naming this copy."""

    def test_only_this_copys_command_counts(self):
        mine = '"C:/py/pythonw.exe" "' + os.path.join(voice_lib.ROOT, "panel.py") + '"'
        self.assertTrue(voice_lib._names_root(mine))
        self.assertTrue(voice_lib._names_root(mine.replace("\\", "/").upper()))
        other = r'"C:\x\runtime\pythonw.exe" "C:\Users\u\AppData\Local\Programs\claude-voice\app\panel.py"'
        self.assertFalse(voice_lib._names_root(other))
        self.assertFalse(voice_lib._names_root('"' + voice_lib.ROOT + '-old\\panel.py"'))


if __name__ == "__main__":
    unittest.main(verbosity=2)
