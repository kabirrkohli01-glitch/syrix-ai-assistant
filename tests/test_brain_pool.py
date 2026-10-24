"""Offline unit tests for syrix_core_v2.brain_pool — ledger, rotation, cooldowns.

No network: the client transport is monkeypatched per test. The quota
exhaustion / 429 / outage drills here are the offline halves of the Phase 1
exit-criteria drills (the live halves run via tools/brain_pool_drill.py).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

# Isolate all state/cache writes before importing the package.
_TMP = tempfile.mkdtemp(prefix="syrix_brain_pool_test_")
os.environ["SYRIX_STATE_DIR"] = _TMP

import syrix_paths  # noqa: E402

syrix_paths._reset_for_tests()

from syrix_core_v2.brain_pool import client as bp_client  # noqa: E402
from syrix_core_v2.brain_pool import providers as bp_providers  # noqa: E402
from syrix_core_v2.brain_pool.ledger import QuotaLedger, estimate_tokens  # noqa: E402
from syrix_core_v2.brain_pool.pool import BrainPool, PoolExhausted  # noqa: E402

_MSGS = [{"role": "user", "content": "hello there"}]


# Every provider key env in the registry. "No keys available" has to mean ALL
# of them, or adding a lane later breaks these tests instead of the code.
_KEY_ENVS = ("GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY",
             "GITHUB_TOKEN", "NVIDIA_API_KEY", "GEMINI_API_KEY",
             "MISTRAL_API_KEY")


def _mk_pool(env=None, cfg=None):
    env = {
        "GROQ_API_KEY": "test-groq",
        "OPENROUTER_API_KEY": "test-or",
        "NVIDIA_API_KEY": "test-nim",
        "CEREBRAS_API_KEY": "",
        "GITHUB_TOKEN": "",
        **(env or {}),
    }
    patcher = mock.patch.dict(os.environ, env)
    patcher.start()
    pool = BrainPool(cfg or {"brain_pool_cache_ttl_s": 0.0})
    return pool, patcher


class LedgerTests(unittest.TestCase):
    def test_headroom_blocks_at_margin(self):
        led = QuotaLedger(path=os.path.join(_TMP, "led1.json"))
        caps = bp_providers.Caps(rpm=10)
        # 90% of 10 = 9 requests allowed; the 10th must be refused pre-emptively.
        for _ in range(9):
            ok, _why = led.headroom("k", caps, 10)
            self.assertTrue(ok)
            led.record("k", 10)
        ok, why = led.headroom("k", caps, 10)
        self.assertFalse(ok)
        self.assertEqual(why, "rpm")

    def test_tpm_budget(self):
        led = QuotaLedger(path=os.path.join(_TMP, "led2.json"))
        caps = bp_providers.Caps(tpm=1000)
        led.record("k", 800)
        ok, why = led.headroom("k", caps, 200)  # 800+200 > 900 margin
        self.assertFalse(ok)
        self.assertEqual(why, "tpm")
        ok, _ = led.headroom("k", caps, 50)  # 850 <= 900
        self.assertTrue(ok)

    def test_daily_persists_across_instances(self):
        path = os.path.join(_TMP, "led3.json")
        led = QuotaLedger(path=path)
        caps = bp_providers.Caps(rpd=10)
        for _ in range(5):
            led.record("k", 1)
        led.flush()
        led2 = QuotaLedger(path=path)
        snap = led2.snapshot("k", caps)
        self.assertEqual(snap["rpd_used"], 5)

    def test_estimate_tokens(self):
        est = estimate_tokens([{"role": "user", "content": "x" * 350}], 100)
        self.assertEqual(est, 200)  # 350/3.5 + 100


class RotationTests(unittest.TestCase):
    def test_overall_timeout_is_shared_across_rotating_lanes(self):
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        seen = []

        def slow_failure(**kw):
            seen.append(kw["timeout_s"])
            time.sleep(0.025)
            raise bp_client.ProviderTimeout("slow lane")

        started = time.monotonic()
        with mock.patch.object(bp_client, "chat", side_effect=slow_failure):
            with self.assertRaises(PoolExhausted):
                pool.chat("T1_FAST", _MSGS, timeout_s=20.0,
                          overall_timeout_s=0.04, use_cache=False)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.15)
        self.assertTrue(seen)
        # pool.py floors the per-lane timeout at 0.05s. When the remaining
        # budget lands a hair above that floor, `max(0.05, min(lane, remaining))`
        # returns `remaining` — e.g. 0.05000000000018 — so an exact <= 0.05
        # comparison flakes on float drift. The invariant is "never meaningfully
        # above the floor", not "bit-identical to it".
        self.assertLessEqual(max(seen), 0.05 + 1e-9)

    def test_nvidia_is_available_as_a_fast_fallback_lane(self):
        """NIM carries the rotation once Groq's daily tokens are spent, because
        it has no token cap at all — so every tier must offer a model there.

        This used to name meta/llama-3.3-70b-instruct. That id now answers
        HTTP 410 Gone and was removed: pinning a specific model id makes the
        test assert the catalogue rather than the behaviour, and a retired id
        costs the rotation a timeout every time it is tried. What matters is
        that the lane is populated, not which model populates it.
        """
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        models = pool.registry.providers["nvidia_nim"].models
        for tier in (bp_providers.T1_FAST, bp_providers.T2_BALANCED,
                     bp_providers.T3_STRONG):
            self.assertIn(tier, models)
            self.assertTrue(models[tier], f"nvidia_nim has no model for {tier}")

    def test_fast_policy_does_not_predoom_local_fallback_timeout(self):
        pool, patcher = _mk_pool(
            env=dict.fromkeys(_KEY_ENVS, ""),
            cfg={"brain_pool_cache_ttl_s": 0.0,
                 "brain_pool_ollama_min_timeout_s": 12.0},
        )
        self.addCleanup(patcher.stop)
        seen = []

        def fake_chat(**kw):
            seen.append(kw["timeout_s"])
            return bp_client.ChatResult(text="local", usage_tokens=5,
                                        model=kw["model"])

        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            result = pool.chat("T1_FAST", _MSGS, timeout_s=4.0,
                               use_cache=False)
        self.assertEqual(result.provider, "ollama")
        self.assertGreaterEqual(seen[0], 12.0)

    def test_a_large_reply_reservation_is_not_predoomed_by_a_flat_ceiling(self):
        """A lane's stall ceiling must account for what the lane was asked for.

        The per-provider timeouts were measured against ordinary requests and
        then applied to every request regardless of size, so one number bounded
        both a 1,600-token reply and an 8,192-token one. The large request
        cannot be produced inside the small ceiling on any lane, which turned a
        stall guard into a guaranteed failure: measured, the deep mode timed out
        on the only provider with no token cap and fell through to the local
        model on every turn.

        Ordinary-sized requests must keep the measured ceiling exactly — the
        point is to stop pre-dooming big ones, not to let every lane hang
        longer.
        """
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        specs = {s.name: s for s in pool.registry.available()}
        spec = specs["nvidia_nim"]
        flat = spec.timeout_strong_s

        ordinary = pool._lane_timeout(spec, bp_providers.T3_STRONG, None, 1600)
        large = pool._lane_timeout(spec, bp_providers.T3_STRONG, None, 8192)

        self.assertEqual(ordinary, flat)
        self.assertGreater(large, flat)
        # The local lane keeps its own floor and is not scaled: it is already
        # the slowest per token and already clamped for that reason.
        local = specs["ollama"]
        self.assertEqual(
            pool._lane_timeout(local, bp_providers.T3_STRONG, None, 8192),
            pool._lane_timeout(local, bp_providers.T3_STRONG, None, 1600))

    def test_429_rotates_and_cools_down(self):
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        calls = []

        def fake_chat(**kw):
            calls.append(kw["base_url"])
            if "groq" in kw["base_url"]:
                raise bp_client.ProviderHTTPError(429, "rate limited", retry_after_s=33.0)
            return bp_client.ChatResult(text="answer from lane", usage_tokens=42, model=kw["model"])

        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            res = pool.chat("T2_BALANCED", _MSGS, use_cache=False)
        self.assertEqual(res.text, "answer from lane")
        self.assertNotIn("groq", res.provider)
        self.assertTrue(any("groq" in c for c in calls))
        # groq lane must now be in cooldown with the Retry-After value.
        status = pool.status()
        groq = next(p for p in status["providers"] if p["name"] == "groq")
        cooled = [l for l in groq["lanes"] if l.get("cooldown_reason") == "rate_limited"]
        self.assertTrue(cooled)
        self.assertAlmostEqual(cooled[0]["cooldown_s"], 33.0, delta=2.0)
        # Next call must NOT touch groq at all.
        calls.clear()
        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            pool.chat("T2_BALANCED", _MSGS, use_cache=False)
        self.assertFalse(any("groq" in c for c in calls))

    def test_retired_model_rotates_once_then_is_benched(self):
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        calls = []

        def fake_chat(**kw):
            calls.append(kw["base_url"])
            if "groq" in kw["base_url"]:
                raise bp_client.ProviderHTTPError(410, "model retired")
            return bp_client.ChatResult(text="fallback", usage_tokens=12,
                                        model=kw["model"])

        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            self.assertEqual(pool.chat("T2_BALANCED", _MSGS,
                                       use_cache=False).text, "fallback")
        status = pool.status()
        groq = next(p for p in status["providers"] if p["name"] == "groq")
        retired = [l for l in groq["lanes"]
                   if l.get("cooldown_reason") == "model_unavailable_http_410"]
        self.assertTrue(retired)
        self.assertGreater(retired[0]["cooldown_s"], 23 * 60 * 60)

        calls.clear()
        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            pool.chat("T2_BALANCED", _MSGS, use_cache=False)
        self.assertFalse(any("groq" in c for c in calls))

    def test_budget_exhaustion_rotates_preemptively(self):
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        # Burn the groq strong lane's RPM budget directly in the ledger.
        spec = pool.registry.providers["groq"]
        model = spec.models[bp_providers.T3_STRONG][0]
        key = spec.ledger_key(model)
        for _ in range(40):
            pool.ledger.record(key, 10)

        def fake_chat(**kw):
            self.assertNotIn("groq", kw["base_url"])  # pre-emptive: groq never called
            return bp_client.ChatResult(text="ok", usage_tokens=10, model=kw["model"])

        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            res = pool.chat("T3_STRONG", _MSGS, use_cache=False)
        self.assertEqual(res.text, "ok")

    def test_total_outage_degrades_to_ollama_then_raises(self):
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        attempts = []

        def fake_chat(**kw):
            attempts.append(kw["base_url"])
            raise bp_client.ProviderNetworkError("ConnectionError")

        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            with self.assertRaises(PoolExhausted):
                pool.chat("T2_BALANCED", _MSGS, use_cache=False)
        # Ollama (the local floor) must have been attempted last.
        self.assertTrue(any("11434" in a for a in attempts))
        self.assertIn("11434", attempts[-1])

    def test_pinned_provider_tried_first(self):
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        first = []

        def fake_chat(**kw):
            first.append(kw["base_url"])
            return bp_client.ChatResult(text="pinned", usage_tokens=5, model=kw["model"])

        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            res = pool.chat("T2_BALANCED", _MSGS, use_cache=False, pinned_provider="nvidia_nim")
        self.assertIn("nvidia", first[0])
        self.assertEqual(res.provider, "nvidia_nim")

    def test_tools_restricted_to_capable_lanes(self):
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        seen = []

        def fake_chat(**kw):
            seen.append(kw["base_url"])
            return bp_client.ChatResult(text="t", usage_tokens=5, model=kw["model"])

        tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            pool.chat("T2_BALANCED", _MSGS, tools=tools, use_cache=False)
        # openrouter (supports_tools=False) must never receive a tools call.
        self.assertFalse(any("openrouter" in s for s in seen))

    def test_cache_serves_repeat_question(self):
        pool, patcher = _mk_pool(cfg={"brain_pool_cache_ttl_s": 600.0})
        self.addCleanup(patcher.stop)
        n = {"calls": 0}

        def fake_chat(**kw):
            n["calls"] += 1
            return bp_client.ChatResult(text="cached answer", usage_tokens=5, model=kw["model"])

        with mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            r1 = pool.chat("T2_BALANCED", _MSGS)
            r2 = pool.chat("T2_BALANCED", _MSGS)
        self.assertEqual(n["calls"], 1)
        self.assertFalse(r1.from_cache)
        self.assertTrue(r2.from_cache)
        self.assertEqual(r2.text, "cached answer")

    def test_missing_key_means_unavailable(self):
        pool, patcher = _mk_pool(env=dict.fromkeys(_KEY_ENVS, ""))
        self.addCleanup(patcher.stop)
        avail = {p.name for p in pool.registry.available()}
        self.assertEqual(avail, {"ollama"})

    def test_tier_normalization(self):
        self.assertEqual(bp_providers.normalize_tier("fast"), bp_providers.T1_FAST)
        self.assertEqual(bp_providers.normalize_tier("strong"), bp_providers.T3_STRONG)
        self.assertEqual(bp_providers.normalize_tier("standard"), bp_providers.T2_BALANCED)
        self.assertEqual(bp_providers.normalize_tier(None), bp_providers.T2_BALANCED)
        self.assertEqual(bp_providers.normalize_tier("T1_FAST"), bp_providers.T1_FAST)


class ProviderPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.secret_messages = [{
            "role": "user",
            "content": {"result": "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2"},
        }]

    def test_secret_chat_skips_cloud_pins_and_reply_cache(self):
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        destinations = []

        def fake_chat(**kwargs):
            destinations.append(kwargs["base_url"])
            return bp_client.ChatResult(
                text="local", usage_tokens=5, model=kwargs["model"]
            )

        with mock.patch.object(pool.cache, "get") as cache_get, \
                mock.patch.object(pool.cache, "put") as cache_put, \
                mock.patch.object(bp_client, "chat", side_effect=fake_chat):
            result = pool.chat(
                "T2_BALANCED",
                self.secret_messages,
                pinned_provider="groq",
                use_cache=True,
            )

        self.assertEqual(result.provider, "ollama")
        self.assertEqual(len(destinations), 1)
        self.assertIn("127.0.0.1", destinations[0])
        cache_get.assert_not_called()
        cache_put.assert_not_called()

    def test_secret_stream_selects_only_loopback_lane(self):
        pool, patcher = _mk_pool()
        self.addCleanup(patcher.stop)
        destinations = []

        def fake_stream(**kwargs):
            destinations.append(kwargs["base_url"])
            yield "local"

        with mock.patch.object(bp_client, "stream", side_effect=fake_stream):
            chunks = list(pool.stream(
                "T2_BALANCED",
                self.secret_messages,
                pinned_provider="groq",
            ))

        self.assertEqual(chunks, ["local"])
        self.assertEqual(len(destinations), 1)
        self.assertIn("127.0.0.1", destinations[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
