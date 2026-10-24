"""Permission grant lifetime.

The rule the user set: a grant is void on **task end**, on **mode exit**, and on
**app restart**. Nothing durable, no "remember this", no hidden setting that
pre-approves an action.

That means the interesting assertions here are largely negative — about what
does *not* exist and what does *not* survive. A grant that outlived its context
would be authority the user never knowingly gave.
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

from syrix_supercomputer import capabilities, paths, permissions, session, tasks  # noqa: E402
from syrix_supercomputer.contract import TaskContract  # noqa: E402


class _PermCase(SupercomputerTestMixin, unittest.TestCase):
    def grant(self, task_id="t1", action="send_email", scope="gmail.com"):
        req = permissions.request(task_id, action, scope, f"{action} at {scope}")
        permissions.decide(req.request_id, permissions.DECISION_ALLOW)
        return req


class TestGrantLifetime(_PermCase):
    def test_a_grant_works_while_its_task_runs(self):
        self.grant()
        self.assertTrue(permissions.has_grant("t1", "send_email", "gmail.com"))

    def test_a_grant_dies_when_its_task_ends(self):
        tasks.create("g", TaskContract(task_id="t1", goal="g"))
        tasks.set_state("t1", tasks.STATE_RUNNING)
        self.grant()

        tasks.set_state("t1", tasks.STATE_DONE)
        self.assertFalse(permissions.has_grant("t1", "send_email", "gmail.com"))

    def test_a_grant_dies_when_the_task_is_stopped(self):
        tasks.create("g", TaskContract(task_id="t1", goal="g"))
        tasks.set_state("t1", tasks.STATE_RUNNING)
        self.grant()

        tasks.stop("t1")
        self.assertFalse(permissions.has_grant("t1", "send_email", "gmail.com"))

    def test_a_grant_dies_when_the_task_fails(self):
        tasks.create("g", TaskContract(task_id="t1", goal="g"))
        self.grant()
        tasks.set_state("t1", tasks.STATE_FAILED)
        self.assertFalse(permissions.has_grant("t1", "send_email", "gmail.com"))

    def test_a_grant_dies_on_mode_exit(self):
        session.enter({})
        self.grant()
        session.exit_mode({})
        self.assertFalse(permissions.has_grant("t1", "send_email", "gmail.com"))

    def test_a_grant_from_a_previous_boot_is_void(self):
        """The boot_id stamp means even a grant that somehow reached disk could
        not be honoured after a restart."""
        self.grant()
        g = permissions._GRANTS["t1"][0]
        self.assertTrue(g.is_valid())

        g.boot_id = "some-previous-boot"
        self.assertFalse(g.is_valid())
        self.assertFalse(permissions.has_grant("t1", "send_email", "gmail.com"))
        self.assertFalse(permissions.consume("t1", "send_email", "gmail.com"))

    def test_grants_do_not_leak_between_tasks(self):
        self.grant(task_id="t1")
        self.assertFalse(permissions.has_grant("t2", "send_email", "gmail.com"))


class TestNothingIsPersisted(_PermCase):
    def test_no_grant_file_is_written(self):
        self.grant()
        listing = os.listdir(self._tmp) if os.path.isdir(self._tmp) else []
        for name in listing:
            self.assertNotIn("permission", name.lower(),
                             f"{name} looks like persisted authority")
            self.assertNotIn("grant", name.lower(),
                             f"{name} looks like persisted authority")

    def test_the_paths_module_offers_no_grant_store(self):
        """There is deliberately no permissions.json: nothing to persist."""
        self.assertFalse(hasattr(paths, "permissions_path"))
        # `settings_path` was removed on 2026-07-26. It promised a settings.json
        # that nothing ever read or wrote — capability toggles have always lived
        # in the main config.json — so the helper was a claim the code did not
        # honour. Asserting its ABSENCE keeps it from being reintroduced.
        self.assertFalse(hasattr(paths, "settings_path"),
                         "capability toggles live in config.json, not a private file")

    def test_there_is_no_remember_this_option(self):
        for name in ("remember", "always_allow", "persist_grant", "save_grant",
                     "grant_forever", "set_permanent"):
            self.assertFalse(hasattr(permissions, name),
                             f"permissions.{name} would outlive its context")

    def test_only_three_decisions_exist(self):
        self.assertEqual(
            set(permissions.DECISIONS), {"allow", "decline", "discuss"},
            "a fourth decision would be a way to widen authority",
        )


class TestScopeAndDenial(_PermCase):
    def test_a_grant_is_scoped_to_exactly_what_was_asked(self):
        self.grant(action="send_email", scope="gmail.com")
        self.assertFalse(permissions.has_grant("t1", "send_email", "outlook.com"))
        self.assertFalse(permissions.has_grant("t1", "delete_online_content", "gmail.com"))

    def test_scope_matching_is_normalised(self):
        """So a decline cannot be dodged, nor a grant accidentally missed, by
        whitespace or case."""
        self.grant(scope="Gmail.com")
        self.assertTrue(permissions.has_grant("t1", "send_email", "  gmail.com "))

    def test_a_declined_effect_stays_declined_for_the_task(self):
        req = permissions.request("t1", "delete_online_content", "gmail.com", "Delete it")
        permissions.decide(req.request_id, permissions.DECISION_DECLINE)
        self.assertTrue(permissions.is_denied("t1", "delete_online_content", "gmail.com"))

    def test_a_decline_does_not_leak_to_another_task(self):
        req = permissions.request("t1", "delete_online_content", "gmail.com", "Delete it")
        permissions.decide(req.request_id, permissions.DECISION_DECLINE)
        self.assertFalse(permissions.is_denied("t2", "delete_online_content", "gmail.com"))

    def test_revoking_a_task_keeps_its_denials(self):
        """Grants and denials point opposite ways. A grant is authority and must
        not outlive the work it was given for; a denial is a restriction, and
        dropping it on task end would mean a refusal stopped applying the moment
        the task it was given in ended — which is exactly when "No" now stops
        the task. Denials go on mode exit, with everything else."""
        req = permissions.request("t1", "delete_online_content", "gmail.com", "Delete it")
        permissions.decide(req.request_id, permissions.DECISION_DECLINE)
        permissions.revoke_task("t1")
        self.assertTrue(permissions.is_denied("t1", "delete_online_content", "gmail.com"))
        self.assertFalse(permissions.has_grant("t1", "delete_online_content", "gmail.com"))

    def test_mode_exit_clears_denials_as_well_as_grants(self):
        req = permissions.request("t1", "delete_online_content", "gmail.com", "Delete it")
        permissions.decide(req.request_id, permissions.DECISION_DECLINE)
        permissions.revoke_all(reason="mode_exit")
        self.assertFalse(permissions.is_denied("t1", "delete_online_content", "gmail.com"))


class TestPendingCards(_PermCase):
    def test_a_pending_card_grants_nothing(self):
        permissions.request("t1", "send_email", "gmail.com", "Send it")
        self.assertFalse(permissions.has_grant("t1", "send_email", "gmail.com"))

    def test_an_unanswered_card_expires(self):
        req = permissions.request("t1", "send_email", "gmail.com", "Send it")
        req.created_ts -= permissions.CONFIRM_TTL_S + 1
        self.assertIsNone(permissions.pending(req.request_id))
        self.assertEqual(permissions.pending_snapshot(), [])

    def test_a_card_is_single_use(self):
        req = permissions.request("t1", "send_email", "gmail.com", "Send it")
        ok, _ = permissions.decide(req.request_id, permissions.DECISION_ALLOW)
        self.assertTrue(ok)
        ok2, detail = permissions.decide(req.request_id, permissions.DECISION_ALLOW)
        self.assertFalse(ok2, "replaying a card must not mint a second grant")
        self.assertEqual(detail, "request_expired_or_unknown")

    def test_the_hud_snapshot_carries_labels_not_payloads(self):
        permissions.request("t1", "send_email", "gmail.com",
                            "Reply to Sam about Friday", why="you asked me to")
        snap = permissions.pending_snapshot()[0]
        # An allowlist on purpose: whatever else a card grows, page content must
        # never become one of its fields.
        self.assertEqual(
            set(snap),
            {"request_id", "task_id", "action", "scope", "description", "why",
             "url", "created_ts", "decision", "decided_ts", "outcome"},
        )

    def test_an_unanswered_card_has_no_decision(self):
        permissions.request("t1", "send_email", "gmail.com", "Reply to Sam")
        snap = permissions.pending_snapshot()[0]
        self.assertEqual(snap["decision"], "")
        self.assertEqual(snap["outcome"], {})
        self.assertEqual(permissions.resolved_snapshot(), [])


class TestCapabilitiesAreNotAuthority(_PermCase):
    def test_enabling_a_capability_creates_no_grant(self):
        cfg = {"supercomputer_enabled": True, "supercomputer_capability_send_messages": True}
        self.assertTrue(capabilities.is_enabled("send_email", cfg))
        self.assertFalse(permissions.has_grant("t1", "send_email", "gmail.com"),
                         "a settings toggle must never be authority to act")

    def test_capability_toggles_persist_but_grants_do_not(self):
        """The durable/ephemeral split, stated as a test so it cannot drift."""
        self.assertFalse(hasattr(permissions, "save_state"))
        self.assertFalse(hasattr(permissions, "load_state"))

    def test_the_memory_brief_is_work_product_not_authority(self):
        """Task memory persists to disk. It must never carry a grant.

        The brief exists so a task survives a permission card without losing
        what it learned. That is only acceptable while it stays a record of
        what was READ and MADE — the moment it could carry "The user allowed X",
        persisting it would smuggle authority past the boot_id rule.
        """
        from syrix_supercomputer import memory

        memory.start("t-auth", "goal")
        memory.note_fact("t-auth", "the page said 42", source="https://example.com")
        memory.note_decision("t-auth", "Stopped to ask permission for send_email")
        memory.save("t-auth", config={})

        with open(paths.task_memory_path("t-auth"), "r", encoding="utf-8") as fh:
            raw = fh.read()
        for forbidden in ("boot_id", "uses_remaining", "granted", "allow"):
            self.assertNotIn(forbidden, raw.lower(),
                             f"a persisted brief must not contain {forbidden!r}")

        # And the loaded brief cannot satisfy the gate: a fresh process has no
        # grants at all, whatever the brief says happened.
        memory._reset_for_tests()
        restored = memory.get("t-auth")
        self.assertIsNotNone(restored)
        self.assertFalse(hasattr(restored, "grants"))
        self.assertFalse(permissions.has_grant("t-auth", "send_email", "anywhere"))


if __name__ == "__main__":
    unittest.main()
