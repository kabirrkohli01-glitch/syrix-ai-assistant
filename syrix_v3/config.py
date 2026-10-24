"""Settings and secrets, loaded once, with no dependency on the legacy host.

v3 has to run three ways: inside the running application, inside the probe,
and inside a bare test process. Only the first has already had ``.env`` read
into the environment by someone else, so this module does it itself and is
idempotent about it — an existing environment variable always wins over the
file, which keeps a shell override working.

Precedence for a setting is environment, then ``config.json``, then the
caller's default. Secrets are only ever read from the environment; a value in
``config.json`` is a setting, never a key.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_ROOT, "config.json")
_ENV_PATH = os.path.join(_ROOT, ".env")

_lock = threading.Lock()
_settings: Optional[Dict[str, Any]] = None


def _load_env_file() -> None:
    """Read ``.env`` into the environment without overwriting what is there."""
    try:
        with open(_ENV_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not name or name in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[name] = value


def _load_config_file() -> Dict[str, Any]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def settings() -> Dict[str, Any]:
    """The merged settings dictionary. Loaded once per process."""
    global _settings
    if _settings is not None:
        return _settings
    with _lock:
        if _settings is None:
            _load_env_file()
            _settings = _load_config_file()
    return _settings


def get(name: str, default: Any = None) -> Any:
    env = os.environ.get(name.upper())
    if env not in (None, ""):
        return env
    value = settings().get(name)
    return default if value is None else value


def flag(name: str, default: bool = False) -> bool:
    value = get(name, None)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def secret(env_name: str) -> str:
    """A credential, from the environment only."""
    settings()
    return str(os.environ.get(env_name) or "").strip()


def root() -> str:
    return _ROOT


def reset_for_tests() -> None:
    global _settings
    with _lock:
        _settings = None
