"""The provider-independent model layer.

Two properties carry the weight here:

* **role independence** — nothing is hard-coded to a vendor, so the models can
  be swapped from settings without touching callers;
* **one resident local model at a time** — 8 GB will not hold two 7B models plus
  a vision model, so switching roles must unload before it loads. Getting this
  wrong shows up as an out-of-memory stall halfway through a task.
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

from syrix_supercomputer import models  # noqa: E402


class _ModelCase(unittest.TestCase):
    def setUp(self):
        models._reset_for_tests()
        self.unloaded = []
        self.chats = []
        self._real_unload = models._ollama_unload
        self._real_ensure = models.ensure_ollama
        self._real_chat = models._ollama_chat

        models._ollama_unload = lambda m, c=None: (self.unloaded.append(m) or True)
        models.ensure_ollama = lambda c=None: True

        def _fake_chat(model, messages, *, cfg, timeout_s, max_tokens):
            models._make_resident(model, cfg)
            self.chats.append({"model": model, "messages": messages})
            return (f"reply from {model}", f"ollama:{model}")

        models._ollama_chat = _fake_chat

    def tearDown(self):
        models._ollama_unload = self._real_unload
        models.ensure_ollama = self._real_ensure
        models._ollama_chat = self._real_chat
        models._reset_for_tests()


class TestRoleResolution(_ModelCase):
    def test_roles_resolve_from_config_not_from_code(self):
        cfg = {
            "supercomputer_model_plan": "llama3.1:8b",
            "supercomputer_model_code": "qwen2.5-coder:7b",
            "supercomputer_model_vision": "moondream",
        }
        self.assertEqual(models.model_for(models.ROLE_PLAN, cfg), "llama3.1:8b")
        self.assertEqual(models.model_for(models.ROLE_CODE, cfg), "qwen2.5-coder:7b")
        self.assertEqual(models.model_for(models.ROLE_VISION, cfg), "moondream")

    def test_no_model_is_hard_coded_as_a_default(self):
        """The user chose to benchmark before selecting, so an unset role must not
        quietly resolve to somebody's favourite model."""
        for role in models.ROLES:
            self.assertEqual(models.model_for(role, {}), "")

    def test_the_pool_tier_follows_the_selected_depth(self):
        """The in-mode switcher is not decorative: it picks the brain tier."""
        # Deep by default since 2026-07-26. On this pool T3's first lane
        # (Cerebras zai-glm-4.7, 60k tpm) is both stronger AND faster than T2's
        # (Groq gpt-oss-120b, 12k tpm) — the latter's token ceiling was the
        # direct cause of the "model stopped responding" failures.
        self.assertEqual(models.tier_for_depth(), "T3_STRONG")
        models.set_depth("auto")
        self.assertEqual(models.tier_for_depth(), "T2_BALANCED")
        models.set_depth("spark")
        self.assertEqual(models.tier_for_depth(), "T1_FAST")
        models.set_depth("zenith")
        self.assertEqual(models.tier_for_depth(), "T3_STRONG")

    def test_a_routine_step_drops_to_a_cheaper_tier(self):
        """Multi-brain: the grade lowers the tier, and can never raise it."""
        models.set_depth("zenith")
        self.assertEqual(models.tier_for(models.GRADE_HARD, {}), "T3_STRONG")
        self.assertEqual(models.tier_for(models.GRADE_NORMAL, {}), "T2_BALANCED")
        self.assertEqual(models.tier_for(models.GRADE_ROUTINE, {}), "T1_FAST")

        # The user choosing the fast depth is a ceiling, not a floor: a "hard" step
        # must not silently escalate above what he picked.
        models.set_depth("spark")
        self.assertEqual(models.tier_for(models.GRADE_HARD, {}), "T1_FAST")

    def test_multibrain_can_be_switched_off(self):
        models.set_depth("zenith")
        off = {"supercomputer_multibrain_enabled": False}
        for grade in models.GRADES:
            self.assertEqual(models.tier_for(grade, off), "T3_STRONG")

    def test_unset_plan_falls_back_to_the_shared_brain_pool(self):
        calls = []
        from syrix_supercomputer import models as eb

        real = eb.pool_transport
        eb.pool_transport = lambda tier, msgs, **kw: (calls.append(tier) or ("pooled", "pool"))
        try:
            text, provider = models.chat(models.ROLE_PLAN, [{"role": "user", "content": "hi"}],
                                         config={})
        finally:
            eb.pool_transport = real
        self.assertEqual(text, "pooled")
        # The tier is whatever the depth selector says. Balanced by default:
        # the fast tier was tried and measurably failed the job (it looped on a
        # search instead of reading a result), and a task that fails is not fast.
        self.assertEqual(calls, ["T2_BALANCED"])

    def test_unset_local_only_role_is_simply_unavailable(self):
        text, provider = models.chat(models.ROLE_CODE, [{"role": "user", "content": "hi"}],
                                     config={})
        self.assertEqual(text, "")
        self.assertEqual(provider, "")

    def test_explicit_pool_prefix_routes_to_the_pool(self):
        from syrix_supercomputer import models as eb

        real = eb.pool_transport
        eb.pool_transport = lambda tier, msgs, **kw: ("pooled", "pool")
        try:
            text, _ = models.chat(models.ROLE_CODE, [{"role": "user", "content": "hi"}],
                                  config={"supercomputer_model_code": "pool:any"})
        finally:
            eb.pool_transport = real
        self.assertEqual(text, "pooled")


