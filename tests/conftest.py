"""Session-wide test isolation (2026-07-19 audit RC6).

The suite exercises the real spine, so without isolation every pytest run
wrote into the PRODUCTION runtime dirs — `test-safety-regression` rids,
"provider meltdown" markers and OneDrive-era stack traces all landed in
%LOCALAPPDATA%/SYRIX/logs/syrix.log and otel_spans.jsonl, degrading the
log-first debugging workflow the project relies on.

syrix_paths already honours SYRIX_STATE_DIR as its root override (state +
logs + otel all hang off it). Point it at a per-run temp dir BEFORE any
SYRIX module is imported. conftest.py loads before test modules, so plain
module-level code is early enough; sitecustomize-level tricks are not
needed.

Tests that need the real repo files (config.json, tests/golden/*) use
repo-relative paths and are unaffected. Tests that build their own state
(connectors_testkit, chat-session temp stores) keep working — they override
paths themselves on top of this baseline.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_TEST_STATE_DIR = tempfile.mkdtemp(prefix="syrix_test_state_")
os.environ["SYRIX_STATE_DIR"] = _TEST_STATE_DIR

# 2026-07-28 — no test may launch a real browser. The web gateway's rendered
# tier is reachable from any test that exercises atlas.reader, and once the
# @playwright/mcp sidecar was installed those tests started spawning Chromium
# and fetching live pages: slow, flaky, and quietly networked. A test that
# wants to exercise the browser tier patches the seam; nothing gets there by
# accident. Honoured by syrix_web.browser_mcp.available().
os.environ["SYRIX_NO_BROWSER"] = "1"

# Same reasoning for the keyed search lanes. Tavily and Exa read real keys from
# .env, so once they became first-class ATLAS lanes a pytest run started making
# live billed API calls — and two ranking tests began asserting against
# whatever the live web returned that minute. Honoured by
# syrix_core_v2.atlas.retrieve._keyed_lane_available().
os.environ["SYRIX_NO_KEYED_SEARCH"] = "1"


@atexit.register
def _cleanup_test_state_dir() -> None:
    shutil.rmtree(_TEST_STATE_DIR, ignore_errors=True)
