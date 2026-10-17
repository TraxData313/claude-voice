"""The registration a game reads first: HKCU\\Software\\claude-voice.

Every test writes under a throwaway key of its own (Software\\claude-voice-test-<pid>)
and deletes it afterwards, so the real key -- the one Toni's game reads -- is
never read, written or removed here. Nothing starts an engine.

    python test_registry.py
"""

import io
import os
import sys
import unittest
import winreg
from contextlib import redirect_stdout
from unittest.mock import patch

import voice_lib
import voice_cli

TEST_KEY = rf"Software\claude-voice-test-{os.getpid()}"
STATE = {"port": 8766, "portHome": 8765}


def _drop(key):
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except FileNotFoundError:
        pass


class Registration(unittest.TestCase):
    def setUp(self):
        _drop(TEST_KEY)
        # Everything that defaults to the real key is pointed at the test one.
        self._key = patch.object(voice_lib, "REG_KEY", TEST_KEY)
        self._key.start()

    def tearDown(self):
        self._key.stop()
        _drop(TEST_KEY)
        self.assertIsNone(voice_lib.registration(TEST_KEY), "the test key was left behind")

    def test_register_writes_the_contract(self):
        values = voice_lib.register(kind="dev", python=r"C:\Py\pythonw.exe", state=STATE)
        found = voice_lib.registration()
        self.assertEqual(found["Contract"], voice_lib.REG_CONTRACT)
        self.assertTrue(voice_lib._same_path(found["InstallDir"], voice_lib.ROOT))
        self.assertEqual(found["Kind"], "dev")
        self.assertEqual(found["Port"], 8766)
        self.assertEqual(found["PortHome"], 8765)
        self.assertEqual(found["Version"], voice_lib.own_version())
        self.assertEqual(found["Launcher"], values["Launcher"])
        # The executable first and quoted, so a reader can split it off without parsing the rest.
        script = os.path.join(voice_lib.ROOT, "voice_cli.py")
        self.assertEqual(found["Launcher"], f'"C:\\Py\\pythonw.exe" "{script}" start')

    def test_ports_are_dwords_and_home_defaults_to_the_port(self):
        voice_lib.register(kind="installed", state={"port": 8765})
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as k:
            port, kind = winreg.QueryValueEx(k, "Port")
            home, _ = winreg.QueryValueEx(k, "PortHome")
        self.assertEqual((port, kind, home), (8765, winreg.REG_DWORD, 8765))

    def test_kind_defaults_by_git(self):
        voice_lib.register(state=STATE)
        want = "dev" if os.path.isdir(os.path.join(voice_lib.ROOT, ".git")) else "installed"
        self.assertEqual(voice_lib.registration()["Kind"], want)
        with self.assertRaises(ValueError):
            voice_lib.register(kind="portable", state=STATE)

    def test_launcher_defaults_to_the_windowless_python(self):
        voice_lib.register(kind="dev", state=STATE)
        self.assertIn(voice_lib._python(), voice_lib.registration()["Launcher"])

    def test_refresh_only_touches_our_own_key(self):
        # No key: the engine never creates one.
        self.assertFalse(voice_lib.refresh_registration({"port": 9001}))
        self.assertIsNone(voice_lib.registration())

        # Ours: port and version brought up to date, the rest untouched.
        voice_lib.register(kind="dev", python=r"C:\Py\pythonw.exe", state=STATE)
        launcher = voice_lib.registration()["Launcher"]
        self.assertTrue(voice_lib.refresh_registration({"port": 9001, "portHome": 8765}))
        found = voice_lib.registration()
        self.assertEqual((found["Port"], found["PortHome"]), (9001, 8765))
        self.assertEqual((found["Kind"], found["Launcher"]), ("dev", launcher))

        # Another copy's: left exactly as it was.
        with patch.object(voice_lib, "ROOT", r"C:\elsewhere\claude-voice"):
            self.assertFalse(voice_lib.refresh_registration({"port": 9100}))
        self.assertEqual(voice_lib.registration()["Port"], 9001)

    def test_unregister_leaves_another_copys_key(self):
        self.assertEqual(voice_lib.unregister(), "absent")
        voice_lib.register(kind="installed", state=STATE)
        with patch.object(voice_lib, "ROOT", r"C:\elsewhere\claude-voice"):
            self.assertEqual(voice_lib.unregister(), "other")
            self.assertIsNotNone(voice_lib.registration())
            self.assertEqual(voice_lib.unregister(force=True), "removed")
        self.assertIsNone(voice_lib.registration())

    def test_unregister_removes_ours(self):
        voice_lib.register(kind="dev", state=STATE)
        self.assertTrue(voice_lib.registered_here())
        self.assertEqual(voice_lib.unregister(), "removed")
        self.assertFalse(voice_lib.registered_here())

    def test_cli_register_and_unregister(self):
        out = io.StringIO()
        with redirect_stdout(out):
            voice_cli.cmd_register(STATE, ["--kind", "dev", "--python", r"C:\Py\pythonw.exe"])
        self.assertIn("as dev", out.getvalue())
        self.assertEqual(voice_lib.registration()["Kind"], "dev")
        self.assertIn("dev, port 8766", voice_cli._registration_line())

        with self.assertRaises(SystemExit):
            voice_cli.cmd_register(STATE, ["--bogus"])

        out = io.StringIO()
        with redirect_stdout(out):
            voice_cli.cmd_unregister(STATE, [])
        self.assertIn("Removed", out.getvalue())
        self.assertIsNone(voice_lib.registration())
        self.assertIn("none", voice_cli._registration_line())

    def test_cli_names_the_copy_it_replaces(self):
        with patch.object(voice_lib, "ROOT", r"C:\elsewhere\claude-voice"):
            voice_lib.register(kind="installed", state=STATE)
        self.assertIn("another copy", voice_cli._registration_line())
        out = io.StringIO()
        with redirect_stdout(out):
            voice_cli.cmd_register(STATE, ["--kind", "dev"])
        self.assertIn(r"C:\elsewhere\claude-voice", out.getvalue())
        self.assertTrue(voice_lib.registered_here())

    def test_the_real_key_name_is_the_documented_one(self):
        self._key.stop()
        try:
            self.assertEqual(voice_lib.REG_KEY, r"Software\claude-voice")
        finally:
            self._key.start()


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("the registration is Windows-only")
    unittest.main(verbosity=2)
