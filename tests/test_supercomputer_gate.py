"""The Supercomputer choke point: gate ordering, capability enforcement, and
the Allow / Decline / Chat-about-this contract.

The properties that matter:

* an undeclared verb fails closed rather than running ungated;
* a capability switched off is *technically unavailable* — absent from the tool
  surface AND refused at the gate;
* a side effect cannot happen without an answered card, every single time;
* Decline blocks the effect rather than the phrasing, and "Chat about this"
  halts the task with nothing left running.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import audit, capabilities, gate, permissions, tasks  # noqa: E402
from syrix_supercomputer.contract import TaskContract  # noqa: E402

_ON = {"supercomputer_enabled": True, "supercomputer_tools_enabled": True}


class _GateCase(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.ran = []
        self.task = tasks.create("test goal", TaskContract(
            task_id="t1", goal="test goal",
            allowed_domains=frozenset({"example.com"}),
        ))
        tasks.set_state("t1", tasks.STATE_RUNNING)

    def work(self, value=None):
        def _fn():
            self.ran.append(True)
            return value if value is not None else {"ok": True, "data": {"done": True}}
        return _fn

    def run_action(self, action, **kw):
        kw.setdefault("task_id", "t1")
        kw.setdefault("scope", "example.com")
        kw.setdefault("config", dict(_ON))
        return gate.execute(action, self.work(), **kw)


class TestKillSwitches(_GateCase):
    def test_master_switch_off_runs_nothing(self):
        res = self.run_action("read_page", config={"supercomputer_enabled": False})
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "supercomputer_disabled")
        self.assertEqual(self.ran, [])

    def test_tools_switch_off_runs_nothing(self):
        res = self.run_action("read_page", config={
            "supercomputer_enabled": True, "supercomputer_tools_enabled": False})
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "tools_disabled")
        self.assertEqual(self.ran, [])

    def test_a_sub_flag_cannot_be_on_when_the_master_is_off(self):
        self.assertFalse(gate.tools_enabled({
            "supercomputer_enabled": False, "supercomputer_tools_enabled": True}))


class TestDenyUnknown(_GateCase):
    def test_undeclared_action_fails_closed(self):
        res = self.run_action("rm_rf_slash")
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "unknown_action")
        self.assertEqual(self.ran, [], "an undeclared verb must never run")

    def test_every_declared_action_maps_to_a_capability(self):
        for cap in capabilities.CAPABILITIES.values():
            for action in cap.actions:
                self.assertIsNotNone(capabilities.capability_for(action))


class TestCapabilityEnforcement(_GateCase):
    def test_destructive_capabilities_are_off_by_default(self):
        """Nothing that costs money or destroys something may be on out of the box."""
        for key in ("send_messages", "delete_online_content", "payments",
                    "account_settings", "publish", "downloads",
                    "agree_to_terms", "export_outside_chat"):
            cap = capabilities.CAPABILITIES[key]
            self.assertFalse(cap.default_on, f"{key} must default to OFF")

    def test_the_capabilities_turned_on_are_all_pinned_to_always_ask(self):
        """uploads and submit_forms went ON on 2026-07-26, and that is only
        acceptable because neither can happen without a card in ANY mode."""
        from syrix_supercomputer import safety_mode

        for key in ("uploads", "submit_forms"):
            self.assertTrue(capabilities.CAPABILITIES[key].default_on, key)
            for action in capabilities.CAPABILITIES[key].actions:
                self.assertIn(action, safety_mode.ALWAYS_ASK,
                              f"{action} is available by default and MUST always ask")

    def test_only_non_destructive_capabilities_are_on_by_default(self):
        """The default-ON set is everything needed to browse, read and work, and
        nothing that changes the world unasked.

        Reaching a signed-in account, reading private documents, reading the user's
        own files, attaching one to a form and submitting it are ON because the
        mode is useless without them — but every one is side-effecting, so each
        still raises a card (asserted below and in the always-ask tests).
        """
        on = {c.key for c in capabilities.CAPABILITIES.values() if c.default_on}
        self.assertEqual(on, {"browse", "read_page", "interact", "workspace_files",
                              "compute", "consult_assistant",
                              "authenticated_sites", "read_private_documents",
                              "draft_messages", "file_read", "uploads",
                              "submit_forms"})
        for c in capabilities.CAPABILITIES.values():
            if c.default_on and c.key in ("browse", "read_page", "interact",
                                          "workspace_files", "compute",
                                          "consult_assistant"):
                self.assertFalse(c.side_effecting, f"{c.key} must not be side-effecting")

    def test_every_capability_has_a_tool_or_a_classifier_behind_it(self):
        """No switch may exist that nothing can call.

        Thirteen of them did until 2026-07-26 — file_write_external, clipboard,
        open_local_project, work_on_syrix, grant_site_microphone and friends
        were rendered on the Settings page with no tool anywhere. Flipping one
        told the user he had granted something, and granted nothing.
        """
        from syrix_supercomputer import action_intent, tools

        emitted = {t.action for t in tools.TOOLS.values() if t.action}
        emitted |= set(action_intent._CAP_FOR_ACTION)
        # Raised by tools.py directly rather than through the Tool table:
        # `use_account` when a sensitive/account domain is opened, and the
        # connector verbs mapped by tools.connector_gate_action.
        emitted.add("use_account")
        emitted |= {action for _token, action in tools._CONNECTOR_ACTION_HINTS}
        emitted.add("read_private_document")

        for cap in capabilities.CAPABILITIES.values():
            self.assertTrue(
                set(cap.actions) & emitted,
                f"capability {cap.key!r} has no action anything can emit — "
                f"either wire a tool to it or delete it",
            )

    def test_the_private_defaults_still_ask_every_time(self):
        """Default-ON is availability, never authority."""
        for action in ("read_private_document", "use_account", "draft_message"):
            self.assertTrue(capabilities.requires_confirmation(action), action)

    def test_reading_approvals_are_reusable_and_writes_are_not(self):
        """One card per message read would train people to click Allow blind;
        one card per send is the entire point of the send card."""
        self.assertGreater(capabilities.grant_uses("read_private_document"), 1)
        self.assertEqual(capabilities.grant_uses("send_email"), 1)
        self.assertEqual(capabilities.grant_uses("purchase"), 1)

    def test_disabled_capability_asks_rather_than_dead_ending(self):
        """A switched-off side-effecting capability raises the same card as
        anything else. Nothing runs without an answer — the change is that the
        answer is one click away rather than buried in a settings page."""
        res = self.run_action("send_email", scope="gmail.com", config=dict(_ON))
        self.assertFalse(res.ok)
        self.assertTrue(res.needs_permission)
        self.assertEqual(self.ran, [], "the action must not run unanswered")

    def test_strict_mode_restores_the_hard_refusal(self):
        res = self.run_action("send_email", scope="gmail.com",
                              config=dict(_ON, supercomputer_strict_capabilities=True))
        self.assertFalse(res.ok)
        self.assertFalse(res.needs_permission)
        self.assertEqual(res.reason, "capability_disabled")
        self.assertEqual(self.ran, [])

    def test_a_disabled_read_only_capability_is_still_a_flat_refusal(self):
        """Only side-effecting capabilities become cards. A read-only one that
        is off has nothing to approve — it is simply switched off."""
        res = self.run_action("open_url", scope="example.com",
                              url="https://example.com/",
                              config=dict(_ON, supercomputer_browser_enabled=False))
        self.assertFalse(res.ok)
        self.assertFalse(res.needs_permission)

    def test_disabled_capability_is_absent_from_the_tool_surface(self):
        """Unavailable, not merely hidden: the model is never offered it."""
        available = capabilities.enabled_actions(dict(_ON))
        self.assertNotIn("send_email", available)
        self.assertNotIn("delete_online_content", available)
        self.assertIn("read_page", available)
        self.assertIn("open_url", available)

    def test_enabling_a_capability_still_requires_a_card(self):
        """Turning it on makes the card reachable; it grants nothing itself."""
        cfg = dict(_ON, supercomputer_capability_send_messages=True)
        res = self.run_action("send_email", scope="gmail.com", config=cfg)
        self.assertTrue(res.needs_permission)
        self.assertEqual(self.ran, [], "no side effect before the user answers")


class TestPermissionFlow(_GateCase):
    def _cfg(self):
        return dict(_ON, supercomputer_capability_send_messages=True)

    def test_read_only_action_needs_no_card(self):
        res = self.run_action("read_page")
        self.assertTrue(res.ok)
        self.assertEqual(len(self.ran), 1)

    def test_side_effect_parks_a_card_and_halts_the_task(self):
        res = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        self.assertTrue(res.needs_permission)
        self.assertTrue(res.permission_request_id)
        self.assertEqual(tasks.get("t1").state, tasks.STATE_AWAITING_PERMISSION)
        self.assertEqual(self.ran, [])

    def test_allow_then_retry_executes_once(self):
        first = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        gate.resolve_permission(first.permission_request_id, "allow")
        self.assertEqual(tasks.get("t1").state, tasks.STATE_RUNNING)

        second = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        self.assertTrue(second.ok)
        self.assertEqual(len(self.ran), 1)

    def test_a_grant_is_single_use(self):
        """An approval is for the action described, not an ongoing licence."""
        first = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        gate.resolve_permission(first.permission_request_id, "allow")
        self.run_action("send_email", scope="gmail.com", config=self._cfg())

        third = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        self.assertTrue(third.needs_permission, "the second send must ask again")
        self.assertEqual(len(self.ran), 1)

    def test_decline_stops_the_task_and_blocks_the_action(self):
        """No means stop, not "skip that and carry on". The task ends where it
        stands and the action never runs."""
        first = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        gate.resolve_permission(first.permission_request_id, "decline")

        self.assertEqual(tasks.get("t1").state, tasks.STATE_STOPPED)
        second = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        self.assertFalse(second.ok)
        self.assertEqual(self.ran, [])

    def test_a_decline_is_remembered_for_the_task(self):
        """Recorded against the effect, not that spelling of the scope — so it
        still holds if the task is resumed and the request comes back reworded."""
        first = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        gate.resolve_permission(first.permission_request_id, "decline")

        self.assertTrue(permissions.is_denied("t1", "send_email", "gmail.com"))
        self.assertTrue(permissions.is_denied("t1", "send_email", "  GMAIL.COM  "))

    def test_a_declined_action_is_refused_even_once_running_again(self):
        first = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        gate.resolve_permission(first.permission_request_id, "decline")
        tasks.set_state("t1", tasks.STATE_RUNNING)   # e.g. The user resumed it

        retry = self.run_action("send_email", scope="  GMAIL.COM  ", config=self._cfg())
        self.assertEqual(retry.reason, "previously_declined")
        self.assertEqual(self.ran, [])

    def test_chat_about_this_halts_with_nothing_running(self):
        first = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        gen_before = tasks.generation("t1")
        gate.resolve_permission(first.permission_request_id, "discuss")

        self.assertEqual(tasks.get("t1").state, tasks.STATE_PAUSED_DISCUSSION)
        self.assertGreater(tasks.generation("t1"), gen_before,
                           "in-flight work must be invalidated, not left running")

        follow = self.run_action("read_page", config=self._cfg())
        self.assertFalse(follow.ok, "nothing may continue in the background")
        self.assertEqual(follow.reason, "task_paused_discussion")
        self.assertEqual(self.ran, [])

    def test_an_expired_card_cannot_be_answered(self):
        first = self.run_action("send_email", scope="gmail.com", config=self._cfg())
        permissions._PENDING.clear()
        res = gate.resolve_permission(first.permission_request_id, "allow")
        self.assertFalse(res["ok"])

    def test_decision_only_comes_from_the_card_channel(self):
        """There is no other function that can mint a grant."""
        self.assertFalse(permissions.has_grant("t1", "send_email", "gmail.com"))
        for name in ("grant", "allow", "authorise", "authorize", "approve_action"):
            self.assertFalse(hasattr(gate, name),
                             f"gate.{name} would be a second way to create authority")


class TestScopeGate(_GateCase):
    def test_an_ordinary_public_site_is_browsing_not_an_escalation(self):
        """Following a link off the named site is what research is. It runs, and
        the destination does NOT join the contract."""
        res = self.run_action("open_url", scope="elsewhere.example",
                              url="https://elsewhere.example/")
        self.assertTrue(res.ok)
        self.assertNotIn("elsewhere.example",
                         tasks.current().contract.allowed_domains)

    def test_open_navigation_off_refuses_without_asking(self):
        """With the kill switch off, an unnamed destination is refused outright —
        asking about it would train approval."""
        res = self.run_action("open_url", scope="evil.example",
                              url="https://evil.example/",
                              config=dict(_ON, supercomputer_open_navigation=False))
        self.assertFalse(res.ok)
        self.assertFalse(res.needs_permission)
        self.assertEqual(res.reason, "domain_not_in_task_scope")
        self.assertEqual(self.ran, [])

    def test_a_sensitive_destination_raises_a_card_instead_of_running(self):
        res = self.run_action("open_url", scope="mail.google.com",
                              url="https://mail.google.com/mail/u/0")
        self.assertFalse(res.ok)
        self.assertTrue(res.needs_permission)
        self.assertEqual(self.ran, [])

    def test_in_scope_url_runs(self):
        res = self.run_action("open_url", scope="example.com",
                              url="https://example.com/page")
        self.assertTrue(res.ok)

    def test_private_url_refused_even_when_in_scope(self):
        tasks.create("t2 goal", TaskContract(
            task_id="t2", goal="g", allowed_domains=frozenset({"192.168.1.1"})))
        tasks.set_state("t2", tasks.STATE_RUNNING)
        res = gate.execute("open_url", self.work(), task_id="t2",
                           scope="192.168.1.1", url="http://192.168.1.1/",
                           config=dict(_ON))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "private_network")


class TestTaskState(_GateCase):
    def test_stopped_task_runs_nothing(self):
        tasks.stop("t1")
        res = self.run_action("read_page")
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "task_stopped")
        self.assertEqual(self.ran, [])

    def test_paused_task_runs_nothing(self):
        tasks.set_state("t1", tasks.STATE_PAUSED_USER)
        res = self.run_action("read_page")
        self.assertFalse(res.ok)
        self.assertEqual(self.ran, [])


class TestBudgetAndErrors(_GateCase):
    def test_a_wedged_action_times_out(self):
        def _hang():
            time.sleep(5)
            return {"ok": True}

        res = gate.execute("read_page", _hang, task_id="t1", scope="example.com",
                           config=dict(_ON), timeout_s=0.5)
        self.assertFalse(res.ok)
        self.assertEqual(res.result, gate.RESULT_TIMEOUT)
        self.assertIn("stopped it", res.message)

    def test_a_raising_action_is_reported_not_propagated(self):
        def _boom():
            raise RuntimeError("browser exploded")

        res = gate.execute("read_page", _boom, task_id="t1", scope="example.com",
                           config=dict(_ON))
        self.assertFalse(res.ok)
        self.assertEqual(res.result, gate.RESULT_ERROR)
        self.assertIn("RuntimeError", res.reason)

    def test_every_refusal_explains_itself(self):
        """A refusal the user cannot understand is a bug."""
        cases = [
            self.run_action("rm_rf_slash"),
            self.run_action("send_email", scope="gmail.com"),
            self.run_action("open_url", scope="evil.example", url="https://evil.example/",
                            config=dict(_ON, supercomputer_open_navigation=False)),
        ]
        for res in cases:
            self.assertTrue(res.message.strip(), f"{res.reason} produced no message")


class TestAudit(_GateCase):
    def test_outcomes_are_recorded(self):
        self.run_action("read_page")
        self.run_action("rm_rf_slash")
        entries = audit.recent(20, task_id="t1")
        actions = [e["action"] for e in entries]
        self.assertIn("read_page", actions)
        self.assertIn("rm_rf_slash", actions)

    def test_audit_records_no_content(self):
        """Counts and reasons only — never message bodies or typed text."""
        self.run_action("read_page", scope="example.com")
        raw = ""
        path = os.path.join(self._tmp, "audit.jsonl")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        self.assertTrue(raw.strip())
        for line in raw.strip().splitlines():
            entry = json.loads(line)
            self.assertEqual(
                set(entry) - {"ts", "action", "result", "task_id", "scope",
                              "reason", "actor", "request_id"},
                set(), "audit gained an unexpected field, which may carry content",
            )

    def test_the_kill_switch_path_is_not_audited(self):
        """The subsystem is meant to be inert when off, including its logging."""
        self.run_action("read_page", config={"supercomputer_enabled": False})
        self.assertEqual(audit.recent(20, task_id="t1"), [])


if __name__ == "__main__":
    unittest.main()
