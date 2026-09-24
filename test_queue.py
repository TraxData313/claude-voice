"""Who is heard, and in what order, when one voice speaks for everybody.

A line that takes the floor used to empty the queue on its way in. On
2026-09-24 a reply from Abby's room arrived four seconds after a session's
TL;DR had started being made, and that summary was never heard. These are
that afternoon, and the cases either side of it.

The engine thread and the player are never started: the Speaker is built
without them, and the tests read its queue the way the panel does.

    python test_queue.py
"""

import queue
import threading
import unittest
from unittest.mock import patch

import speak_server
import voice_lib

SESSION = "digital-ai-assistant"      # a Claude session's lines, from the watcher
ROOM = "Abby"                         # what Abby's room sends as its project


def line(project, text="Something worth hearing.", mood=None):
    return speak_server.Job([text], "abby", {}, text=text, project=project,
                            instruction=voice_lib.MOODS.get(mood), mood=mood)


class TakingTheFloor(unittest.TestCase):
    def setUp(self):
        sp = self.sp = speak_server.Speaker.__new__(speak_server.Speaker)
        sp.state = {}
        sp.jobs = queue.Queue()
        sp.play_q = queue.Queue(maxsize=2)
        sp.lock = threading.Lock()
        sp.current = sp.playing = None
        sp.resume = threading.Event()
        sp.resume.set()
        self.labels = "project"
        sp._live = lambda: {"sessionLabel": self.labels}
        for target, value in ((speak_server.winsound, "PlaySound"), (speak_server, "log")):
            patcher = patch.object(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Whoever was heard last is one record for the whole process, so each
        # test gets a clean one, as if the engine had just started.
        patcher = patch.object(speak_server, "_last_speaker", speak_server._LastSpeaker())
        self.last = patcher.start()
        self.addCleanup(patcher.stop)

    def in_the_air(self, job):
        self.sp.playing = self.sp.current = job
        return job

    def waiting(self, *jobs):
        for job in jobs:
            self.sp.jobs.put(job)
        return jobs

    def order(self):
        return list(self.sp.jobs.queue)

    def test_a_reply_from_the_room_no_longer_takes_a_summary_with_it(self):
        summary = self.in_the_air(line(SESSION, "It's live! The change is on main.", "happy"))
        reply = line(ROOM, "I'm clearing both stale warnings now.")
        self.sp.submit(reply, barge=True)

        self.assertTrue(summary.cancelled)            # still cut off, so the reply is heard now
        first, again = self.order()
        self.assertIs(first, reply)
        self.assertIsNot(again, summary)
        self.assertEqual((again.text, again.mood, again.instruction),
                         (summary.text, "happy", voice_lib.MOODS["happy"]))
        self.assertFalse(again.cancelled)

    def test_the_line_said_again_says_whose_it_is(self):
        # Right after a line of the room's, in the same voice, it would
        # otherwise be heard as the room carrying on.
        self.in_the_air(line(SESSION, "It's live!"))
        self.sp.submit(line(ROOM), barge=True)
        self.assertTrue(self.order()[1].chunks[0].startswith("digital ai assistant. It's live!"))

    def test_but_not_after_a_line_of_no_project_or_with_names_off(self):
        summary = self.in_the_air(line(SESSION, "It's live!"))
        self.sp.submit(line(None, "Testing, testing."), barge=True)
        self.assertEqual(self.order()[1].chunks, summary.chunks)

        self.labels = "off"
        summary = self.in_the_air(line(SESSION, "Still live."))
        self.sp.submit(line(ROOM), barge=True)
        self.assertEqual(self.order()[1].chunks, summary.chunks)

    def test_its_own_older_lines_are_still_replaced(self):
        aside = self.in_the_air(line(ROOM, "Let me look."))
        older, theirs = self.waiting(line(ROOM, "Still looking."), line(SESSION))
        reply = line(ROOM, "Found it.")
        self.sp.submit(reply, barge=True)
        self.assertTrue(aside.cancelled)
        self.assertEqual(self.order(), [reply, theirs])
        self.assertNotIn(older, self.order())

    def test_everyone_else_waiting_keeps_their_place(self):
        self.in_the_air(line(SESSION, "First."))
        second, other = self.waiting(line(SESSION, "Second."), line("qwen-voices", "Elsewhere."))
        reply = line(ROOM)
        self.sp.submit(reply, barge=True)
        order = self.order()
        self.assertEqual([j.text for j in order], [reply.text, "First.", "Second.", "Elsewhere."])
        self.assertIs(order[2], second)
        self.assertIs(order[3], other)

    def test_the_next_name_is_judged_against_the_new_last_line(self):
        self.last.prefix(SESSION, {})
        self.in_the_air(line(SESSION))
        self.assertEqual(self.last.prefix(ROOM, {}), "Abby. ")   # as /speak asks it
        self.sp.submit(line(ROOM), barge=True)
        # The session's line is last in the queue now, so its next one needs
        # no name, and the room's next one does.
        self.assertEqual(self.last.prefix(SESSION, {}), "")
        self.assertEqual(self.last.prefix(ROOM, {}), "Abby. ")

    def test_a_held_voice_still_queues_at_the_end(self):
        held = self.in_the_air(line(SESSION))
        self.sp.resume.clear()
        reply = line(ROOM)
        self.sp.submit(reply, barge=True)
        self.assertFalse(held.cancelled)
        self.assertEqual(self.order(), [reply])

    def test_a_repeat_replaces_only_the_line_it_repeats(self):
        said = self.in_the_air(line(SESSION, "Say that again?"))
        (after,) = self.waiting(line(SESSION, "And then this."))
        again = self.sp.repeat_current()
        self.assertTrue(said.cancelled)
        self.assertEqual(self.order(), [again, after])
        self.assertEqual(again.text, said.text)

    def test_stop_still_empties_everything(self):
        playing = self.in_the_air(line(SESSION))
        self.waiting(line(ROOM), line(SESSION))
        self.sp.cancel()
        self.assertTrue(playing.cancelled)
        self.assertEqual(self.order(), [])


if __name__ == "__main__":
    unittest.main()
