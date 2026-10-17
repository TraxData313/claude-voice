"""What a Claude session is told about its voice, and what it may write back.

Two halves, and both were missing. A session had no way to know whether the
voice could laugh or whisper -- CLAUDE.md is read once, and the engine can
change under a conversation -- and nothing it wrote could carry a mood, since
its text is all it has. So the hook tells it (speak_hook.tell) and a bracketed
stage direction carries the mood (voice_lib.direction).

Nothing here loads a model or starts the engine. The hook is run in-process with
its stdin, stdout, logs and config swapped for scratch ones; the engine's
SessionBook is handed a speaker that only records what it was given.

    python test_session.py
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import speak_hook
import speak_server
import voice_lib

# Importing the hook points this at the hook's real log; nothing here should
# write there.
voice_lib.notify = voice_lib._unlogged

STATE = {"enabled": True, "engine": "breeze", "voice": "abby", "source": "embedding",
         "port": 8765, "narrate": True, "alerts": True,
         "maxChars": 4000, "fullMaxChars": 4000, "narrateMaxChars": 240}
ABBY = ({"id": "abby", "name": "Abby"}, {})


def spoken(markdown):
    """What the engine would make of this message: the words, and
    the mood they asked for."""
    speech, _ = voice_lib.speech_for(markdown, STATE)
    return voice_lib.direction(speech)


class AMoodWrittenIntoTheLine(unittest.TestCase):
    def test_a_mood_in_front_is_taken_out_and_kept(self):
        self.assertEqual(voice_lib.direction("(whisper) I found it."),
                         ("whisper", "I found it."))

    def test_on_a_line_of_its_own_it_leaves_no_stray_full_stop(self):
        # clean_text gives a line with no ending a full stop, and a direction
        # alone on the first line of a TL;DR is exactly such a line.
        self.assertEqual(spoken("## TL;DR\n(excited)\n- It works!\n- Every test passes."),
                         ("excited", "It works! Every test passes."))

    def test_beside_the_heading_counts_as_the_first_thing_in_it(self):
        self.assertEqual(spoken("## TL;DR (sad)\n- The build is red again.")[0], "sad")

    def test_the_first_one_wins_and_none_is_read_out(self):
        self.assertEqual(voice_lib.direction("(calm) First. (excited) Second."),
                         ("calm", "First. Second."))
        self.assertEqual(voice_lib.direction("Done. (sad). Next thing."),
                         ("sad", "Done. Next thing."))

    def test_the_way_a_stage_direction_gets_written_lands_on_a_mood(self):
        for written, mood in (("(whispering)", "whisper"), ("(Softly)", "tender"),
                              ("[sadly]", "sad"), ("( excitedly )", "excited"),
                              ("(playfully)", "playful"), ("(gently)", "calm")):
            self.assertEqual(voice_lib.direction(f"{written} Hello.")[0], mood, written)

    def test_a_slip_in_the_spelling_still_lands_on_the_mood(self):
        # Typed into the panel by hand, and read out as no mood at all.
        self.assertEqual(voice_lib.direction("(wisper) I want to tell you a secret"),
                         ("whisper", "I want to tell you a secret"))
        self.assertEqual(voice_lib.direction("[exited] It works!"), ("excited", "It works!"))

    def test_an_aside_in_brackets_is_not_a_direction(self):
        for line in ("(sad, I know) the build is red.", "Option one (recommended).",
                     "It returns (None) when empty.", "A flat (tire), again.",
                     "Keep a (wary) eye on it.", "Delete the file(s) first.",
                     "- [x] done"):
            self.assertEqual(voice_lib.direction(line), (None, line))

    def test_a_link_is_not_a_direction(self):
        # clean_text keeps only a link's words, and those are words.
        self.assertEqual(spoken("See [calm](https://example.com) for more."),
                         (None, "See calm for more."))

    def test_a_line_that_is_only_a_direction_leaves_nothing_to_say(self):
        self.assertEqual(voice_lib.direction("(whisper)"), ("whisper", ""))

    def test_sounds_stay_where_they_were_written(self):
        self.assertEqual(spoken("## TL;DR\n(excited)\n- It works! (laugh) Really."),
                         ("excited", "It works! (laugh) Really."))

    def test_emphasis_is_never_a_mood(self):
        # "*serious*" is how markdown stresses a word far more often than it is
        # a stage direction, so asterisks carry sounds and never moods.
        self.assertEqual(spoken("A *serious* bug, now fixed."),
                         (None, "A serious bug, now fixed."))


class ASoundBetweenAsterisks(unittest.TestCase):
    """Emphasis is stripped on the way to speech, so "*laughs*" used to arrive as
    the word laughs -- the one outcome worse than silence."""

    def test_the_way_chat_writes_them_becomes_a_sound(self):
        for written, want in (("*laughs* That's so true.", "(laugh) That's so true."),
                              ("It broke again *sigh*.", "It broke again (sigh)."),
                              ("*sighs heavily* But fine.", "(sigh) But fine."),
                              ("*ahem* Listen.", "(clears throat) Listen."),
                              ("- *giggles* Okay!", "(laugh) Okay!")):
            self.assertEqual(spoken(written)[1], want, written)

    def test_a_word_stressed_in_a_sentence_stays_a_word(self):
        for written, want in (("*Laugh* tracks are gone.", "Laugh tracks are gone."),
                              ("**laughs** loud", "laughs loud."),
                              ("the *laughter* was loud", "the laughter was loud.")):
            self.assertEqual(spoken(written)[1], want, written)

    def test_breeze_makes_it_and_the_others_drop_it(self):
        line = spoken("*laughs* That's so true.")[1]
        self.assertEqual(voice_lib.perform(line, "breeze"), "(laugh) That's so true.")
        self.assertEqual(voice_lib.perform(line, "qwen"), "That's so true.")


@patch.object(voice_lib, "resolve", lambda *a, **k: ABBY)
class WhatASessionIsTold(unittest.TestCase):
    def note(self, **changes):
        return voice_lib.session_note(dict(STATE, **changes))

    def test_breeze_offers_moods_and_sounds(self):
        key, text = self.note(engine="breeze")
        self.assertTrue(text.startswith(voice_lib.SESSION_NOTE_HEAD))
        self.assertIn("Abby through Breeze 2", text)
        self.assertIn("(whisper)", text)
        for sound in voice_lib.engine_can("breeze", "events"):
            self.assertIn(f"({sound})", text)

    def test_qwen_offers_a_mood_and_says_there_are_no_sounds(self):
        _, text = self.note(engine="qwen")
        self.assertIn("(whisper)", text)
        self.assertIn("leave them out", text)
        self.assertNotIn("(clears throat)", text)

    def test_pocket_is_offered_nothing_it_cannot_do(self):
        _, text = self.note(engine="pocket")
        self.assertIn("no moods and no sounds", text)
        for mood in voice_lib.MOODS:
            if mood != "sad":                 # named once, as what not to write
                self.assertNotIn(f"({mood})", text)

    def test_off_says_off(self):
        key, text = self.note(enabled=False)
        self.assertIn("the voice is off", text)
        self.assertNotIn("(laugh)", text)
        self.assertNotEqual(key, self.note()[0])

    def test_the_key_moves_with_anything_the_text_would(self):
        keys = {self.note()[0], self.note(engine="qwen")[0], self.note(enabled=False)[0]}
        self.assertEqual(len(keys), 3)
        with patch.object(voice_lib, "resolve", lambda *a, **k: ({"name": "Max"}, {})):
            self.assertNotIn(self.note()[0], keys)

    def test_it_invites_playing_rather_than_rationing(self):
        # "dont shy playing with the mood" -- told that most lines want none,
        # a session wrote almost none.
        for engine in ("breeze", "qwen"):
            _, text = self.note(engine=engine)
            self.assertIn("Don't be shy", text)
            self.assertNotIn("Most lines want none", text)

    def test_a_new_wording_is_news_even_when_nothing_else_changed(self):
        # An update that rewords the note reaches sessions already open.
        key = self.note()[0]
        with patch.object(voice_lib, "SESSION_NOTE_HEAD", "claude-voice, as of today:"):
            self.assertNotEqual(self.note()[0], key)

    def test_an_engine_is_described_by_what_it_can_do_not_by_its_name(self):
        table = dict(voice_lib.ENGINE_CAN, pocket={"instruction": False,
                                                   "events": ("laugh",)})
        with patch.object(voice_lib, "ENGINE_CAN", table):
            _, text = self.note(engine="pocket")
        self.assertIn("makes sounds but takes no mood", text)
        self.assertIn("(laugh)", text)
        self.assertNotIn("(whisper)", text)


class Hook(unittest.TestCase):
    """speak_hook.main, in-process, with everything it touches swapped out."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.state = dict(STATE)
        self.posts, self.started, self.spooled = [], [], []
        self.down = False

        def post(port, path, body=None, timeout=5):
            if self.down:
                raise ConnectionRefusedError("down")
            self.posts.append((path, body))
            return {}

        for target, name, value in (
                (speak_hook, "TOLD_PATH", os.path.join(self.dir, "told.json")),
                (speak_hook, "HOOK_LOG", os.path.join(self.dir, "hook.log")),
                (voice_lib, "LOG_DIR", self.dir),
                (voice_lib, "load_state", lambda: dict(self.state)),
                (voice_lib, "resolve", lambda *a, **k: ABBY),
                (voice_lib, "post", post),
                (voice_lib, "start_server", lambda state, wait=0, from_hook=False: not self.started.append(1)),
                (voice_lib, "spool", lambda body: self.spooled.append(body) or True)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "claude-desktop"})
        env.start()
        self.addCleanup(env.stop)

    def run_hook(self, **payload):
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                contextlib.redirect_stdout(out):
            speak_hook.main([])
        printed = out.getvalue().strip()
        return json.loads(printed)["hookSpecificOutput"] if printed else None

    def transcript(self, *entries):
        path = os.path.join(self.dir, "t.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        return path

    # -- telling -----------------------------------------------------------
    def test_a_new_session_is_told_what_the_voice_can_do(self):
        told = self.run_hook(hook_event_name="SessionStart", session_id="s1", source="startup")
        self.assertEqual(told["hookEventName"], "SessionStart")
        self.assertIn("(laugh)", told["additionalContext"])

    def test_it_is_told_the_writing_rules_too_so_claude_md_need_not_be(self):
        told = self.run_hook(hook_event_name="SessionStart", session_id="s1")
        self.assertIn("## TL;DR", told["additionalContext"])
        self.assertIn("The voice is currently **Abby**", told["additionalContext"])

    def test_it_is_told_once_and_then_only_when_something_changed(self):
        self.run_hook(hook_event_name="SessionStart", session_id="s1")
        self.assertIsNone(self.run_hook(hook_event_name="UserPromptSubmit", session_id="s1"))
        self.state["engine"] = "qwen"
        told = self.run_hook(hook_event_name="UserPromptSubmit", session_id="s1")
        self.assertIn("makes no sounds", told["additionalContext"])
        self.assertIsNone(self.run_hook(hook_event_name="UserPromptSubmit", session_id="s1"))

    def test_a_new_voice_reaches_an_open_session_at_its_next_prompt(self):
        self.run_hook(hook_event_name="SessionStart", session_id="s1")
        with patch.object(voice_lib, "resolve", lambda *a, **k: (
                {"id": "neya", "name": "Neya", "persona": "soft-spoken"}, {})):
            told = self.run_hook(hook_event_name="UserPromptSubmit", session_id="s1")
        self.assertIn("**Neya** — soft-spoken.", told["additionalContext"])

    def test_a_session_older_than_the_hook_is_told_at_its_next_prompt(self):
        told = self.run_hook(hook_event_name="UserPromptSubmit", session_id="old")
        self.assertEqual(told["hookEventName"], "UserPromptSubmit")

    def test_a_compacted_session_is_told_again(self):
        self.run_hook(hook_event_name="SessionStart", session_id="s1")
        self.assertIsNotNone(self.run_hook(hook_event_name="SessionStart", session_id="s1",
                                           source="compact"))

    def test_switching_the_voice_off_is_news_too(self):
        self.run_hook(hook_event_name="SessionStart", session_id="s1")
        self.state["enabled"] = False
        told = self.run_hook(hook_event_name="UserPromptSubmit", session_id="s1")
        self.assertIn("the voice is off", told["additionalContext"])

    def test_a_run_nobody_is_watching_is_told_nothing(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}):
            self.assertIsNone(self.run_hook(hook_event_name="SessionStart", session_id="s1"))

    def test_the_record_keeps_only_the_newest_sessions(self):
        with patch.object(speak_hook, "TOLD_KEEP", 3):
            for n in range(6):
                self.run_hook(hook_event_name="SessionStart", session_id=f"s{n}")
        self.assertEqual(sorted(speak_hook._told()), ["s3", "s4", "s5"])

    # -- the engine -----------------------------------------------------------
    def test_a_prompt_names_the_session_to_the_engine(self):
        self.run_hook(hook_event_name="UserPromptSubmit", session_id="s1",
                      transcript_path="C:/t/one.jsonl", cwd=r"C:\code\proj",
                      prompt="Fix the panel flicker " + "x" * 500)
        path, body = self.posts[-1]
        self.assertEqual((path, body["event"]), ("/hook", "UserPromptSubmit"))
        self.assertEqual(body["transcript_path"], "C:/t/one.jsonl")
        self.assertLessEqual(len(body["prompt"]), speak_hook.PROMPT_KEEP)

    def test_a_prompt_brings_a_dead_engine_back_while_the_voice_is_on(self):
        self.down = True
        self.run_hook(hook_event_name="UserPromptSubmit", session_id="s1")
        self.assertEqual(self.started, [1])
        self.assertEqual(self.spooled, [], "a prompt is nothing to say later")
        self.state["enabled"] = False
        self.run_hook(hook_event_name="UserPromptSubmit", session_id="s1")
        self.assertEqual(self.started, [1], "switched off means left alone")

    # -- speaking ---------------------------------------------------------
    def test_the_answer_goes_to_the_engine_from_the_payload(self):
        self.run_hook(hook_event_name="Stop", session_id="s1", transcript_path="C:/nowhere.jsonl",
                      last_assistant_message="Body.\n\n## TL;DR\n- Done.")
        path, body = self.posts[-1]
        self.assertEqual(path, "/hook")
        self.assertEqual(body["event"], "Stop")
        self.assertEqual(body["text"], "Body.\n\n## TL;DR\n- Done.")
        self.assertEqual(body["entrypoint"], "claude-desktop")

    def test_an_older_claude_code_without_the_field_is_read_once_off_its_transcript(self):
        path = self.transcript(
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": "All done."}]}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "tool_use", "name": "Bash"}]}})
        self.run_hook(hook_event_name="Stop", transcript_path=path)
        self.assertEqual(self.posts[-1][1]["text"], "All done.")

    def test_an_answer_that_timed_out_on_a_busy_engine_is_not_kept_to_say_twice(self):
        with patch.object(voice_lib, "post", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow"))):
            self.run_hook(hook_event_name="Stop", last_assistant_message="Done.")
        self.assertEqual(self.spooled, [])

    def test_a_headless_run_sends_spools_and_starts_nothing(self):
        self.down = True
        with patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}):
            self.run_hook(hook_event_name="UserPromptSubmit", session_id="s1")
            self.run_hook(hook_event_name="Stop", last_assistant_message="Done.")
        self.assertEqual((self.posts, self.spooled, self.started), ([], [], []))

    def test_an_engine_is_started_once_per_gap_not_once_per_hook(self):
        with patch.object(voice_lib, "SPAWN_STAMP", os.path.join(self.dir, "spawn.stamp")):
            self.assertTrue(voice_lib._claim_spawn())
            self.assertFalse(voice_lib._claim_spawn())
            old = time.time() - voice_lib.SPAWN_GAP - 5
            os.utime(voice_lib.SPAWN_STAMP, (old, old))
            self.assertTrue(voice_lib._claim_spawn())

    def test_an_answer_that_finds_the_engine_down_is_kept_and_the_engine_started(self):
        self.down = True
        self.run_hook(hook_event_name="Stop", last_assistant_message="Done.")
        self.assertEqual([b["text"] for b in self.spooled], ["Done."])
        self.assertEqual(self.started, [1])

    def test_with_the_voice_off_nothing_is_sent(self):
        self.state["enabled"] = False
        self.run_hook(hook_event_name="Stop", last_assistant_message="Done.")
        self.run_hook(hook_event_name="Notification", message="Claude needs your permission")
        self.assertEqual(self.posts, [])

    def test_lines_between_tool_calls_are_not_read_by_default(self):
        path = self.transcript({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "Now I'm reading it."}]}})
        self.run_hook(hook_event_name="PreToolUse", tool_name="Read", transcript_path=path)
        self.assertEqual(self.posts, [])

    def test_with_narrate_transcript_on_the_line_is_read_off_the_end(self):
        self.state["narrateFromTranscript"] = True
        path = self.transcript({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "(whisper) Peeking."}]}})
        self.run_hook(hook_event_name="PreToolUse", tool_name="Read", transcript_path=path)
        self.assertEqual(self.posts[-1][1]["text"], "(whisper) Peeking.")

    def test_a_question_goes_with_its_input(self):
        ask = {"questions": [{"question": "Which one?"}]}
        self.run_hook(hook_event_name="PreToolUse", tool_name="AskUserQuestion", tool_input=ask)
        self.assertEqual(self.posts[-1][1]["tool_input"], ask)

    def test_an_alert_goes_with_its_message(self):
        self.run_hook(hook_event_name="Notification", notification_type="permission_prompt",
                      message="Claude needs your permission to use Bash")
        body = self.posts[-1][1]
        self.assertEqual(body["message"], "Claude needs your permission to use Bash")
        self.assertEqual(body["notification_type"], "permission_prompt")


