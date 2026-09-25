"""The hook line install.ps1 writes, and what 'status' says about it.

A bare "python speak_hook.py" ran perfectly until the day the folder moved.
Then python exited with 2 -- "can't open file" -- and 2 is the one exit code
Claude Code reads as "block": every prompt refused, every tool call refused, a
whole session locked out by a speech hook (2026-09-25). So the line is a guard
now, a sliver of Python that runs the hook as python would and exits 0 when
the file is gone. It is written by install.ps1, and read here out of it, so the
test and the installer cannot drift apart.

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
    """The Python the installer puts after -c, joined the way PowerShell joins it."""
    with open(os.path.join(HERE, "install.ps1"), encoding="utf-8-sig") as fh:
        src = fh.read()
    block = re.search(r"\$guard = (.+?)\n\$pyFwd", src, re.S).group(1)
    parts = re.findall(r"'((?:[^']|'')*)'", block)
    return "".join(parts).replace("''", "'")


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
        # Bash-Word's quoting, as install.ps1 does it.
        word = '"' + hook.replace("$", "\\$").replace("`", "\\`") + '"'
        line = '"' + sys.executable.replace("\\", "/") + '" -c "' + self.guard + '" ' + word
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
