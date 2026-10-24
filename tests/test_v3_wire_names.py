"""Every ``wire.X`` that v3 uses exists.

``subagents`` stepped with ``wire.KIND_ZENITH`` while ``wire`` never defined
it, so every question split into parallel parts raised AttributeError on its
first step and the turn fell back to the previous runtime, which answered it
badly and slowly. Nothing else in the suite ran that path.
"""
from __future__ import annotations

import glob
import os
import re

from syrix_v3 import wire

_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "syrix_v3")


def test_every_wire_name_used_in_v3_is_defined():
    used = set()
    for path in glob.glob(os.path.join(_ROOT, "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as fh:
            used |= set(re.findall(r"\bwire\.([A-Za-z_]\w*)", fh.read()))
    assert sorted(n for n in used if not hasattr(wire, n)) == []
