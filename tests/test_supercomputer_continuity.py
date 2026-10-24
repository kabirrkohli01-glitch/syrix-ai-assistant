"""Continuity: the conversation, the history category, and picking a task back up.

Three failures, one theme — the mode forgot things it had no business forgetting.

1. Leaving and re-entering wiped the chat, because the workspace stamped a
   watermark at mount and the component unmounts on exit.
2. Supercomputer chats were filed as ordinary chats, so they appeared under
   "Chats" in history and reopening one landed in normal SYRIX.
3. "Chat about this" and Decline both promised "tell me and I will pick up from
   here", and nothing implemented it — the next message started a new task.
"""
from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import resume, session, spine, tasks  # noqa: E402
from syrix_supercomputer.contract import TaskContract  # noqa: E402


class _FakeStore:
    """Stands in for syrix_host_modules.chat_sessions."""

    def __init__(self):
        self.sessions = {}
        self.active = ""
        self.order = []
        # Channel -> session binding (2026-07-27). A mode with its own
        # conversation routes by channel instead of taking the single active
        # pointer, so normal chat keeps its session while a background task
        # runs. Mirrored here because a fake that lags the real interface hides
        # exactly the bug this exists to catch.
        self.channels = {}

    def new_session(self, title="", force_new=False, set_active=True):
        sid = f"s{len(self.order) + 1}"
        self.sessions[sid] = {"id": sid, "title": title, "meta": {}, "messages": []}
        self.order.append(sid)
        if set_active:
            self.active = sid
        return sid

    def bind_channel(self, channel, session_id):
        if not channel or not session_id:
            return False
        self.channels[str(channel).lower()] = str(session_id)
        return True

    def unbind_channel(self, channel):
        return self.channels.pop(str(channel or "").lower(), None) is not None

    def bound_session(self, channel):
        return self.channels.get(str(channel or "").lower(), "")

    def get_active_id(self):
        return self.active

    def set_session_meta(self, sid, key, value):
        if sid not in self.sessions:
            return False
        self.sessions[sid]["meta"][key] = value
        return True

    def get_session_meta(self, sid, key, default=None):
        return self.sessions.get(sid, {}).get("meta", {}).get(key, default)

    def peek_session(self, sid):
        return dict(self.sessions[sid]) if sid in self.sessions else None

    def load_session(self, sid):
        """Unlike peek, this makes the session active — which is the whole
        point on a resume: new lines have to join the conversation the user
        reopened, not whichever one happened to be current."""
        if sid not in self.sessions:
            return None
        self.active = sid
        return dict(self.sessions[sid])

    def list_sessions(self):
        return [{"id": s["id"], "title": s["title"], "active": s["id"] == self.active,
                 "kind": str(s["meta"].get("kind") or ""), "message_count": len(s["messages"]),
                 "preview": ""} for s in self.sessions.values()]


