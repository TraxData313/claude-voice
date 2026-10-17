"""The installer and uninstaller (installer.py), without installing anything real.

Everything happens in temp folders, under a throwaway product name
(claude-voice-test-<pid>) so the registry keys, the Run value and the notes
folder are never the real ones. Nothing is downloaded, nothing is pip-installed,
no engine is started. The real end-to-end run is described in docs/install.md.

    python test_installer.py
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import installer
import voice_lib

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = f"claude-voice-test-{os.getpid()}"


class Quiet(installer.Out):
    """An Out that writes nothing anywhere."""

    def __init__(self, lay, dry_run=True):
        super().__init__(lay, dry_run=True, status=False)
        self.lines = []

    def say(self, text=""):
        self.lines.append(text)


def _tree(root, files):
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cv-installer-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lay = installer.Layout(os.path.join(self.tmp, "Programs", PRODUCT), PRODUCT,
                                    os.path.join(self.tmp, "data"), os.path.join(self.tmp, "claude"),
                                    os.path.join(self.tmp, "links"))
        self.addCleanup(self._registry_clean)

    def _registry_clean(self):
        installer.reg_delete_key(self.lay.reg_key)
        installer.reg_delete_key(self.lay.uninstall_key)
        installer.reg_delete_value(installer.RUN_KEY, self.lay.run_value)
        self.assertIsNone(installer.reg_read(self.lay.reg_key), "a test registry key was left behind")
        self.assertIsNone(installer.reg_read(installer.RUN_KEY, self.lay.run_value))
        shutil.rmtree(self.lay.notes, ignore_errors=True)


class Pins(unittest.TestCase):
    def test_install_ps1_pins_the_same_python_as_runtime_json(self):
        pin = installer.read_json(os.path.join(HERE, "runtime.json"))
        with open(os.path.join(HERE, "install.ps1"), encoding="utf-8") as fh:
            script = fh.read()
        for name, key in (("PythonVersion", "version"), ("PythonUrl", "url"), ("PythonSha256", "sha256")):
            found = re.search(rf'\${name}\s*=\s*"([^"]+)"', script)
            self.assertTrue(found, f"install.ps1 has no ${name}")
            self.assertEqual(found.group(1), pin[key], f"install.ps1 ${name} differs from runtime.json")
        self.assertTrue(pin["url"].startswith("https://www.python.org/ftp/python/"))
        self.assertRegex(pin["sha256"], r"^[0-9a-f]{64}$")

    def test_nothing_spawns_bypass_or_hidden_powershell(self):
        # Python: no PowerShell started at all, by any of these.
        for name in ("installer.py", "voice_lib.py", "panel.py", "update_check.py"):
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                code = fh.read()
            for literal in ('"powershell', "'powershell", '"-ExecutionPolicy', "'-ExecutionPolicy",
                            "-ComObject", "EncodedCommand"):
                self.assertFalse(literal in code, f"{name} has {literal}")
        # The scripts: the flag is named only in the usage text a person reads.
        for name in ("install.ps1", "setup.ps1", "uninstall.ps1"):
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                text = fh.read()
            code = re.sub(r"<#.*?#>", "", text, flags=re.S)
            code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
            for word in ("ExecutionPolicy", "EncodedCommand", "WindowStyle", "Hidden", "Invoke-Expression"):
                self.assertFalse(word in code, f"{name} has {word}")


class Files(Base):
    def test_copy_app_leaves_out_the_checkout_and_the_machine(self):
        src = os.path.join(self.tmp, "src")
        _tree(src, {"voice_lib.py": "", "installer.py": "", "config.json": "{}", "installed.json": "{}",
                    "test_x.py": "", ".git/HEAD": "", "logs/a.log": "", "voices/female/abby/voice.json": "{}",
                    "docs/icons/abby.ico": "", "setup-app/bin/x.exe": "", "__pycache__/x.pyc": "",
                    "config.json.123.new": ""})
        dest = os.path.join(self.tmp, "dest")
        installer.copy_app(src, dest)
        got = sorted(os.path.relpath(os.path.join(h, f), dest).replace("\\", "/")
                     for h, _, fs in os.walk(dest) for f in fs)
        self.assertEqual(got, ["docs/icons/abby.ico", "installer.py", "voice_lib.py",
                               "voices/female/abby/voice.json"])

    def test_swap_in_replaces_whole_and_keeps_the_old_on_failure(self):
        live, new = os.path.join(self.tmp, "app"), os.path.join(self.tmp, "app.new")
        _tree(live, {"v.txt": "old"})
        _tree(new, {"v.txt": "new"})
        installer.swap_in(new, live)
        with open(os.path.join(live, "v.txt")) as fh:
            self.assertEqual(fh.read(), "new")
        self.assertFalse(os.path.exists(live + ".old"))
        self.assertFalse(os.path.exists(new))
        # A staged folder that is not there: the rename fails, the live one is put back.
        with self.assertRaises(OSError):
            installer.swap_in(os.path.join(self.tmp, "missing"), live)
        with open(os.path.join(live, "v.txt")) as fh:
            self.assertEqual(fh.read(), "new")

    def test_park_runtime_leaves_no_program_folder(self):
        _tree(self.lay.root, {"runtime/python.exe": "x", "runtime/Lib/os.py": "", "app/voice_lib.py": "",
                              "install.json": "{}"})
        temp = os.path.join(self.tmp, "temp")
        os.makedirs(temp)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        with patch.dict(os.environ, {"TEMP": temp}):
            parked, gone = installer.park_runtime(self.lay, Quiet(self.lay))
        self.assertTrue(gone)
        self.assertFalse(os.path.exists(self.lay.root))
        self.assertFalse(parked and os.path.exists(parked), "nothing was running, so nothing should wait")

    def test_park_runtime_leaves_files_it_did_not_put_there(self):
        _tree(self.lay.root, {"runtime/python.exe": "x", "app/voice_lib.py": "", "app.old/x": "",
                              "install.json": "{}", "notes.txt": "mine", "tools/t.exe": "t"})
        temp = os.path.join(self.tmp, "temp")
        os.makedirs(temp)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        with patch.dict(os.environ, {"TEMP": temp}):
            parked, gone = installer.park_runtime(self.lay, Quiet(self.lay))
        self.assertTrue(gone)
        self.assertEqual(sorted(os.listdir(self.lay.root)), ["notes.txt", "tools"])

    def test_never_installs_into_somebody_elses_folder(self):
        _tree(self.lay.root, {"runtime/python.exe": "x", "notes.txt": "mine"})
        with self.assertRaises(installer.Refused):
            installer.install(self.lay, HERE, Quiet(self.lay), dry_run=True)

    def test_old_layout_moves_aside_keeping_settings_and_claims(self):
        _tree(self.lay.root, {"voice_lib.py": "", "panel.py": "", "config.json": '{"voice": "neya"}',
                              "installed.json": json.dumps({"studio": "D:/s", "python": "C:/py",
                                                            "breeze": "D:/b", "claude": True}),
                              "runtime/python.exe": "", "logs/x.log": ""})
        self.assertTrue(installer.old_layout(self.lay.root))
        claims = installer.migrate_old_layout(self.lay, Quiet(self.lay), dry_run=False)
        self.assertEqual(claims, {"studio": "D:/s", "breeze": "D:/b"})
        self.assertEqual(installer.read_json(os.path.join(self.lay.data, "config.json")), {"voice": "neya"})
        self.assertEqual(sorted(os.listdir(self.lay.root)), ["runtime"])

    def test_a_checkout_is_not_an_old_install(self):
        _tree(self.lay.root, {"voice_lib.py": "", ".git/HEAD": ""})
        self.assertFalse(installer.old_layout(self.lay.root))
        self.assertTrue(installer.is_dev_checkout(self.lay.root))

    def test_an_old_install_keeps_the_voices_it_made(self):
        _tree(self.lay.root, {"voice_lib.py": "", "voices/female/x/shipped/voice.json": "{}",
                              "voices/female/x/mine/voice.json": "{}", "voices/female/x/mine/ref.wav": "w"})
        source = os.path.join(self.tmp, "source")
        _tree(source, {"voice_lib.py": "", "voices/female/x/shipped/voice.json": "{}"})
        installer.migrate_old_layout(self.lay, Quiet(self.lay), dry_run=False, source=source)
        kept = os.path.join(self.lay.data, "voices", "female", "x")
        self.assertEqual(sorted(os.listdir(kept)), ["mine"])
        self.assertEqual(sorted(os.listdir(os.path.join(kept, "mine"))), ["ref.wav", "voice.json"])

    def test_the_code_alone_is_not_an_old_install(self):
        _tree(self.lay.root, {"voice_lib.py": "", "panel.py": ""})          # an unzipped copy
        self.assertFalse(installer.old_layout(self.lay.root, self.lay.product))
        _tree(self.lay.root, {"installed.json": '{"kind": "dev"}'})
        self.assertFalse(installer.old_layout(self.lay.root, self.lay.product))
        _tree(self.lay.root, {"installed.json": '{"python": "C:/py"}'})
        self.assertTrue(installer.old_layout(self.lay.root, self.lay.product))

    def test_a_worktree_is_a_checkout_too(self):
        _tree(self.lay.root, {"voice_lib.py": "", ".git": "gitdir: C:/elsewhere/.git/worktrees/x"})
        self.assertTrue(installer.is_dev_checkout(self.lay.root))
        self.assertFalse(installer.old_layout(self.lay.root))

    def test_somebody_elses_install_json_is_not_ours_to_uninstall(self):
        _tree(self.lay.root, {"install.json": '{"name": "not us"}', "precious.txt": "x"})
        with self.assertRaises(installer.Refused):
            installer.Layout.of_installed(self.lay.root)
        self.assertEqual(installer.main(["uninstall", "--root", self.lay.root, "--quiet"]), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.lay.root, "precious.txt")))


class Settings(Base):
    def test_a_first_config_has_no_placeholders_and_names_the_engine(self):
        with patch.object(installer, "previous_copy", return_value=None):
            cfg = installer.write_config(self.lay, Quiet(self.lay), "pocket", port=8799, dry_run=False)
        self.assertEqual(cfg["engine"], "pocket")
        self.assertEqual(cfg["port"], 8799)
        self.assertFalse([k for k in cfg if k.startswith("_")])
        self.assertNotIn("studioDir", cfg)
        self.assertEqual(installer.read_json(os.path.join(self.lay.data, "config.json")), cfg)

    def test_a_later_install_keeps_your_settings(self):
        installer.write_json(os.path.join(self.lay.data, "config.json"), {"voice": "neya", "engine": "qwen",
                                                                          "volume": 0.4})
        cfg = installer.write_config(self.lay, Quiet(self.lay), None, dry_run=False)
        self.assertEqual(cfg, {"voice": "neya", "engine": "qwen", "volume": 0.4})
        cfg = installer.write_config(self.lay, Quiet(self.lay), "pocket", dry_run=False)
        self.assertEqual(cfg["engine"], "pocket")
        self.assertEqual(cfg["voice"], "neya")

    def test_a_first_install_brings_the_previous_copys_settings(self):
        before = os.path.join(self.tmp, "clone")
        installer.write_json(os.path.join(before, "config.json"), {"voice": "neya", "mutedSessions": ["x"]})
        with patch.object(installer, "previous_copy", return_value=before):
            cfg = installer.write_config(self.lay, Quiet(self.lay), "pocket", dry_run=False)
        self.assertEqual(cfg["voice"], "neya")
        self.assertNotIn("mutedSessions", cfg)


class Registration(Base):
    def test_the_apps_entry_runs_the_signed_python_not_powershell(self):
        values = installer.uninstall_values(self.lay, "2.0.0", 1234)
        self.assertTrue(values["UninstallString"].startswith(f'"{self.lay.pythonw}" '))
        self.assertIn("installer.py\" uninstall", values["UninstallString"])
        self.assertEqual(values["QuietUninstallString"], values["UninstallString"] + " --quiet")
        self.assertNotIn("powershell", json.dumps(values).lower())
        for key in ("DisplayName", "DisplayVersion", "Publisher", "DisplayIcon", "InstallLocation",
                    "EstimatedSize", "NoModify", "NoRepair"):
            self.assertIn(key, values)
        self.assertEqual(values["InstallLocation"], self.lay.root)

    def test_names_are_the_products_own(self):
        self.assertEqual(self.lay.reg_key, "Software\\" + PRODUCT)
        self.assertTrue(self.lay.uninstall_key.endswith("\\Uninstall\\" + PRODUCT))
        self.assertEqual(self.lay.run_value, PRODUCT)
        self.assertTrue(self.lay.notes.endswith(PRODUCT))
        real = installer.Layout()
        self.assertEqual(real.reg_key, "Software\\claude-voice")
        self.assertEqual(real.shortcut_leaf, "Abby for Claude.lnk")

    def test_shortcut_round_trip(self):
        path = os.path.join(self.tmp, "links", "x.lnk")
        target = sys.executable
        installer.make_shortcut(path, target, '"C:\\some where\\panel.py"', self.tmp, "a test")
        got = installer.read_shortcut(path)
        self.assertTrue(installer.same_path(got[0], target))
        self.assertEqual(got[1], '"C:\\some where\\panel.py"')

    def test_uninstall_plan_lists_only_what_is_ours(self):
        _tree(self.lay.root, {"app/voice_cli.py": "", "runtime/python.exe": ""})
        link = installer.make_shortcut(os.path.join(self.lay.start_menu, self.lay.shortcut_leaf),
                                       self.lay.pythonw, "", self.lay.data)
        installer.reg_write(self.lay.uninstall_key, {"DisplayName": PRODUCT})
        installer.reg_write(self.lay.reg_key, {"InstallDir": self.lay.app, "Kind": "installed"})
        installer.reg_write(installer.RUN_KEY, {self.lay.run_value: installer.login_command(self.lay)})
        os.makedirs(os.path.dirname(self.lay.command_md))
        with open(self.lay.command_md, "w", encoding="utf-8") as fh:
            fh.write(f'"{self.lay.app.replace(chr(92), "/")}/voice_cli.py"')
        doc = {"claude": {"dir": self.lay.claude_dir}, "shortcuts": [link], "claims": {}}
        what = " | ".join(w for w, _ in installer.plan_uninstall(self.lay, doc))
        for expected in ("hooks", "/voice command", "shortcut", "starting with Windows",
                         "registry key games", "Settings > Apps", "the program itself"):
            self.assertIn(expected, what)

    def test_uninstall_plan_leaves_another_copys_things(self):
        _tree(self.lay.root, {"app/voice_cli.py": "", "runtime/python.exe": ""})
        other = os.path.join(self.tmp, "clone")
        installer.reg_write(self.lay.reg_key, {"InstallDir": other, "Kind": "dev"})
        installer.reg_write(installer.RUN_KEY, {self.lay.run_value: f'"pythonw" "{other}\\panel.py"'})
        os.makedirs(os.path.dirname(self.lay.command_md))
        with open(self.lay.command_md, "w", encoding="utf-8") as fh:
            fh.write(f'"{other.replace(chr(92), "/")}/voice_cli.py"')
        doc = {"claude": {"dir": self.lay.claude_dir}, "shortcuts": [], "claims": {}}
        what = " | ".join(w for w, _ in installer.plan_uninstall(self.lay, doc))
        self.assertNotIn("registry key games", what)
        self.assertNotIn("starting with Windows", what)
        self.assertNotIn("/voice command", what)


class DryRun(Base):
    def test_a_dry_run_install_writes_nothing(self):
        _tree(self.lay.root, {"runtime/python.exe": ""})
        before = sorted(os.listdir(self.tmp))
        out = Quiet(self.lay)
        with patch.object(installer, "previous_copy", return_value=None):
            installer.install(self.lay, HERE, out, engine="none", dry_run=True,
                              options={"desktop": True, "startup": True})
        self.assertEqual(sorted(os.listdir(self.tmp)), before)
        self.assertEqual(os.listdir(self.lay.root), ["runtime"])
        self.assertIsNone(installer.reg_read(self.lay.uninstall_key))
        self.assertIsNone(installer.reg_read(self.lay.reg_key))
        self.assertTrue(any("nothing was written" in line for line in out.lines))

    def test_a_games_no_claude_install_keeps_the_claude_wiring_on_record(self):
        claude = {"dir": os.path.join(self.tmp, ".claude")}
        _tree(self.lay.root, {"runtime/python.exe": "", "app/voice_lib.py": "",
                              "install.json": json.dumps({"layout": installer.LAYOUT, "product": self.lay.product,
                                                          "claude": claude})})
        with patch.object(installer, "previous_copy", return_value=None):
            manifest = installer.install(self.lay, HERE, Quiet(self.lay), engine="none", claude=False,
                                         dry_run=True)
        self.assertEqual(manifest["claude"], claude)

    def test_a_dry_run_uninstall_removes_nothing(self):
        _tree(self.lay.root, {"runtime/python.exe": "", "app/voice_cli.py": ""})
        installer.write_json(self.lay.manifest, {"layout": 1, "product": PRODUCT, "claude": None,
                                                 "shortcuts": [], "claims": {}})
        out = Quiet(self.lay)
        plan = installer.uninstall(self.lay, out, dry_run=True)
        self.assertTrue(plan)
        self.assertTrue(os.path.isfile(self.lay.manifest))
        self.assertTrue(any("Would remove" in line for line in out.lines))

    def test_a_quiet_uninstall_keeps_your_things_and_whatever_it_does_not_name(self):
        _tree(self.lay.root, {"runtime/python.exe": "", "app/voice_cli.py": ""})
        installer.write_json(self.lay.manifest, {"layout": 1, "product": PRODUCT, "data": self.lay.data,
                                                 "claude": None, "shortcuts": [], "claims": {}})
        _tree(self.lay.data, {"config.json": '{"port": 8798}', "voices/female/mine/voice.json": "{}",
                              "logs/speak-server.log": "x", "breeze/weights.bin": "x"})
        installer.reg_write(self.lay.uninstall_key, {"DisplayName": PRODUCT})
        temp = os.path.join(self.tmp, "temp")
        os.makedirs(temp)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        with patch.dict(os.environ, {"TEMP": temp}):
            installer.uninstall(self.lay, Quiet(self.lay), quiet=True, remove_data=False)
        self.assertFalse(os.path.exists(self.lay.root))
        self.assertIsNone(installer.reg_read(self.lay.uninstall_key))
        left = sorted(os.listdir(self.lay.data))
        self.assertEqual(left, ["breeze", "config.json", "voices"])   # the logs went; the rest is not its to take


class Updating(unittest.TestCase):
    def test_an_installed_copy_takes_nothing_older_than_itself(self):
        import update_check
        tmp = tempfile.mkdtemp(prefix="cv-update-")
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "src", "claude-voice-main")
        _tree(src, {"version.json": '{"version": "0.0.1"}', "installer.py": "", "voice_lib.py": ""})
        said = []
        with patch.object(voice_lib, "INSTALL", {"layout": 1}), \
                patch.object(installer, "fetch_source", return_value=src), \
                patch.object(update_check.subprocess, "Popen", side_effect=AssertionError("must not run")):
            self.assertFalse(update_check.apply_update({"port": 1}, say=said.append))
        self.assertTrue(any("Nothing to do" in line for line in said))


class Never(Base):
    def test_never_installs_into_a_checkout(self):
        _tree(self.lay.root, {".git/HEAD": "", "runtime/python.exe": ""})
        with self.assertRaises(installer.Refused):
            installer.install(self.lay, HERE, Quiet(self.lay), engine="none", dry_run=True)


class Hooks(unittest.TestCase):
    def test_remove_mine_leaves_another_copys_hooks(self):
        tmp = tempfile.mkdtemp(prefix="cv-hooks-")
        self.addCleanup(shutil.rmtree, tmp, True)
        settings = os.path.join(tmp, "settings.json")
        theirs = "C:/clone/speak_hook.py"
        mine = "C:/Users/Some One/AppData/Local/Programs/claude-voice/app/speak_hook.py"
        voice_lib.install_hooks(settings, voice_lib.hook_command("python", theirs), narrate_lines=False)
        doc = installer.read_json(settings)
        doc["hooks"]["Stop"].append({"hooks": [{"type": "command",
                                                "command": voice_lib.hook_command("python", mine)}]})
        installer.write_json(settings, doc)
        self.assertTrue(voice_lib.remove_hooks(settings, mine.replace("/", "\\")))
        left = json.dumps(installer.read_json(settings))
        self.assertIn("C:/clone/speak_hook.py", left)
        self.assertNotIn("Some One", left)
        self.assertTrue(voice_lib.remove_hooks(settings))
        self.assertNotIn("speak_hook", json.dumps(installer.read_json(settings)))

    def test_a_shared_entry_keeps_the_other_hook(self):
        tmp = tempfile.mkdtemp(prefix="cv-hooks-")
        self.addCleanup(shutil.rmtree, tmp, True)
        settings = os.path.join(tmp, "settings.json")
        installer.write_json(settings, {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": voice_lib.hook_command("python", "C:/a/speak_hook.py")},
            {"type": "command", "command": "notify-send done"}]}]}})
        voice_lib.install_hooks(settings, voice_lib.hook_command("python", "C:/a/speak_hook.py"))
        voice_lib.install_hooks(settings, voice_lib.hook_command("python", "C:/a/speak_hook.py"))
        self.assertTrue(voice_lib.remove_hooks(settings))
        self.assertEqual(installer.read_json(settings),
                         {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}]}})


class InstalledLayout(unittest.TestCase):
    def test_voice_lib_reads_the_install_record(self):
        tmp = tempfile.mkdtemp(prefix="cv-layout-")
        self.addCleanup(shutil.rmtree, tmp, True)
        app = os.path.join(tmp, "app")
        os.makedirs(app)
        shutil.copy2(os.path.join(HERE, "voice_lib.py"), app)
        data = os.path.join(tmp, "data")
        installer.write_json(os.path.join(tmp, "install.json"), {"layout": 1, "product": PRODUCT, "data": data})
        probe = ("import json, voice_lib as v; print(json.dumps([v.CONFIG_PATH, v.LOG_DIR, v.REG_KEY, "
                 "v.WHERE_PATH, v.own_voices_dir({}), v._workdir(), bool(v.INSTALL)]))")
        got = json.loads(subprocess.run([sys.executable, "-c", probe], cwd=app, capture_output=True,
                                        text=True, check=True).stdout)
        self.assertEqual(got[0], os.path.join(data, "config.json"))
        self.assertEqual(got[1], os.path.join(data, "logs"))
        self.assertEqual(got[2], "Software\\" + PRODUCT)
        self.assertTrue(got[3].endswith(os.path.join(PRODUCT, "where.json")))
        self.assertEqual(got[4], os.path.join(data, "voices"))
        self.assertTrue(got[6])

    def test_a_checkout_keeps_everything_beside_the_code(self):
        self.assertEqual(voice_lib.INSTALL, {})
        self.assertEqual(voice_lib.CONFIG_PATH, os.path.join(voice_lib.ROOT, "config.json"))
        self.assertEqual(voice_lib.REG_KEY, "Software\\claude-voice")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("the installer is Windows-only")
    unittest.main(verbosity=2)
