"""The panel's two lists and its typing box, driven for real.

Written after breaking them: a row was given one more value than the tree had
columns, and a fifth value with four columns is silently dropped by Tk right up
until something asks for the column by name, at which point `fill` raises and
both lists go blank. `py_compile` cannot see any of that and neither can
reading it. So this builds a real Treeview and puts real rows in it.

It needs a desktop to open a window on, and it opens none: the root is withdrawn
before anything is drawn into it.

    python test_panel.py
"""

import queue
import time
import tkinter as tk
import unittest
from tkinter import ttk
from unittest.mock import patch

import panel


class Rows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        # Only the parts of the panel these two methods touch. Building the
        # whole window would need an engine to draw the state of.
        self.panel = panel.Panel.__new__(panel.Panel)
        self.panel.drawn = {}
        # A voice the panel has no picture for, which is the path that draws a
        # coloured initial instead. Leaving the dict empty is not that case:
        # the lookup's own default is a pixel size, not an image.
        self.panel.icons = {"abby": None}
        self.panel.tree = panel.Panel._rows(self.root, 3)

    def job(self, jid, text, mood=None):
        return {"id": jid, "voice": "abby", "when": "12:28", "project": "living-abby",
                "session": "direct", "text": text, "instruction": mood}

    def values(self):
        return [self.panel.tree.item(i, "values")
                for i in self.panel.tree.get_children()]

    def test_a_row_lands_with_every_column_it_was_given(self):
        self.panel.fill(self.panel.tree, "history",
                        [self.job(1, "Yes, please.", "sound delighted and smug")])
        when, project, session, mood, text = self.values()[0]
        self.assertEqual(when, "12:28")
        self.assertEqual(project, "living-abby")
        self.assertEqual(session, "direct")
        self.assertEqual(mood, "sound delighted and smug")
        self.assertEqual(text, "Yes, please.")

    def test_lines_without_a_mood_still_draw(self):
        # The whole failure, in one check: every line in the list carries no
        # mood, which is the ordinary case and was the broken one.
        self.panel.fill(self.panel.tree, "history",
                        [self.job(1, "One"), self.job(2, "Two")])
        self.assertEqual([v[4] for v in self.values()], ["One", "Two"])
        self.assertEqual([v[3] for v in self.values()], ["", ""])

    def test_the_column_takes_no_room_until_there_is_a_mood(self):
        self.panel.fill(self.panel.tree, "history", [self.job(1, "One")])
        self.assertEqual(self.panel.tree.column("mood", "width"), 0)
        self.panel.fill(self.panel.tree, "history",
                        [self.job(1, "One"), self.job(2, "Two", "sound tired")])
        self.assertEqual(self.panel.tree.column("mood", "width"),
                         panel.Panel.MOOD_WIDTH)
        # And gives it back, so a switch to Pocket does not leave a dead column
        # standing in a window this narrow.
        self.panel.fill(self.panel.tree, "history", [self.job(3, "Three")])
        self.assertEqual(self.panel.tree.column("mood", "width"), 0)

    def test_an_empty_list_is_drawn_rather_than_raising(self):
        self.panel.fill(self.panel.tree, "queue", [])
        self.assertEqual(self.values(), [])

    def test_a_long_mood_is_cut_rather_than_pushing_the_line_out(self):
        self.panel.fill(self.panel.tree, "history",
                        [self.job(1, "Hello", "sound " + "very " * 40 + "tired")])
        self.assertLessEqual(len(self.values()[0][3]), 61)


