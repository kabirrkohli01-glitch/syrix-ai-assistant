"""Process-wide runtime configuration.

The desktop host loads ``config.json`` into ``CONFIG`` at start-up, and the
packages read their settings from it lazily, each with its own default. Run
standalone (tests, tools), it starts from ``config.json`` in the project root
when one exists and is otherwise empty, so every reader falls back to its
default.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> Dict[str, Any]:
    try:
        with open(_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


CONFIG: Dict[str, Any] = load_config()
