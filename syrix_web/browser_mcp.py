"""Persistent ``@playwright/mcp`` sidecar — lazy, bounded, and reapable.

SYRIX already had an MCP client (:class:`syrix_mcp_layer.MCPClientManager`), but
it spawns → initialises → calls → closes for **every operation**. That is right
for a stateless filesystem server and useless for a browser: each page read
would relaunch Chromium, and there would be no page to snapshot after the
navigate returned. This module keeps one session open instead.

Shape, deliberately copied from ``MCPClientManager`` where it was already right:
a private asyncio loop on a daemon thread, and a purely synchronous public API.
The reply path is threaded (``runtime_spine`` has zero coroutines); a coroutine
escaping into it would contend with the HUD bridge's event loop.

Where it differs: the session lives inside one long-running task that owns both
async context managers and services a work queue. The MCP SDK's stdio transport
uses anyio task groups, which must be entered and exited on the *same* task —
holding the contexts open from a helper and closing them from another thread is
the classic "cancel scope in a different task" crash.

Four properties this module is responsible for:

**Lazy** — nothing starts until someone asks for a page. An offline turn must
never spawn Node or Chromium, and that is a test, not an intention.

**Idle-bounded** — after ``web_gateway_idle_shutdown_s`` with no calls the
sidecar shuts itself down. This machine has to stay usable for gaming.

**Reapable** — normal shutdown goes through the SDK, which terminates the
process tree. For the crash case there is a psutil sweep scoped to *our own*
children whose command line contains *our* cli.js path, so it can never touch
another Node process on the machine.

**Constrained** — the sidecar exposes 24 tools including ``browser_evaluate``
and ``browser_run_code_unsafe``. :data:`ALLOWED_TOOLS` is checked before the
JSON-RPC call is made, so arbitrary page-script execution is unreachable from
SYRIX regardless of what the model asks for. CLI flags are defence in depth,
never the boundary.

**Network-guarded** — Chromium receives only a loopback proxy backed by
``PublicOnlyProxy``. The proxy resolves and pins every navigation, redirect and
subresource to an all-public DNS answer; a guard startup failure prevents the
sidecar from launching.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import syrix_obs as obs

from . import audit, child_env, policy
from .public_proxy import PublicOnlyProxy

# The only tools SYRIX may call. Read-only navigation and observation.
# browser_evaluate / browser_run_code_unsafe / browser_click / browser_type are
# absent on purpose: interaction belongs to Supercomputer's gated verbs, and
# script evaluation belongs to nobody.
ALLOWED_TOOLS = frozenset({
    "browser_navigate",
    "browser_navigate_back",
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_wait_for",
    "browser_tabs",
    "browser_close",
    "browser_resize",
})

_DEFAULT_IDLE_S = 120.0
_START_TIMEOUT_S = 45.0
_CALL_TIMEOUT_S = 30.0
_VIEWPORT = "1280,900"
_MCP_IMPORT_LOCK = threading.RLock()


def _stdio_sdk():
    """Import MCP stdio despite SYRIX's callable Popen safety wrapper.

    The MCP Windows helper evaluates ``subprocess.Popen[bytes]`` at import
    time. SYRIX deliberately wraps ``subprocess.Popen`` with a function that
    enforces command policy and hidden windows; functions are callable but not
    subscriptable, so the annotation crashes before the SDK can load. During
    this one import we install a subscriptable *proxy around the same safety
    wrapper*—process launches still pass through every existing gate—then
    restore the exact wrapper immediately.
    """
    with _MCP_IMPORT_LOCK:
        current_popen = subprocess.Popen
        proxy = None
        if not isinstance(current_popen, type):
            class _SafePopenImportProxy:
                def __call__(self, *args, **kwargs):
                    return current_popen(*args, **kwargs)

                def __getitem__(self, _item):
                    return Any

                def __getattr__(self, name):
                    return getattr(current_popen, name)

            proxy = _SafePopenImportProxy()
            subprocess.Popen = proxy  # type: ignore[assignment]
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            return ClientSession, StdioServerParameters, stdio_client
        finally:
            if proxy is not None and subprocess.Popen is proxy:
                subprocess.Popen = current_popen  # type: ignore[assignment]


def _live_config() -> Dict[str, Any]:
    try:
        import core_boot

        return core_boot.CONFIG
    except Exception:
        return {}


def _cfg(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return config if config is not None else _live_config()


def cli_path() -> str:
    """Absolute path to the pinned sidecar entry point.

    Resolved from the repo, never from PATH and never through ``npx`` — a
    production runtime must not fetch a package at request time. ``npx`` stays
    where it belongs, in ``tools/verify_mcp.py``.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(
        root, "hud_electron", "node_modules", "@playwright", "mcp", "cli.js"
    )