class TheTypingBox(unittest.TestCase):
    """Trying a mood by hand, which is what the box is for."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def typer(self, takes_mood=True, takes_events=()):
        one = panel.Panel.__new__(panel.Panel)
        one.root = self.root
        one.typer = one.typed = one.typed_mood = None
        one.takes_mood = takes_mood
        one.takes_events = takes_events
        one.dim = []
        one.on_top = tk.BooleanVar(value=True)
        one.dark = tk.BooleanVar(value=True)
        one.sent = {}
        one.act = lambda path, body: one.sent.update(path=path, **body)
        panel.Panel.open_typer(one)
        self.addCleanup(panel.Panel.close_typer, one)
        return one

    def test_a_mood_typed_beside_the_words_rides_with_them(self):
        one = self.typer()
        one.typed.insert("1.0", "hello there")
        one.typed_mood.insert(0, "  sound tired but proud ")
        panel.Panel.speak_typed(one)
        self.assertEqual(one.sent["text"], "hello there")
        self.assertEqual(one.sent["instruction"], "sound tired but proud")

    def test_a_box_that_only_names_a_mood_sends_the_mood(self):
        # Typed by hand on 2026-09-24, and sent as three words she did not
        # whisper. As the mood it gets the sentence that was heard working.
        one = self.typer()
        one.typed.insert("1.0", "I want to tell you a secret")
        one.typed_mood.insert(0, "wisper this line")
        panel.Panel.speak_typed(one)
        self.assertEqual(one.sent["mood"], "whisper")
        self.assertNotIn("instruction", one.sent)

    def test_no_mood_typed_is_no_mood_sent(self):
        one = self.typer()
        one.typed.insert("1.0", "hello there")
        panel.Panel.speak_typed(one)
        self.assertEqual(one.sent["instruction"], "")

    def test_an_engine_that_performs_no_mood_is_offered_no_box(self):
        one = self.typer(takes_mood=False)
        self.assertIsNone(one.typed_mood)
        one.typed.insert("1.0", "hello there")
        panel.Panel.speak_typed(one)
        self.assertEqual(one.sent["instruction"], "")

    def test_closing_forgets_both_boxes(self):
        one = self.typer()
        panel.Panel.close_typer(one)
        self.assertIsNone(one.typed)
        self.assertIsNone(one.typed_mood)
        # The grey label inside it must not be left in the list the theme
        # repaints: a dead widget there breaks the next switch.
        self.assertTrue(all(w.winfo_exists() for w in one.dim))

    def test_a_mood_named_goes_as_a_mood_for_the_engine_to_expand(self):
        # "sad" alone is the weak form as an instruction, and the engine knows
        # the whole sentence it stands for -- so a name is sent as a name.
        one = self.typer()
        one.typed.insert("1.0", "I looked everywhere.")
        one.typed_mood.insert(0, "Sad")
        panel.Panel.speak_typed(one)
        self.assertEqual(one.sent["mood"], "sad")
        self.assertNotIn("instruction", one.sent)

    def test_an_engine_that_makes_sounds_says_which_under_the_box(self):
        one = self.typer(takes_events=("laugh", "sigh"))
        shown = [w.cget("text") for w in one.dim if w.winfo_exists()]
        self.assertTrue(any("(laugh) (sigh)" in text for text in shown), shown)
        closed = self.typer()
        self.assertFalse(any("(laugh)" in w.cget("text") for w in closed.dim))


class TheBreezeWindow(unittest.TestCase):
    """What stands between picking Breeze and eleven gigabytes arriving.

    Opened for real and never shown: `over`, which would place it on screen,
    withdraws it instead, and the two things that reach outside -- the machine
    check and the status file -- are fed by hand.
    """

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def bare(self):
        one = panel.Panel.__new__(panel.Panel)
        one.root = self.root
        one.setup_win = None
        one.setup_inbox = queue.Queue()
        one.dim = []
        one.on_top = tk.BooleanVar(value=False)
        one.dark = tk.BooleanVar(value=False)
        one.drawn = {}
        one.engine = "pocket"
        one.sent = {}
        one.act = lambda path, body: one.sent.update(path=path, **body)
        one.hold = lambda *a, **k: None
        return one

    def window(self):
        one = self.bare()
        with patch.object(panel, "over", lambda win, parent: win.withdraw()), \
                patch.object(panel.Panel, "check_for_breeze", lambda self: None), \
                patch.object(panel.Panel, "watch_breeze_install", lambda self: None):
            panel.Panel.open_breeze_setup(one)
        self.addCleanup(panel.Panel.close_breeze_setup, one)
        return one

    def answer(self, one, verdict):
        report = {"verdict": verdict, "target": one.setup_target, "gpu": None,
                  "rows": [{"need": "an NVIDIA card", "have": "a card", "ok": verdict != "no"}]}
        with patch("breeze_setup.running", return_value=None):
            one.setup_inbox.put(("checked", report))
            panel.Panel.drain_setup(one)

    def test_picking_it_uninstalled_opens_this_rather_than_switching(self):
        one = self.bare()
        one.engine_box = ttk.Combobox(self.root, values=list(panel.ENGINE_LABELS.values()))
        one.engine_box.set(panel.ENGINE_LABELS["breeze"])
        one.drawn["breezeReady"] = False
        opened = []
        with patch.object(panel.Panel, "open_breeze_setup", lambda self: opened.append(1)):
            panel.Panel.pick_engine(one)
        self.assertEqual(opened, [1])
        self.assertEqual(one.sent, {}, "nothing is switched, and nothing downloaded")
        self.assertEqual(one.engine_box.get(), panel.ENGINE_LABELS["pocket"])

    def test_picking_it_installed_just_switches(self):
        one = self.bare()
        one.engine_box = ttk.Combobox(self.root, values=list(panel.ENGINE_LABELS.values()))
        one.engine_box.set(panel.ENGINE_LABELS["breeze"])
        one.drawn["breezeReady"] = True
        panel.Panel.pick_engine(one)
        self.assertEqual(one.sent, {"path": "/set-engine", "engine": "breeze"})

    def test_nothing_can_be_downloaded_before_the_machine_has_answered(self):
        self.assertTrue(self.window().setup_go.instate(["disabled"]))

    def test_a_machine_that_can_run_it_lights_the_button(self):
        one = self.window()
        self.answer(one, "full")
        self.assertFalse(one.setup_go.instate(["disabled"]))

    def test_a_machine_that_cannot_never_does(self):
        one = self.window()
        self.answer(one, "no")
        self.assertTrue(one.setup_go.instate(["disabled"]))
        self.assertIn("cannot run it", one.setup_status.cget("text"))

    def test_an_answer_about_a_folder_no_longer_chosen_is_ignored(self):
        # The check runs on a thread, and the folder can be changed while it
        # does; the answer that comes back late is about the old one.
        one = self.window()
        one.setup_inbox.put(("checked", {"verdict": "no", "target": "E:\\old-choice",
                                         "gpu": None, "rows": []}))
        panel.Panel.drain_setup(one)
        self.assertEqual(one.setup_status.cget("text"), "")
        self.assertIsNone(one.setup_verdict)

    def test_finishing_switches_to_breeze_once(self):
        one = self.window()
        one.setup_started = time.time()
        one.setup_inbox.put(("status", {"state": "done", "fast": True}, False))
        panel.Panel.drain_setup(one)
        self.assertEqual(one.sent, {"path": "/set-engine", "engine": "breeze"})
        self.assertIsNone(one.setup_started)
        self.assertTrue(one.drawn["breezeReady"])

    def test_a_failure_offers_to_carry_on(self):
        one = self.window()
        one.setup_started = time.time()
        one.setup_inbox.put(("status", {"state": "failed", "error": "no network"}, False))
        panel.Panel.drain_setup(one)
        self.assertIn("no network", one.setup_status.cget("text"))
        self.assertFalse(one.setup_go.instate(["disabled"]))
        self.assertEqual(one.setup_go.cget("text"), "try again")


if __name__ == "__main__":
    unittest.main(verbosity=2)
