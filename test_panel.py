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

import tkinter as tk
import unittest

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

    def typer(self, takes_mood=True):
        one = panel.Panel.__new__(panel.Panel)
        one.root = self.root
        one.typer = one.typed = one.typed_mood = None
        one.takes_mood = takes_mood
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
        one.typed_mood.insert(0, "  sound tired ")
        panel.Panel.speak_typed(one)
        self.assertEqual(one.sent["text"], "hello there")
        self.assertEqual(one.sent["instruction"], "sound tired")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