class TestResidency(_ModelCase):
    def test_a_local_call_becomes_resident(self):
        models.chat(models.ROLE_CODE, [{"role": "user", "content": "x"}],
                    config={"supercomputer_model_code": "qwen2.5-coder:7b"})
        self.assertEqual(models.resident(), "qwen2.5-coder:7b")

    def test_switching_role_unloads_the_previous_model_first(self):
        """The whole point: two 7B models will not co-reside on 8 GB."""
        models.chat(models.ROLE_CODE, [{"role": "user", "content": "x"}],
                    config={"supercomputer_model_code": "qwen2.5-coder:7b"})
        models.chat(models.ROLE_PLAN, [{"role": "user", "content": "y"}],
                    config={"supercomputer_model_plan": "llama3.1:8b"})
        self.assertEqual(self.unloaded, ["qwen2.5-coder:7b"])
        self.assertEqual(models.resident(), "llama3.1:8b")

    def test_reusing_the_same_model_does_not_thrash(self):
        cfg = {"supercomputer_model_code": "qwen2.5-coder:7b"}
        models.chat(models.ROLE_CODE, [{"role": "user", "content": "a"}], config=cfg)
        models.chat(models.ROLE_CODE, [{"role": "user", "content": "b"}], config=cfg)
        self.assertEqual(self.unloaded, [], "an unload/reload per call would be brutal")

    def test_unload_local_releases_and_is_idempotent(self):
        models.chat(models.ROLE_CODE, [{"role": "user", "content": "x"}],
                    config={"supercomputer_model_code": "qwen2.5-coder:7b"})
        self.assertTrue(models.unload_local({}))
        self.assertEqual(models.resident(), "")
        self.assertFalse(models.unload_local({}), "a second unload has nothing to do")

    def test_pool_calls_do_not_take_local_residency(self):
        from syrix_supercomputer import models as eb

        real = eb.pool_transport
        eb.pool_transport = lambda tier, msgs, **kw: ("pooled", "pool")
        try:
            models.chat(models.ROLE_PLAN, [{"role": "user", "content": "x"}], config={})
        finally:
            eb.pool_transport = real
        self.assertEqual(models.resident(), "")


class TestFailureBehaviour(_ModelCase):
    def test_a_dead_model_returns_empty_rather_than_raising(self):
        """A model failure mid-task must degrade to an honest 'I could not',
        never an exception that aborts the turn."""
        models.ensure_ollama = lambda c=None: False
        models._ollama_chat = self._real_chat
        text, provider = models.chat(models.ROLE_CODE, [{"role": "user", "content": "x"}],
                                     config={"supercomputer_model_code": "qwen2.5-coder:7b"})
        self.assertEqual(text, "")
        self.assertEqual(provider, "")

    def test_vision_without_a_configured_model_is_unavailable(self):
        text, _ = models.vision("what is this", "abc", config={})
        self.assertEqual(text, "")

    def test_available_models_is_empty_when_ollama_is_down(self):
        got = models.available_models({"ollama_host": "http://127.0.0.1:1"})
        self.assertEqual(got, [])


class TestSnapshot(_ModelCase):
    def test_snapshot_reports_every_role(self):
        snap = models.snapshot({"supercomputer_model_code": "qwen2.5-coder:7b",
                                "ollama_host": "http://127.0.0.1:1"})
        for role in models.ROLES:
            self.assertIn(role, snap["roles"])
        self.assertEqual(snap["roles"]["code"], "qwen2.5-coder:7b")
        self.assertEqual(snap["roles"]["plan"], "pool")


if __name__ == "__main__":
    unittest.main()
