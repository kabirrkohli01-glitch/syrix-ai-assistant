"""Supercomputer browser sandbox — policy, control boundary, and isolation.

The properties pinned here are the ones §4/§5 are actually asking for:

* the agent can only reach what the transport policy AND the task scope both
  allow, and a user-approved domain still cannot become a route to the local
  network;
* Stop and Take Control are enforced in Python, so a stuck renderer cannot
  defeat them, and an op already in flight is discarded rather than landing
  late;
* the Supercomputer browser shares no state, no partition and no message type
  with the existing HUD app dock.
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

from syrix_supercomputer.browser import controller, policy, transport  # noqa: E402
from syrix_supercomputer.contract import TaskContract  # noqa: E402

_CFG = {"supercomputer_enabled": True, "supercomputer_browser_enabled": True}


class _FakeTransport:
    """Records ops instead of talking to Electron."""

    def __init__(self, reply=None):
        self.ops = []
        self._reply = reply or {"ok": True, "reason": "", "data": {}}

    def __call__(self, verb, *, timeout_s=12.0, **fields):
        self.ops.append(dict(fields, verb=verb))
        if verb == "tabs" and self._reply.get("ok"):
            # What the real Electron module reports back: the profile actually
            # in use, which is the HUD browser's own persistent one.
            return {"ok": True, "reason": "", "data": {
                "tabs": [], "partition": "persist:syrix-hud-browser",
                "sharedProfile": True,
            }}
        return dict(self._reply)


class _BrowserCase(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.fake = _FakeTransport()
        self._real_send = controller.send
        controller.send = self.fake

    def tearDown(self):
        controller.send = self._real_send
        super().tearDown()

    def arm(self, *domains):
        """Open a session with a task scoped to the given domains."""
        controller.ensure_open(config=_CFG)
        controller.set_contract(TaskContract(
            task_id="t1", goal="test", allowed_domains=frozenset(domains),
        ))


class TestTransportPolicy(unittest.TestCase):
    """Rules that hold no matter what the task is."""

    def tearDown(self):
        policy._reset_for_tests()

    def test_only_http_and_https(self):
        for url in ("javascript:alert(1)", "file:///C:/Windows/win.ini",
                    "data:text/html,<h1>x", "blob:https://x/y", "ftp://example.com"):
            ok, reason = policy.check_url(url)
            self.assertFalse(ok, f"{url} should be refused")
            self.assertTrue(reason.startswith("scheme_blocked"), reason)

    def test_localhost_and_private_lan_refused(self):
        for url in ("http://localhost:8080/", "http://127.0.0.1/", "http://[::1]/",
                    "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.4/",
                    "http://169.254.169.254/latest/meta-data/",
                    "http://printer.local/", "http://box.internal/"):
            ok, reason = policy.check_url(url)
            self.assertFalse(ok, f"{url} should be refused")
            self.assertEqual(reason, "private_network")

    def test_ipv6_loopback_in_every_spelling_refused(self):
        """WHATWG URL keeps the brackets in hostname where Python strips them,
        so the two enforcement layers must be checked against the same set."""
        for url in ("http://[::1]/", "http://[0:0:0:0:0:0:0:1]/",
                    "http://[::ffff:127.0.0.1]/", "http://[::ffff:192.168.1.1]/"):
            ok, reason = policy.check_url(url)
            self.assertFalse(ok, f"{url} should be refused")
            self.assertEqual(reason, "private_network")

    def test_credentials_in_url_refused(self):
        """https://github.com@evil.example is how a host check gets pointed at
        the wrong authority."""
        ok, reason = policy.check_url("https://github.com@evil.example/path")
        self.assertFalse(ok)
        self.assertEqual(reason, "credentials_in_url")

    def test_admin_paths_refused(self):
        ok, reason = policy.check_url("https://example.com/wp-admin/")
        self.assertFalse(ok)
        self.assertEqual(reason, "admin_path")

    def test_ordinary_https_allowed(self):
        ok, reason = policy.check_url("https://en.wikipedia.org/wiki/Chromium")
        self.assertTrue(ok, reason)

    def test_preview_origin_is_one_exact_host_port(self):
        """The single loopback exception must not open the interface."""
        policy.set_preview_origin("127.0.0.1:7321")
        ok, _ = policy.check_url("http://127.0.0.1:7321/index.html")
        self.assertTrue(ok, "the registered preview must be reachable")
        ok, reason = policy.check_url("http://127.0.0.1:7322/")
        self.assertFalse(ok, "a different port must stay blocked")
        self.assertEqual(reason, "private_network")
        policy.clear_preview_origin()
        ok, _ = policy.check_url("http://127.0.0.1:7321/index.html")
        self.assertFalse(ok, "clearing the preview must re-block it")


class TestTaskScope(unittest.TestCase):
    """Which domains THIS task may reach."""

    def test_no_contract_means_no_navigation(self):
        ok, reason = policy.check_scope("https://example.com", None)
        self.assertFalse(ok)
        self.assertEqual(reason, "no_task_contract")

    def test_in_scope_domain_allowed(self):
        c = TaskContract(task_id="t", goal="g", allowed_domains=frozenset({"example.com"}))
        ok, _ = policy.check_scope("https://example.com/page", c)
        self.assertTrue(ok)

    def test_subdomain_is_in_contract_but_a_suffix_spoof_is_not(self):
        """The spoof-suffix rule is about CONTRACT MEMBERSHIP, which is what
        gates the actions that matter. Open navigation may still let the agent
        look at github.com.evil.example — reading a public page is browsing —
        but it never counts as the approved github.com."""
        c = TaskContract(task_id="t", goal="g", allowed_domains=frozenset({"github.com"}))
        self.assertTrue(c.allows_url("https://api.github.com/x"))
        self.assertFalse(c.allows_url("https://github.com.evil.example/x"))
        self.assertFalse(c.allows_url("https://notgithub.com/x"))

    def test_a_suffix_spoof_is_refused_outright_with_open_navigation_off(self):
        c = TaskContract(task_id="t", goal="g", allowed_domains=frozenset({"github.com"}))
        strict = {"supercomputer_open_navigation": False}
        self.assertTrue(policy.check_scope("https://api.github.com/x", c, config=strict)[0])
        self.assertFalse(
            policy.check_scope("https://github.com.evil.example/x", c, config=strict)[0])
        self.assertFalse(policy.check_scope("https://notgithub.com/x", c, config=strict)[0])

    def test_approved_domain_still_cannot_reach_the_local_network(self):
        """Transport policy is checked first and never widens, so approving a
        domain can never become a route to a device on the home network."""
        c = TaskContract(task_id="t", goal="g",
                         allowed_domains=frozenset({"localhost", "192.168.1.1"}))
        ok, reason = policy.check_navigation("http://192.168.1.1/", c)
        self.assertFalse(ok)
        self.assertEqual(reason, "private_network")


class TestSensitiveText(unittest.TestCase):
    def test_credentials_are_never_typed(self):
        for text in ("hunter2 password", "my CVV is 123", "seed phrase: abandon abandon"):
            ok, reason = policy.check_typed_text(text)
            self.assertFalse(ok, f"{text!r} should be refused")
            self.assertEqual(reason, "refused_sensitive_text")

    def test_ordinary_text_is_fine(self):
        ok, _ = policy.check_typed_text("chromium release notes")
        self.assertTrue(ok)


class TestControlBoundary(_BrowserCase):
    """Stop and Take Control are enforced in Python, not in the UI."""

    def test_agent_may_act_when_it_holds_control(self):
        self.arm("example.com")
        self.fake.ops.clear()  # arm() opens the session, which probes the profile
        res = controller.open_url("https://example.com", config=_CFG)
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertEqual([o["verb"] for o in self.fake.ops], ["open"])

    def test_take_control_blocks_every_input_verb(self):
        self.arm("example.com")
        controller.take_control()
        self.fake.ops.clear()

        for call in (
            lambda: controller.open_url("https://example.com", config=_CFG),
            lambda: controller.click("t", "e1", config=_CFG),
            lambda: controller.type_text("t", "e1", "hello", config=_CFG),
            lambda: controller.scroll("t", 300, config=_CFG),
            lambda: controller.back("t", config=_CFG),
        ):
            res = call()
            self.assertFalse(res["ok"])
            self.assertEqual(res["reason"], "user_in_control")
        self.assertEqual(self.fake.ops, [],
                         "no input op may reach the browser while the user drives")

    def test_reading_still_works_while_the_user_drives(self):
        """Take Control stops the agent acting, not the workspace showing what
        is on screen."""
        self.arm("example.com")
        controller.take_control()
        res = controller.read("t", config=_CFG)
        self.assertTrue(res["ok"])

    def test_pause_blocks_input_and_resume_restores_it(self):
        self.arm("example.com")
        controller.pause()
        self.assertFalse(controller.click("t", "e1", config=_CFG)["ok"])
        controller.resume()
        self.assertTrue(controller.click("t", "e1", config=_CFG)["ok"])

    def test_stop_bumps_the_generation(self):
        self.arm("example.com")
        before = controller.generation()
        controller.stop()
        self.assertGreater(controller.generation(), before)

    def test_in_flight_op_is_discarded_after_stop(self):
        """An op issued before Stop must not land afterwards — this is what
        makes Stop actually stop rather than just stop issuing new work."""
        self.arm("example.com")
        issued_at = controller.generation()
        controller.stop()
        self.fake.ops.clear()

        res = controller.click("t", "e1", config=_CFG, generation_at_issue=issued_at)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "cancelled")
        self.assertEqual(self.fake.ops, [])

    def test_wait_returns_early_when_stopped(self):
        self.arm("example.com")
        import threading

        threading.Timer(0.05, controller.stop).start()
        res = controller.wait(5.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "cancelled")


class TestCapabilityGate(_BrowserCase):
    def test_browser_capability_off_makes_the_verbs_unavailable(self):
        """Disabled must be technically unavailable, not merely hidden."""
        self.arm("example.com")
        cfg = {"supercomputer_enabled": True, "supercomputer_browser_enabled": False}
        self.fake.ops.clear()
        res = controller.open_url("https://example.com", config=cfg)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "browser_capability_disabled")
        self.assertEqual(self.fake.ops, [])

    def test_master_kill_switch_stops_everything(self):
        self.arm("example.com")
        cfg = {"supercomputer_enabled": False}
        self.assertFalse(controller.read("t", config=cfg)["ok"])
        self.assertFalse(controller.open_url("https://example.com", config=cfg)["ok"])


class TestNavigationGate(_BrowserCase):
    def test_out_of_scope_domain_never_reaches_the_browser_when_locked_down(self):
        self.arm("example.com")
        self.fake.ops.clear()
        cfg = dict(_CFG, supercomputer_open_navigation=False)
        res = controller.open_url("https://evil.example/", config=cfg)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "domain_not_in_task_scope")
        self.assertEqual(self.fake.ops, [])

    def test_a_sensitive_domain_never_reaches_the_browser_unasked(self):
        """Open navigation does not extend to somewhere private: an inbox is
        refused at the controller, before any op is emitted."""
        self.arm("example.com")
        self.fake.ops.clear()
        res = controller.open_url("https://mail.google.com/", config=_CFG)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "needs_scope_permission")
        self.assertEqual(self.fake.ops, [])

    def test_private_url_never_reaches_the_browser(self):
        self.arm("example.com", "192.168.1.1")
        self.fake.ops.clear()
        res = controller.open_url("http://192.168.1.1/", config=_CFG)
        self.assertFalse(res["ok"])
        self.assertEqual(self.fake.ops, [])

    def test_sensitive_text_never_reaches_the_browser(self):
        self.arm("example.com")
        self.fake.ops.clear()
        res = controller.type_text("t", "e1", "my password is hunter2", config=_CFG)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "refused_sensitive_text")
        self.assertEqual(self.fake.ops, [])


class TestSessionIsolation(_BrowserCase):
    def test_the_session_tag_is_fresh_per_session(self):
        first = controller.partition()
        self.assertTrue(first.startswith("syrix-supercomputer-"))
        controller.shutdown(config=_CFG)
        self.assertNotEqual(controller.partition(), first)

    def test_the_real_profile_is_reported_not_invented(self):
        """The Electron module owns the session; Python must report what it says
        rather than its own tag, or the HUD shows a partition nobody is using."""
        self.assertEqual(controller.browser_partition(), "")
        self.assertFalse(controller.shared_profile())
        controller.tabs(config=_CFG)
        self.assertEqual(controller.browser_partition(), "persist:syrix-hud-browser")
        self.assertTrue(controller.shared_profile())
        snap = controller.snapshot()
        self.assertEqual(snap["partition"], "persist:syrix-hud-browser")
        self.assertNotEqual(snap["session_tag"], snap["partition"])

    def test_shutdown_forgets_the_reported_profile(self):
        controller.tabs(config=_CFG)
        controller.shutdown(config=_CFG)
        self.assertEqual(controller.browser_partition(), "")
        self.assertFalse(controller.shared_profile())

    def test_shutdown_clears_state_even_without_a_renderer(self):
        """A dead HUD must not leave this module believing a browser is live."""
        self.arm("example.com")

        def _dead(*a, **kw):
            raise RuntimeError("renderer gone")

        controller.send = _dead
        controller.shutdown(config=_CFG)
        self.assertEqual(controller.control_state(), controller.CONTROL_CLOSED)
        self.assertFalse(controller.is_agent_allowed())
        self.assertIsNone(controller.contract())


if __name__ == "__main__":
    unittest.main()
