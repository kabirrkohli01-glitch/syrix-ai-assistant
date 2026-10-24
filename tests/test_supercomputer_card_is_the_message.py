"""The permission card IS the message.

The user, 2026-07-28: the card worked, but answering it deleted the card and posted
a paragraph in its place — "I said no, what do you want me to change" — so one
decision became a hole plus two or three stray chat lines, and because cards
render after every chat line the leftovers landed out of order too.

What these tests pin:

* answering a card does not delete it — it resolves in place, carrying the
  decision and what followed from it;
* Decline and "Chat about this" emit NO chat line at all;
* Allow still reports in the chat, because a completed continuation is a real
  answer and belongs in the conversation;
* the kill switch restores the old behaviour exactly;
* none of this weakens the security properties — a resolved card is display
  only, and a decline still blocks the action for the rest of the task.
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

from syrix_supercomputer import permissions, resume, session, status  # noqa: E402


class _CardCase(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):  # noqa: N802
        super().setUp()
        permissions._reset_for_tests()
        resume._reset_for_tests()
        self.permissions = permissions
        self.resume = resume
        # Capture what would have reached the conversation, so "no stray
        # message" is an assertion rather than an eyeball check.
        self.emitted = []
        self._real_emit = resume._emit
        resume._emit = lambda text, **kw: self.emitted.append(str(text))

    def tearDown(self):  # noqa: N802
        resume._emit = self._real_emit
        permissions._reset_for_tests()
        resume._reset_for_tests()
        super().tearDown()

    def _card(self, task_id="t1", action="send_email", scope="gmail.com",
              description="Send the reply to Sam"):
        return permissions.request(task_id, action, scope, description,
                                   why="you asked me to")


class TestAnsweringKeepsTheCard(_CardCase):
    def test_a_declined_card_stays_and_carries_its_answer(self):
        req = self._card()
        ok, detail = self.permissions.decide(req.request_id,
                                             self.permissions.DECISION_DECLINE)
        self.assertTrue(ok)
        self.assertEqual(detail, "declined")

        self.assertEqual(self.permissions.pending_snapshot(), [],
                         "an answered card must leave the pending list")
        resolved = self.permissions.resolved_snapshot()
        self.assertEqual(len(resolved), 1, "the card itself must survive")
        self.assertEqual(resolved[0]["decision"], "decline")
        self.assertEqual(resolved[0]["description"], "Send the reply to Sam",
                         "a decision you cannot see the subject of records nothing")
        self.assertGreater(resolved[0]["decided_ts"], 0)

    def test_decline_writes_into_the_card_not_the_chat(self):
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_DECLINE)
        res = self.resume.after_decline("t1", "Send the reply to Sam",
                                        config={}, request_id=req.request_id)

        self.assertEqual(res.get("rendered"), "card")
        self.assertEqual(self.emitted, [],
                         "declining must not leave a message in the conversation")
        outcome = self.permissions.resolved_snapshot()[0]["outcome"]
        self.assertIn("Take Control", outcome.get("guidance", ""))
        self.assertTrue(outcome.get("headline"))

    def test_discuss_writes_into_the_card_not_the_chat(self):
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_DISCUSS)
        res = self.resume.after_discuss("t1", "Send the reply to Sam",
                                        config={}, request_id=req.request_id)

        self.assertEqual(res.get("rendered"), "card")
        self.assertEqual(self.emitted, [])
        outcome = self.permissions.resolved_snapshot()[0]["outcome"]
        self.assertIn("differently", outcome.get("guidance", ""))

    def test_discuss_still_parks_the_task_for_a_typed_reply(self):
        """The formation changed; the resume mechanism must not have."""
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_DISCUSS)
        self.resume.after_discuss("t1", "Send the reply to Sam",
                                  config={}, request_id=req.request_id)
        awaiting = self.resume.awaiting()
        self.assertEqual(awaiting["task_id"], "t1")
        self.assertEqual(awaiting["kind"], self.resume.WAIT_DISCUSSION)

    def test_decline_still_parks_the_task_for_a_redirect(self):
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_DECLINE)
        self.resume.after_decline("t1", "Send the reply to Sam",
                                  config={}, request_id=req.request_id)
        self.assertEqual(self.resume.awaiting()["kind"], self.resume.WAIT_REDIRECT)


class TestTheKillSwitch(_CardCase):
    def test_switching_it_off_restores_the_chat_line(self):
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_DECLINE)
        res = self.resume.after_decline(
            "t1", "Send the reply to Sam",
            config={"supercomputer_card_is_the_message": False},
            request_id=req.request_id)

        self.assertEqual(res.get("rendered"), "chat")
        self.assertEqual(len(self.emitted), 1)
        self.assertIn("stopped the task here", self.emitted[0])

    def test_an_unknown_card_falls_back_to_the_chat(self):
        """A continuation with no card to write into must still be visible."""
        res = self.resume.after_decline("t1", "Send the reply to Sam",
                                        config={}, request_id="no-such-card")
        self.assertEqual(res.get("rendered"), "chat")
        self.assertEqual(len(self.emitted), 1)


class TestResolvedCardsAreDisplayOnly(_CardCase):
    def test_a_resolved_decline_still_blocks_the_action(self):
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_DECLINE)
        self.assertTrue(self.permissions.is_denied("t1", "send_email", "gmail.com"),
                        "keeping the card on screen must not un-refuse it")
        self.assertFalse(self.permissions.has_grant("t1", "send_email", "gmail.com"))

    def test_a_resolved_allow_does_not_re_grant_on_replay(self):
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_ALLOW)
        self.assertTrue(self.permissions.consume("t1", "send_email", "gmail.com"))
        # The card is still on screen; replaying it must not mint a second grant.
        ok, detail = self.permissions.decide(req.request_id,
                                             self.permissions.DECISION_ALLOW)
        self.assertFalse(ok)
        self.assertEqual(detail, "request_expired_or_unknown")
        self.assertFalse(self.permissions.consume("t1", "send_email", "gmail.com"))

    def test_note_refuses_to_touch_a_card_that_was_never_answered(self):
        req = self._card()
        self.assertFalse(self.permissions.note(req.request_id, headline="nope"))

    def test_mode_exit_clears_the_answered_cards(self):
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_DECLINE)
        self.permissions.revoke_all(reason="mode_exit")
        self.assertEqual(self.permissions.resolved_snapshot(), [])

    def test_the_history_is_capped(self):
        for i in range(self.permissions.RESOLVED_KEEP + 6):
            req = self._card(description=f"card {i}")
            self.permissions.decide(req.request_id, self.permissions.DECISION_DECLINE)
        resolved = self.permissions.resolved_snapshot()
        self.assertEqual(len(resolved), self.permissions.RESOLVED_KEEP)
        self.assertEqual(resolved[-1]["description"],
                         f"card {self.permissions.RESOLVED_KEEP + 5}",
                         "the newest answer must be the one kept")


class TestAnExpiredCardSaysSo(_CardCase):
    def test_it_becomes_a_visible_expired_card_not_a_silent_removal(self):
        req = self._card()
        # Age it past the TTL rather than sleeping.
        self.permissions._PENDING[req.request_id].created_ts -= (
            self.permissions.CONFIRM_TTL_S + 5)
        self.assertEqual(self.permissions.pending_snapshot(), [])
        resolved = self.permissions.resolved_snapshot()
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["decision"], "expired")

    def test_an_expired_card_grants_nothing(self):
        req = self._card()
        self.permissions._PENDING[req.request_id].created_ts -= (
            self.permissions.CONFIRM_TTL_S + 5)
        self.permissions.pending_snapshot()
        self.assertFalse(self.permissions.has_grant("t1", "send_email", "gmail.com"))
        ok, detail = self.permissions.decide(req.request_id,
                                             self.permissions.DECISION_ALLOW)
        self.assertFalse(ok, "an expired card must not be answerable")


class TestTheSnapshotCarriesBoth(_CardCase):
    def test_session_snapshot_exposes_resolved_cards(self):
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_DECLINE)
        snap = session.snapshot({})
        self.assertIn("resolved_permission", snap)
        self.assertEqual(len(snap["resolved_permission"]), 1)
        self.assertEqual(snap["pending_permission"], [])

    def test_a_resolved_card_does_not_read_as_waiting(self):
        """The amber "waiting for permission" state belongs to open cards only."""
        req = self._card()
        self.permissions.decide(req.request_id, self.permissions.DECISION_DECLINE)
        derived = status.derive(task=None, browser={},
                                pending_permission=self.permissions.pending_snapshot())
        self.assertNotEqual(derived.get("key"), "waiting")


if __name__ == "__main__":
    unittest.main()
