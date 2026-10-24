"""SYRIX Supercomputer — a separate agentic working environment.

Supercomputer is NOT an answer style like SPARK or ZENITH. It is a distinct
mode with its own workspace (chat + embedded browser), its own permission
system, its own browser session, and (from Phase 8) its own voice pipeline.

Hard rules for this package — see ``docs/SUPERCOMPUTER_BOUNDARY.md``:

* **Nothing here is imported until the user selects the mode.** The only entry
  point is a lazy import inside one branch of ``syrix_core_v2.runtime_spine``.
  Keep this module free of heavy imports so that staying lazy is cheap;
  ``tests/test_supercomputer_isolation.py`` pins it.
* **Fail closed.** A Supercomputer turn never degrades into a normal answer.
  Every failure path says plainly that nothing was executed.
* **The browser boundary is structural.** Supercomputer drives only the
  BrowserViews it created. It never touches the desktop, other apps, or the
  existing HUD app dock. Those views run in the HUD browser's own persistent
  profile, so pages are signed in as the user — which makes every side effect a
  question of permission rather than of reachability, and is why the gate is
  the thing that has to be right.
* **Authority comes only from the user.** Permission to act is never derived
  from model output, page content, or a stored grant from a previous run.
"""
from __future__ import annotations

# Bumped when the on-disk settings shape changes in a non-backward-compatible
# way. `settings.py` refuses to write a file written by a newer schema.
SCHEMA_VERSION = 1

# HUD state key. Registered in syrix_host's essential-keys allow-list and
# re-injected on every filtered write from `hud_api.last_snapshot()`, the same
# clobber-trap dance `syrix_connectors` needs.
STATE_KEY = "syrix_supercomputer"

# Mode identifier on the wire: `[[SYRIX_MODEL:supercomputer]]`.
MODEL_ID = "supercomputer"

__all__ = ["SCHEMA_VERSION", "STATE_KEY", "MODEL_ID"]