class _ThreadCase(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from syrix_supercomputer import chat_thread

        self.store = _FakeStore()
        self.chat_thread = chat_thread
        self._real_store = chat_thread._store
        chat_thread._store = lambda: self.store

    def tearDown(self):
        self.chat_thread._store = self._real_store
        super().tearDown()


class TestTheChatIsFiledSeparately(_ThreadCase):
    def test_entering_opens_a_session_tagged_supercomputer(self):
        sid = self.chat_thread.start()
        self.assertTrue(sid)
        self.assertEqual(self.store.get_session_meta(sid, "kind"), self.chat_thread.KIND)
        self.assertTrue(self.chat_thread.is_supercomputer_session(sid))

    def test_an_ordinary_text_session_is_not_mistaken_for_one(self):
        """The reopen path opens the whole agentic mode; it must be sure first."""
        sid = self.store.new_session(title="Normal chat")
        self.assertFalse(self.chat_thread.is_supercomputer_session(sid))
        self.assertFalse(self.chat_thread.is_supercomputer_session(""))
        self.assertFalse(self.chat_thread.is_supercomputer_session("does-not-exist"))

    def test_the_browser_tabs_are_remembered_against_the_session(self):
        sid = self.chat_thread.start()
        self.chat_thread.remember_browser(sid, [
            {"url": "https://a.example/one"},
            {"url": "https://b.example/two"},
            {"url": "about:blank"},          # not a real page
            {"url": "https://a.example/one"},  # duplicate
        ])
        self.assertEqual(self.chat_thread.remembered_tabs(sid),
                         ["https://a.example/one", "https://b.example/two"])

    def test_reopening_puts_the_conversation_back_into_the_agent(self):
        """Otherwise the transcript reads as continuous and the agent is amnesiac,
        which is worse than starting clean."""
        sid = self.chat_thread.start()
        self.store.sessions[sid]["messages"] = [
            {"role": "user", "text": "compare the two GPUs"},
            {"role": "assistant", "text": "the 5090 is faster"},
        ]
        spine.forget()
        restored = self.chat_thread.rehydrate(sid)
        self.assertEqual(restored, 2)
        history = spine.history()
        self.assertEqual([h["role"] for h in history], ["user", "assistant"])
        self.assertIn("5090", history[-1]["content"])


class TestReopeningFromHistory(_ThreadCase):
    """Opening a saved chat must land the user back where he left it.

    It did not: it opened a blank Supercomputer session instead, for two
    independent reasons.

    1. `chat_load_session` publishes the loaded transcript to the
       `syrix_loaded_session` key for the NORMAL chat panel and never rewrites
       the live chat buffer. The workspace renders only `chat_lines`, so there
       was literally nothing for it to show.
    2. The shell sent `supercomputer_enter` and the workspace sent another on
       mount, in no guaranteed order — and if the bare one won, the resume was
       discarded as "already active".
    """

    def setUp(self):
        super().setUp()
        from syrix_supercomputer import hud_api

        self.hud_api = hud_api
        self.sid = self.chat_thread.start()
        self.store.sessions[self.sid]["messages"] = [
            {"role": "user", "text": "compare the RTX 5090 and the 4090"},
            {"role": "assistant", "text": "the 5090 is about 30% faster"},
        ]
        self.chat_thread.remember_browser(self.sid, [{"url": "https://nvidia.com/5090"}])
        # A different, live session — as if the user had left and come back.
        self.store.active = ""
        session.exit_mode({})

    def test_reopening_restores_the_transcript_for_the_workspace(self):
        session.enter({})
        self.hud_api._open_chat_thread(self.sid, {}, fresh_session=True)

        restored = session.snapshot({}).get("chat_restored") or []
        self.assertEqual([r["role"] for r in restored], ["user", "assistant"])
        self.assertIn("5090", restored[-1]["text"])

    def test_reopening_restores_the_agent_memory_too(self):
        """Otherwise the transcript reads as continuous and the agent is blank."""
        session.enter({})
        self.hud_api._open_chat_thread(self.sid, {}, fresh_session=True)
        self.assertEqual(len(spine.history()), 2)

    def test_reopening_hands_the_tabs_to_the_workspace_to_open(self):
        """NOT opened from the backend.

        Every Python-side navigation goes through policy.check_navigation,
        which refuses with `no_task_contract` when no task is running — and a
        resume happens outside any task, so restoring from there failed every
        single time. It is also the wrong actor: the user chose this conversation,
        so these are his tabs, opened by the workspace like the start page.
        """
        session.enter({})
        self.hud_api._open_chat_thread(self.sid, {}, fresh_session=True)
        snap = session.snapshot({})
        self.assertEqual(snap.get("restore_tabs"), ["https://nvidia.com/5090"])

    def test_only_real_web_urls_are_ever_restored(self):
        self.chat_thread.remember_browser(self.sid, [
            {"url": "https://ok.example/a"},
            {"url": "file:///C:/Windows/System32/config"},
            {"url": "javascript:alert(1)"},
            {"url": "about:blank"},
        ])
        session.enter({})
        self.hud_api._open_chat_thread(self.sid, {}, fresh_session=True)
        self.assertEqual(session.snapshot({}).get("restore_tabs"),
                         ["https://ok.example/a"])

    def test_reopening_makes_that_session_the_one_new_lines_join(self):
        """Without this the conversation looks continuous and is recorded split.

        It BINDS rather than taking the active pointer (2026-07-27): normal
        chat has to keep its own session while this one is live, or a message
        typed in normal chat while a background task runs lands in here.
        """
        session.enter({})
        self.hud_api._open_chat_thread(self.sid, {}, fresh_session=True)
        self.assertEqual(self.store.bound_session("supercomputer"), self.sid)
        self.assertNotEqual(self.store.active, self.sid)

    def test_reopening_does_not_rotate_a_new_thread(self):
        before = list(self.store.order)
        session.enter({})
        self.hud_api._open_chat_thread(self.sid, {}, fresh_session=True)
        self.assertEqual(self.store.order, before,
                         "a resume must not create a session")

    def test_a_resume_is_honoured_even_when_the_mode_is_already_open(self):
        """The two-enter race: if the bare enter landed first, the resume was
        dropped as 'already active' and the user got a blank chat."""
        session.enter({})
        self.hud_api._open_chat_thread("", {}, fresh_session=True)   # the bare one
        self.hud_api._open_chat_thread(self.sid, {}, fresh_session=False)  # the resume
        restored = session.snapshot({}).get("chat_restored") or []
        self.assertEqual(len(restored), 2)
        self.assertEqual(self.store.bound_session("supercomputer"), self.sid)

    def test_a_plain_re_entry_does_not_rotate_a_live_conversation(self):
        session.enter({})
        self.hud_api._open_chat_thread("", {}, fresh_session=True)
        opened = list(self.store.order)
        self.hud_api._open_chat_thread("", {}, fresh_session=False)
        self.assertEqual(self.store.order, opened)

    def test_only_a_supercomputer_session_may_reopen_the_mode(self):
        """Deny-unknown: a normal text chat must not drag the agentic mode open
        around a transcript that was never a Supercomputer conversation."""
        normal = self.store.new_session(title="Normal chat")
        self.store.sessions[normal]["messages"] = [{"role": "user", "text": "hi"}]
        session.enter({})
        self.hud_api._open_chat_thread(normal, {}, fresh_session=True)
        self.assertEqual(session.snapshot({}).get("chat_restored") or [], [])

    def test_a_fresh_session_clears_a_previous_restore(self):
        session.enter({})
        self.hud_api._open_chat_thread(self.sid, {}, fresh_session=True)
        self.assertTrue(session.snapshot({}).get("chat_restored"))
        self.hud_api._open_chat_thread("", {}, fresh_session=True)
        self.assertEqual(session.snapshot({}).get("chat_restored") or [], [])


class TestPickingATaskBackUp(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.task = tasks.create("research the thing", TaskContract(
            task_id="t1", goal="research the thing",
            allowed_domains=frozenset({"example.com"}),
        ))
        tasks.set_state("t1", tasks.STATE_RUNNING)

    def test_asking_a_question_registers_the_wait(self):
        resume.await_reply("t1", resume.WAIT_QUESTION, question="which template?")
        record = resume.awaiting()
        self.assertEqual(record["task_id"], "t1")
        self.assertEqual(record["kind"], resume.WAIT_QUESTION)

    def test_a_short_reply_is_read_as_an_answer(self):
        for text in ("yes", "the modern one", "navy blue please", "go ahead"):
            self.assertTrue(spine._reads_as_reply(text, "which template?", {}), text)

    def test_a_new_instruction_is_not_read_as_an_answer(self):
        for text in ("now open reddit and find the top post about GPUs",
                     "go to tradingview and chart NVDA for me"):
            self.assertFalse(spine._reads_as_reply(text, "which template?", {}), text)

    def test_a_new_request_stops_the_parked_task_rather_than_orphaning_it(self):
        resume.await_reply("t1", resume.WAIT_DISCUSSION, question="the send")
        out = spine._maybe_continue("rid", "now open reddit and find something else", {})
        self.assertIsNone(out, "a new request must not resume the old task")
        self.assertEqual(resume.awaiting()["task_id"], "")
        self.assertEqual(tasks.get("t1").state, tasks.STATE_STOPPED)

    def test_nothing_awaiting_means_nothing_to_continue(self):
        self.assertIsNone(spine._maybe_continue("rid", "yes", {}))

    def test_a_finished_task_cannot_be_resumed(self):
        resume.await_reply("t1", resume.WAIT_QUESTION)
        tasks.set_state("t1", tasks.STATE_DONE)
        self.assertIsNone(resume.continue_from_chat("yes", config={}))
        self.assertEqual(resume.awaiting()["task_id"], "")

    def test_the_feature_can_be_switched_off(self):
        resume.await_reply("t1", resume.WAIT_QUESTION)
        self.assertIsNone(spine._maybe_continue(
            "rid", "yes", {"supercomputer_resume_from_chat_enabled": False}))
        # …and the wait is left intact rather than silently discarded.
        self.assertEqual(resume.awaiting()["task_id"], "t1")


if __name__ == "__main__":
    unittest.main()