def _config_path() -> str:
    """Pinned sidecar config: downloads off, service workers blocked."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "browser_mcp_config.json")


def _node_exe() -> str:
    return "node.exe" if os.name == "nt" else "node"


def _make_stdio_parameters(parameter_type, argv: List[str]):
    """Build sidecar launch parameters without inheriting parent secrets."""
    return parameter_type(
        command=_node_exe(),
        args=argv,
        env=child_env.minimal_child_env(),
    )


def _output_dir() -> str:
    try:
        import syrix_paths

        path = os.path.join(syrix_paths.cache_dir(), "playwright_mcp")
    except Exception:
        path = os.path.join(os.path.expanduser("~"), ".syrix_playwright_mcp")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as exc:
        obs.report("web.browser.outdir", exc, level="debug", path=path)
    return path


def available(config: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """``(ok, reason)`` — is the browser tier usable at all right now?"""
    # Hard environment veto, checked before anything else. The test suite sets
    # it (tests/conftest.py) so no test can reach a real browser by accident,
    # and it is a usable emergency brake outside tests too.
    if os.environ.get("SYRIX_NO_BROWSER", "").strip() not in ("", "0", "false"):
        return False, "browser_disabled_by_env"
    cfg = _cfg(config)
    if not bool(cfg.get("web_gateway_enabled", True)):
        return False, "gateway_disabled"
    if not bool(cfg.get("web_gateway_browser_enabled", True)):
        return False, "browser_disabled"
    try:
        # The browser owns its persistent MCP session and imports the SDK
        # directly in ``_serve``. Depending on syrix_mcp_layer's process-wide
        # import flag here created a false negative after deferred plugin
        # bootstrap: the SDK imported fine later, but the stale flag kept the
        # browser permanently disabled for that process.
        _stdio_sdk()
    except Exception as exc:
        tb = exc.__traceback__
        while tb is not None and tb.tb_next is not None:
            tb = tb.tb_next
        frame = ""
        if tb is not None:
            frame = (
                f"{os.path.basename(tb.tb_frame.f_code.co_filename)}:"
                f"{tb.tb_lineno}:{tb.tb_frame.f_code.co_name}"
            )
        obs.report("web.browser.mcp_import", exc, level="warning", frame=frame)
        return False, "mcp_unavailable"
    if not os.path.exists(cli_path()):
        return False, "sidecar_not_installed"
    return True, ""


class BrowserSidecar:
    """One long-lived ``@playwright/mcp`` process behind a sync API."""

    def __init__(self, *, idle_s: float = _DEFAULT_IDLE_S) -> None:
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._queue: Optional[asyncio.Queue] = None
        self._runner: Optional[asyncio.Future] = None
        self._ready = threading.Event()
        self._fatal = ""
        self._idle_s = float(idle_s)
        self._idle_timer: Optional[threading.Timer] = None
        self._last_used = 0.0
        self._starts = 0
        self._calls = 0
        self._restarts = 0
        self._proxy: Optional[PublicOnlyProxy] = None

    # ── loop plumbing ───────────────────────────────────────────────────
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, daemon=True, name="syrix-web-browser"
            )
            thread.start()
            self._loop, self._thread = loop, thread
            return loop

    def _argv(
        self,
        config: Optional[Dict[str, Any]],
        *,
        proxy_url: str,
    ) -> List[str]:
        cfg = _cfg(config)
        parsed_proxy = urlsplit(str(proxy_url or ""))
        if (parsed_proxy.scheme != "http" or parsed_proxy.hostname != "127.0.0.1"
                or not parsed_proxy.port):
            raise ValueError("public_proxy_required")
        nav_ms = int(float(cfg.get("web_gateway_nav_timeout_s", 20.0)) * 1000)
        # Screenshots are only worth their bytes when the vision tier can read
        # them; with it off, omitting image payloads keeps a snapshot cheap.
        images = "auto" if bool(cfg.get("web_gateway_vision_enabled", False)) else "omit"
        return [
            cli_path(),
            "--config", _config_path(),
            "--isolated",          # in-memory profile: no cookies, no history
            "--headless",
            "--output-dir", _output_dir(),
            # Chromium otherwise has an implicit loopback proxy bypass. The
            # special negative rule removes it so localhost, redirects and all
            # subresources are forced through the public-only network guard.
            "--proxy-server", proxy_url,
            "--proxy-bypass", "<-loopback>",
            "--blocked-origins", ";".join(policy.BLOCKED_ORIGINS),
            "--viewport-size", _VIEWPORT,
            "--timeout-navigation", str(nav_ms),
            "--timeout-action", "8000",
            "--image-responses", images,
        ]

    # ── the session-owning task ─────────────────────────────────────────
    async def _serve(self, argv: List[str], ready: asyncio.Event) -> None:
        ClientSession, StdioServerParameters, stdio_client = _stdio_sdk()

        params = _make_stdio_parameters(StdioServerParameters, argv)
        queue = self._queue
        assert queue is not None
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    ready.set()
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        future, tool, args, timeout = item
                        if future.cancelled():
                            continue
                        try:
                            result = await asyncio.wait_for(
                                session.call_tool(tool, args), timeout
                            )
                            future.set_result(result)
                        except Exception as exc:  # per-call, never fatal
                            if not future.done():
                                future.set_exception(exc)
        except Exception as exc:
            self._fatal = f"{type(exc).__name__}: {exc}"[:200]
            obs.report("web.browser.serve", exc, level="warning")
        finally:
            ready.set()  # unblock a start() that is waiting on a failed launch
            self._ready.clear()
            # Drain: anything still queued would otherwise hang its caller.
            try:
                while queue is not None and not queue.empty():
                    item = queue.get_nowait()
                    if item and not item[0].done():
                        item[0].set_exception(RuntimeError("sidecar_stopped"))
            except Exception as exc:
                obs.report("web.browser.drain", exc, level="debug")

    # ── lifecycle ───────────────────────────────────────────────────────
    def alive(self) -> bool:
        return bool(self._ready.is_set() and self._runner is not None
                    and not self._runner.done() and self._proxy is not None
                    and self._proxy.alive())

    def start(
        self,
        *,
        timeout: float = _START_TIMEOUT_S,
        config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """Launch the sidecar if it is not already up. Idempotent."""
        with self._lock:
            if self.alive():
                return True, ""
            if self._runner is not None or self._proxy is not None:
                # A sidecar without its exact proxy is never reusable. Tear
                # down both halves before attempting a clean guarded restart.
                self.stop(reason="guard_or_sidecar_lost")
            ok, reason = available(config)
            if not ok:
                return False, reason

            guard = PublicOnlyProxy()
            try:
                proxy_url = guard.start()
                if not proxy_url or not guard.alive():
                    raise RuntimeError("network_guard_not_alive")
            except Exception as exc:
                obs.report("web.browser.network_guard", exc, level="warning")
                guard.stop()
                return False, "network_guard_unavailable"
            self._proxy = guard

            try:
                loop = self._ensure_loop()
                self._fatal = ""
                self._ready.clear()
                argv = self._argv(config, proxy_url=proxy_url)
            except Exception as exc:
                obs.report("web.browser.network_guard_config", exc, level="warning")
                self.stop(reason="network_guard_config_failed")
                return False, "network_guard_unavailable"

            async def _boot() -> asyncio.Event:
                self._queue = asyncio.Queue()
                event = asyncio.Event()
                self._runner = asyncio.ensure_future(self._serve(argv, event))
                return event

            t0 = time.time()
            try:
                ready_event = asyncio.run_coroutine_threadsafe(
                    _boot(), loop
                ).result(timeout=10.0)
                asyncio.run_coroutine_threadsafe(
                    ready_event.wait(), loop
                ).result(timeout=timeout)
            except Exception as exc:
                obs.report("web.browser.start", exc, level="warning")
                self._fatal = f"{type(exc).__name__}: {exc}"[:200]
                self.stop()
                return False, "sidecar_start_failed"

            if self._fatal:
                self.stop()
                return False, "sidecar_start_failed"

            self._ready.set()
            self._starts += 1
            self._touch()
            audit.record(
                "sidecar_start", ok=True, ms=(time.time() - t0) * 1000.0,
                tier="browser",
            )
            return True, ""

    def stop(self, *, reason: str = "explicit") -> None:
        """Shut the sidecar down. Safe to call when it is already down."""
        with self._lock:
            self._cancel_idle_timer()
            queue, loop, runner = self._queue, self._loop, self._runner
            guard, self._proxy = self._proxy, None
            self._ready.clear()
            self._queue = None
            self._runner = None

        if queue is not None and loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(
                    queue.put(None), loop
                ).result(timeout=3.0)
            except Exception as exc:
                obs.report("web.browser.stop_signal", exc, level="debug")
        if runner is not None and loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(
                    asyncio.wait_for(asyncio.shield(runner), 8.0), loop
                ).result(timeout=10.0)
            except Exception as exc:
                obs.report("web.browser.stop_wait", exc, level="debug")

        if guard is not None:
            try:
                guard.stop()
            except Exception as exc:
                obs.report("web.browser.network_guard_stop", exc, level="debug")

        killed = sweep_orphans()
        audit.record("sidecar_stop", ok=True, reason=reason, tier="browser",
                     orphans=killed)

    # ── idle management ─────────────────────────────────────────────────
    def _cancel_idle_timer(self) -> None:
        timer, self._idle_timer = self._idle_timer, None
        if timer is not None:
            try:
                timer.cancel()
            except Exception as exc:
                obs.report("web.browser.idle_cancel", exc, level="debug")

    def _touch(self) -> None:
        self._last_used = time.time()
        self._cancel_idle_timer()
        if self._idle_s <= 0:
            return
        timer = threading.Timer(self._idle_s, self._on_idle)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _on_idle(self) -> None:
        if time.time() - self._last_used + 0.5 < self._idle_s:
            return  # a call landed while the timer was firing
        self.stop(reason="idle")

    # ── calls ───────────────────────────────────────────────────────────
    def call(
        self,
        tool: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        timeout: float = _CALL_TIMEOUT_S,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call one allow-listed sidecar tool. Never raises.

        Returns ``{ok, text, reason}``. A URL argument is policy-checked here as
        well as by the caller, because this is the last place before the browser
        and the last place is where a guard has to hold.
        """
        name = str(tool or "")
        if name not in ALLOWED_TOOLS:
            audit.record("tool_refused", ok=False, reason=name, tier="browser")
            return {"ok": False, "text": "", "reason": "tool_not_allowed"}

        args = dict(args or {})
        url = str(args.get("url") or "").strip()
        if url:
            ok, reason = policy.check_fetch(url)
            if not ok:
                audit.record("nav_blocked", url=url, ok=False, reason=reason,
                             tier="browser")
                return {"ok": False, "text": "", "reason": reason}

        started, reason = self.start(config=config)
        if not started:
            return {"ok": False, "text": "", "reason": reason}

        loop, queue = self._loop, self._queue
        if loop is None or queue is None:
            return {"ok": False, "text": "", "reason": "sidecar_not_running"}

        t0 = time.time()

        async def _submit() -> Any:
            future: asyncio.Future = loop.create_future()
            await queue.put((future, name, args, timeout))
            return await future

        try:
            result = asyncio.run_coroutine_threadsafe(
                _submit(), loop
            ).result(timeout=timeout + 10.0)
        except Exception as exc:
            obs.report("web.browser.call", exc, level="debug", tool=name)
            audit.record("tool_error", url=url, ok=False, tier="browser",
                         reason=f"{type(exc).__name__}", ms=(time.time() - t0) * 1000)
            self._note_possible_crash()
            return {"ok": False, "text": "", "reason": "sidecar_call_failed"}

        self._calls += 1
        self._touch()
        parts: List[str] = []
        for block in (getattr(result, "content", None) or []):
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
        joined = "\n".join(parts)
        is_error = bool(getattr(result, "isError", False))
        audit.record(
            "tool_call", url=url, ok=not is_error, tier="browser",
            ms=(time.time() - t0) * 1000.0, chars=len(joined), tool=name,
        )
        return {
            "ok": not is_error,
            "text": joined,
            "reason": "tool_error" if is_error else "",
        }

    def _note_possible_crash(self) -> None:
        """A failed call may mean the process died — verify, don't assume."""
        runner = self._runner
        if runner is not None and runner.done():
            self._restarts += 1
            self.stop(reason="crash")

    # ── introspection ───────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        proxy = self._proxy.status() if self._proxy is not None else {"alive": False}
        return {
            "alive": self.alive(),
            "starts": self._starts,
            "restarts": self._restarts,
            "calls": self._calls,
            "idle_s": self._idle_s,
            "last_used": round(self._last_used, 1),
            "fatal": self._fatal,
            "network_guard": proxy,
        }