class WhatSpeakMakesOfIt(unittest.TestCase):
    """speak_server._delivery: which of the three ways of asking wins."""

    def test_an_instruction_in_the_caller_s_own_words_wins(self):
        self.assertEqual(speak_server._delivery(
            {"instruction": "sound smug", "mood": "sad"}, "whisper"),
            ("sound smug", None, None))

    def test_a_mood_in_its_own_field_wins_over_one_in_the_text(self):
        self.assertEqual(speak_server._delivery({"mood": "sad"}, "whisper"),
                         (voice_lib.MOODS["sad"], "sad", None))

    def test_a_mood_written_into_the_text_is_used_when_nothing_else_came(self):
        self.assertEqual(speak_server._delivery({}, "whisper"),
                         (voice_lib.MOODS["whisper"], "whisper", None))

    def test_a_name_that_is_no_mood_is_reported_rather_than_believed(self):
        self.assertEqual(speak_server._delivery({"mood": "sarcastic"}),
                         ("", None, "sarcastic"))
        self.assertEqual(speak_server._delivery({"mood": "sarcastic"}, "calm"),
                         (voice_lib.MOODS["calm"], "calm", "sarcastic"))

    def test_nothing_asked_is_nothing_sent(self):
        self.assertEqual(speak_server._delivery({}), ("", None, None))

    def test_an_instruction_that_only_names_a_mood_is_that_mood(self):
        # What the panel's box held on 2026-09-24. Sent as three words it did
        # not whisper; the mood's whole sentence is the one heard working.
        self.assertEqual(speak_server._delivery({"instruction": "wisper this line"}, None),
                         (voice_lib.MOODS["whisper"], "whisper", None))

    def test_an_instruction_that_says_more_is_kept_as_written(self):
        for words in ("Whisper it slowly, like a secret", "don't whisper"):
            self.assertEqual(speak_server._delivery({"instruction": words}, "sad"),
                             (words, None, None), words)


