"""Shared fixtures for the syrix_connectors test files.

Guarantees every test runs fully isolated from the live system:

* ``TempStateMixin`` redirects ``syrix_connectors.paths.connectors_dir`` to a
  fresh temp dir (state file, tokens, audit log all land there) and points
  ``activity_feed._HUD_STATE_PATH`` at a temp file so no test ever writes the
  user's real hud_state.json.
* A stub ``syrix_host`` module is installed in ``sys.modules`` so the lazy
  ``import syrix_host`` inside events/hud_api/scheduler resolves to a
  40-line stub instead of the 60k-line runtime.
* ``FakeCrypt`` replaces DPAPI with a reversible XOR so secure_store tests
  do not depend on pywin32.
* ``FakeConnector`` + ``register_fake``/``unregister_fake`` add a synthetic
  connector to the registry catalogue for manager/scheduler/tool tests.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import types
from collections import OrderedDict
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# NOTE (2026-07-13): the syrix_host stub is installed PER-TEST in
# TempStateMixin.setUp and REMOVED in tearDown — never at module import.
# A module-level `sys.modules['syrix_host'] = stub` leaked: every later
# test in the suite that imports the real 40k-line syrix_host (STT, deep
# attach, routing smoke) got the 40-line stub instead and failed. The
# connector source only lazy-imports syrix_host inside functions, so a
# per-test stub is sufficient and fully isolated.


def _make_jc_stub() -> types.ModuleType:
    """Fresh syrix_host stub for one test (island + hud-state capture)."""
    stub = types.ModuleType("syrix_host")
    stub.__is_test_stub__ = True
    stub._island_calls = []
    stub._hud_updates = []

    def _publish_island_notice(text, **kw):  # noqa: ANN001
        stub._island_calls.append((text, kw))
        return True

    def _syrix_update_hud_state(fields):  # noqa: ANN001
        stub._hud_updates.append(dict(fields))
        return True

    stub._publish_island_notice = _publish_island_notice
    stub._syrix_update_hud_state = _syrix_update_hud_state
    return stub

from syrix_connectors import paths as conn_paths  # noqa: E402
from syrix_connectors import registry as conn_registry  # noqa: E402
from syrix_connectors import secure_store as conn_store  # noqa: E402
from syrix_connectors.base import (  # noqa: E402
    HEALTH_AVAILABLE,
    HEALTH_CONNECTED,
    PERM_DELETE,
    PERM_READ,
    PERM_SEND,
    RISK_CONFIRM,
    RISK_NOT_IMPLEMENTED,
    SCHEMA_VERSION,
    ActionResult,
    BackgroundTask,
    ConnectorAction,
    ConnectorBase,
)


class FakeCrypt:
    """Reversible stand-in for win32crypt (XOR + prefix so corruption shows)."""

    _KEY = 0x5A
    _MAGIC = b"FAKEDPAPI:"

    def CryptProtectData(self, plain, desc, entropy, a, b, flags):  # noqa: N802
        return self._MAGIC + bytes(c ^ self._KEY for c in plain)

    def CryptUnprotectData(self, blob, entropy, a, b, flags):  # noqa: N802
        if not blob.startswith(self._MAGIC):
            raise ValueError("corrupted blob")
        return ("desc", bytes(c ^ self._KEY for c in blob[len(self._MAGIC):]))


class TempStateMixin:
    """setUp/tearDown: isolate all connector state + HUD writes to a temp dir."""

    def setUp(self):  # noqa: N802
        super().setUp()
        self._tmp = tempfile.mkdtemp(prefix="syrix_conn_test_")
        # Install a per-test syrix_host stub, saving whatever was there so
        # tearDown restores it (real module or absent) — never leak the stub
        # into the rest of the suite.
        self._prev_jc = sys.modules.get("syrix_host")
        sys.modules["syrix_host"] = _make_jc_stub()
        self._orig_dir_fn = conn_paths.connectors_dir
        conn_paths.connectors_dir = lambda: self._ensure_tmp()
        # Never let activity_feed resolve to the real hud_state.json.
        from syrix_core_v2 import activity_feed as _feed

        self._feed = _feed
        self._orig_feed_path = _feed._HUD_STATE_PATH
        _feed._HUD_STATE_PATH = Path(self._tmp) / "hud_state.json"
        # DPAPI stand-in (tests that want the real thing restore it).
        self._orig_crypt_fn = conn_store._win32crypt
        conn_store._win32crypt = lambda: FakeCrypt()
        conn_registry._reset_for_tests()
        # Manager module state must not leak across tests.
        from syrix_connectors import manager as _mgr

        with _mgr._CONFIRM_LOCK:
            _mgr._PENDING_CONFIRMS.clear()
        with _mgr._AUTH_LOCK:
            _mgr._AUTH_FLOW.clear()
            _mgr._AUTH_THREADS.clear()
            _mgr._CONNECT_LAUNCH_TS.clear()

    def _ensure_tmp(self) -> str:
        os.makedirs(self._tmp, exist_ok=True)
        return self._tmp

    def tearDown(self):  # noqa: N802
        conn_paths.connectors_dir = self._orig_dir_fn
        conn_store._win32crypt = self._orig_crypt_fn
        self._feed._HUD_STATE_PATH = self._orig_feed_path
        conn_registry._reset_for_tests()
        unregister_fake()
        # Restore the real syrix_host (or its absence) — never leak the stub.
        if self._prev_jc is not None:
            sys.modules["syrix_host"] = self._prev_jc
        else:
            sys.modules.pop("syrix_host", None)
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()


class FakeConnector(ConnectorBase):
    """Synthetic connector exercising every framework gate."""

    id = "fakeconn"
    name = "FakeConn"
    description = "Test connector"
    icon = "fake"
    category = "test"
    auth_kind = "none"
    schema_version = SCHEMA_VERSION
    background_tasks = (
        BackgroundTask(key="tick", label="Tick", description="test task",
                       interval_s=60.0, default_enabled=False),
    )

    def __init__(self) -> None:
        self.executed = []
        self.task_runs = []
        self.connected_flag = False
        self.raise_exc = None       # exception instance to raise from execute
        self.execute_sleep = 0.0
        self._actions = OrderedDict()
        self._actions["fk_read"] = ConnectorAction(
            key="fk_read", label="Read Things", description="read stuff",
            verb=PERM_READ, timeout_s=2.0,
            tool_schema={"type": "function",
                         "function": {"name": "fk_read", "description": "read",
                                      "parameters": {"type": "object",
                                                     "properties": {}, "required": []}}},
        )
        self._actions["fk_send"] = ConnectorAction(
            key="fk_send", label="Send Things", description="send stuff",
            verb=PERM_SEND, risk=RISK_CONFIRM, timeout_s=2.0,
            tool_schema={"type": "function",
                         "function": {"name": "fk_send", "description": "send",
                                      "parameters": {"type": "object",
                                                     "properties": {}, "required": []}}},
        )
        self._actions["fk_never"] = ConnectorAction(
            key="fk_never", label="Never Things", description="declared only",
            verb=PERM_DELETE, risk=RISK_NOT_IMPLEMENTED,
            tool_schema={"type": "function",
                         "function": {"name": "fk_never", "description": "never",
                                      "parameters": {"type": "object",
                                                     "properties": {}, "required": []}}},
        )

    def actions(self):
        return self._actions

    def execute(self, action_key, args, *, config, request_id=""):
        if self.execute_sleep:
            time.sleep(self.execute_sleep)
        if self.raise_exc is not None:
            raise self.raise_exc
        self.executed.append((action_key, dict(args)))
        return ActionResult(ok=True, content=f"ran {action_key}", count=1)

    def connect(self, *, config, progress=None):
        self.connected_flag = True
        return True, "fak…@test.com"

    def disconnect(self, *, config):
        self.connected_flag = False

    def health(self, *, config):
        return HEALTH_CONNECTED if self.connected_flag else HEALTH_AVAILABLE

    def run_task(self, task_key, *, config):
        self.task_runs.append(task_key)
        return f"task {task_key} ran"


_FAKE_ENTRY = conn_registry.CatalogueEntry(
    id="fakeconn", name="FakeConn", description="Test connector",
    icon="fake", category="test", implemented=True,
    import_path="tests.connectors_testkit:FakeConnector",
)


def register_fake(instance=None) -> FakeConnector:
    """Add FakeConn to the live catalogue + instance cache; returns instance."""
    inst = instance or FakeConnector()
    if _FAKE_ENTRY.id not in conn_registry._BY_ID:
        conn_registry.CATALOGUE.append(_FAKE_ENTRY)
        conn_registry._BY_ID[_FAKE_ENTRY.id] = _FAKE_ENTRY
    with conn_registry._LOCK:
        conn_registry._INSTANCES[_FAKE_ENTRY.id] = inst
    return inst


def unregister_fake() -> None:
    conn_registry._BY_ID.pop(_FAKE_ENTRY.id, None)
    conn_registry.CATALOGUE[:] = [e for e in conn_registry.CATALOGUE
                                  if e.id != _FAKE_ENTRY.id]
    with conn_registry._LOCK:
        conn_registry._INSTANCES.pop(_FAKE_ENTRY.id, None)


def mark_connected(inst) -> None:
    """Persist connected state + defaults for a connector instance."""
    from syrix_connectors import permissions

    if isinstance(inst, FakeConnector):
        inst.connected_flag = True
    permissions.set_connected(inst, "fak…@test.com")