def sweep_orphans() -> int:
    """Kill any surviving sidecar process tree we own. Returns the count.

    Scoped twice over: only processes that are *our* descendants, and only
    those whose command line contains *our* cli.js path. It is not possible for
    this to reach another Node process on the machine.
    """
    marker = cli_path().lower()
    killed = 0
    try:
        import psutil
    except Exception:
        return 0
    try:
        me = psutil.Process(os.getpid())
        for child in me.children(recursive=True):
            try:
                cmdline = " ".join(child.cmdline()).lower()
            except Exception:
                continue
            if marker not in cmdline:
                continue
            try:
                for grandchild in child.children(recursive=True):
                    try:
                        grandchild.kill()
                    except Exception as exc:
                        obs.report("web.browser.sweep_child", exc, level="debug")
                child.kill()
                killed += 1
            except Exception as exc:
                obs.report("web.browser.sweep_kill", exc, level="debug")
    except Exception as exc:
        obs.report("web.browser.sweep", exc, level="debug")
    return killed


_SIDECAR: Optional[BrowserSidecar] = None
_SIDECAR_LOCK = threading.Lock()


def sidecar(config: Optional[Dict[str, Any]] = None) -> BrowserSidecar:
    """Process-wide singleton. Constructing it does NOT launch anything."""
    global _SIDECAR
    with _SIDECAR_LOCK:
        if _SIDECAR is None:
            idle = float(_cfg(config).get("web_gateway_idle_shutdown_s",
                                          _DEFAULT_IDLE_S))
            _SIDECAR = BrowserSidecar(idle_s=idle)
        return _SIDECAR


def shutdown(*, reason: str = "shutdown") -> None:
    """Stop the sidecar if one was ever started. Called from SYRIX teardown."""
    global _SIDECAR
    with _SIDECAR_LOCK:
        current, _SIDECAR = _SIDECAR, None
    if current is not None:
        current.stop(reason=reason)
    else:
        sweep_orphans()


def status() -> Dict[str, Any]:
    with _SIDECAR_LOCK:
        current = _SIDECAR
    if current is None:
        return {"alive": False, "starts": 0, "calls": 0}
    return current.status()


def _reset_for_tests() -> None:
    global _SIDECAR
    with _SIDECAR_LOCK:
        current, _SIDECAR = _SIDECAR, None
    if current is not None:
        current.stop(reason="test_reset")


__all__ = [
    "ALLOWED_TOOLS",
    "BrowserSidecar",
    "available",
    "cli_path",
    "shutdown",
    "sidecar",
    "status",
    "sweep_orphans",
]