class Sessions(unittest.TestCase):
    """SessionBook: what the engine does with what a hook handed it."""

    def setUp(self):
        self.jobs = []
        speaker = type("Speaker", (), {"submit": lambda _, job, barge=False:
                                       self.jobs.append((job, barge))})()
        with patch.object(voice_lib, "load_state", lambda: {}):
            self.book = speak_server.SessionBook(speaker)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        for target, name, value in (
                (voice_lib, "already_spoken", lambda text, remember=True: False),
                (voice_lib, "resolve", lambda *a, **k: ABBY),
                (voice_lib, "notification_due", lambda speech, within=10.0: True),
                (voice_lib, "PENDING_PATH", os.path.join(self.dir, "pending.jsonl")),
                (voice_lib, "LOG_DIR", self.dir)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def hook(self, state=None, **body):
        body.setdefault("transcript_path", "C:/t/one.jsonl")
        body.setdefault("cwd", r"C:\code\my-project")
        return self.book.hook(body, dict(STATE, **(state or {})))

    def said(self):
        return [j.text for j, _ in self.jobs]

    def say(self, speech):
        self.book.say(speech, "narration", dict(STATE), "C:/t/one.jsonl")
        return self.jobs[-1][0] if self.jobs else None

    def test_a_mood_goes_beside_the_words_as_the_whole_instruction(self):
        job = self.say("(whisper) I found it.")
        self.assertEqual(job.text, "I found it.")
        self.assertEqual(job.mood, "whisper")
        self.assertEqual(job.instruction, voice_lib.MOODS["whisper"])
        self.assertEqual(job.as_dict()["mood"], "whisper")
        self.assertFalse(self.jobs[-1][1], "queued, never barging")

    def test_a_line_without_one_is_exactly_what_it_was(self):
        job = self.say("Now I'm running the tests.")
        self.assertIsNone(job.mood)
        self.assertIsNone(job.instruction)
        self.assertEqual(job.chunks, ["Now I'm running the tests."])

    def test_a_line_that_was_only_a_direction_is_not_said(self):
        self.assertIsNone(self.say("(sad)"))

    def test_an_answer_with_a_tldr_is_heard_as_its_tldr(self):
        reply = self.hook(event="Stop", text="Long body.\n\n## TL;DR\n(excited)\n- It works!")
        self.assertEqual(reply["said"], "summary")
        job = self.jobs[-1][0]
        self.assertEqual((job.mood, job.text), ("excited", "It works!"))
        self.assertEqual(job.project, "my-project")

    def test_an_answer_without_one_is_heard_whole(self):
        self.assertEqual(self.hook(event="Stop", text="Short and done.")["said"], "whole answer")
        self.assertEqual(self.said(), ["Short and done."])

    def test_the_voice_off_says_nothing(self):
        self.hook(state={"enabled": False}, event="Stop", text="Done.")
        self.assertEqual(self.jobs, [])

    def test_a_muted_session_stays_muted_however_its_path_is_spelled(self):
        reply = self.hook(state={"mutedSessions": [r"C:\t\one.jsonl"]},
                          event="Stop", text="Done.")
        self.assertEqual(self.jobs, [])
        self.assertIn("muted", reply["why"])

    def test_a_headless_run_is_silent_unless_asked_for(self):
        self.hook(event="Stop", text="Report.", entrypoint="sdk-cli")
        self.assertEqual(self.jobs, [])
        self.hook(state={"watchHeadless": True}, event="Stop", text="Report.",
                  entrypoint="sdk-cli")
        self.assertEqual(self.said(), ["Report."])

    def test_a_permission_prompt_names_the_tool_from_its_own_message(self):
        self.hook(event="Notification", message="Claude needs your permission to use WebFetch")
        self.assertEqual(self.said(), ["I need your permission to use Web Fetch."])

    def test_alerts_off_is_quiet(self):
        self.hook(state={"alerts": False}, event="Notification",
                  message="Claude needs your permission")
        self.assertEqual(self.jobs, [])

    def test_a_question_is_said_from_its_input(self):
        self.hook(event="PreToolUse", tool_name="AskUserQuestion",
                  tool_input={"questions": [{"question": "Which file first?"}]})
        self.assertTrue(self.said() and "Which file first?" in self.said()[0], self.said())

    def test_a_line_between_tools_respects_narrate(self):
        self.hook(state={"narrate": False}, event="PreToolUse", tool_name="Read",
                  text="Now I'm reading it.")
        self.assertEqual(self.jobs, [])
        self.hook(event="PreToolUse", tool_name="Read", text="Now I'm reading it.")
        self.assertEqual(self.said(), ["Now I'm reading it."])

    def test_the_panel_lists_a_session_by_its_first_prompt_and_can_mute_it(self):
        self.hook(event="UserPromptSubmit", prompt="Fix the panel flicker please now today ok")
        self.hook(event="UserPromptSubmit", prompt="something else entirely")
        rows = self.book.sessions(state=dict(STATE))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "Fix the panel flicker please now")
        self.assertEqual(rows[0]["project"], "my-project")
        saved = []
        with patch.object(voice_lib, "load_state", lambda: {"mutedSessions": ["C:/t/other.jsonl"]}),                 patch.object(voice_lib, "save_state", saved.append),                 patch.object(voice_lib, "CONFIG_PATH", os.path.join(self.dir, "config.json")):
            self.book.muted = set()       # a hook adopted an older list meanwhile
            self.book.set_muted("C:/t/one.jsonl", True)
        self.assertTrue(self.book.sessions(state=dict(STATE))[0]["muted"])
        self.assertEqual(saved[-1]["mutedSessions"], ["C:/t/one.jsonl", "C:/t/other.jsonl"],
                         "edited from the config as it is, not from a stale copy")

    def test_what_was_spooled_while_down_is_said_once_it_is_up(self):
        voice_lib.spool({"event": "Stop", "transcript_path": "C:/t/two.jsonl",
                         "text": "Said late."})
        with open(voice_lib.PENDING_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "Stop", "text": "Too old.", "at": 1}) + "\n")
        with patch.object(voice_lib, "load_state", lambda: dict(STATE)):
            self.book.catch_up(dict(STATE))
        self.assertEqual(self.said(), ["Said late."])
        self.assertFalse(os.path.exists(voice_lib.PENDING_PATH))


class OldDefaultEngine(unittest.TestCase):
    """A config from before 2.0 that never named an engine meant Qwen."""

    def test_only_a_config_without_an_engine_and_with_studio_there(self):
        studio = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, studio, True)
        self.assertIsNone(voice_lib.old_default_engine({"studioDir": studio}), "no Studio in it")
        os.makedirs(os.path.join(studio, "runtime", "bin", "server"))
        open(os.path.join(studio, "runtime", "bin", "server", "jvm.dll"), "w").close()
        self.assertEqual(voice_lib.old_default_engine({"studioDir": studio}), "qwen")
        self.assertIsNone(voice_lib.old_default_engine({"studioDir": studio, "engine": "pocket"}))
        self.assertIsNone(voice_lib.old_default_engine(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
