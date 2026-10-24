"""Importing third-party libraries inside the host process.

The host replaces ``subprocess.Popen`` with a function that validates every
command and hides console windows — a safety primitive that stays. A library
that uses ``subprocess.Popen[...]`` in a class annotation evaluates that
subscript while its module is first imported, and a function cannot be
subscripted, so the import fails. ``ddgs`` does exactly this: inside the
running application every DuckDuckGo search died on
``TypeError: 'function' object is not subscriptable`` before sending a
request, while the same code worked in any other process — measured from the
search log, three lanes of four answering and the fourth never trying.

The fix is the one ``syrix_web.browser_mcp`` uses for ``mcp``: for the length
of the import only, ``subprocess.Popen`` is a proxy that can be subscripted
and that forwards every call to the safety wrapper, so nothing launched
during the import escapes validation.
"""
from __future__ import annotations

import subprocess
import threading
from contextlib import contextmanager
from typing import Any, Iterator

_lock = threading.Lock()


@contextmanager
def subscriptable_popen() -> Iterator[None]:
    current = subprocess.Popen
    if isinstance(current, type):
        yield
        return

    class _PopenImportProxy:
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return current(*args, **kwargs)

        def __getitem__(self, _item: Any) -> Any:
            return Any

        def __getattr__(self, name: str) -> Any:
            return getattr(current, name)

    proxy = _PopenImportProxy()
    subprocess.Popen = proxy  # type: ignore[assignment,misc]
    try:
        yield
    finally:
        if subprocess.Popen is proxy:
            subprocess.Popen = current  # type: ignore[misc]


def ddgs() -> Any:
    """The ``ddgs.DDGS`` class, imported safely in any process.

    ``asyncio`` is loaded first and outside the proxy: on Windows it subclasses
    ``subprocess.Popen`` at import, and a subclass of the proxy would outlive
    the import window. In the host it is always already loaded — the bridge
    runs on it — so this is a no-op there.
    """
    import asyncio  # noqa: F401
    import importlib
    with _lock:
        with subscriptable_popen():
            # ``from ddgs import DDGS`` hands back a lazy stand-in that loads
            # the real class on first use — after this window has closed. The
            # submodule holds the real class; importing it here loads it now,
            # and the cached module then serves every other caller too.
            real = importlib.import_module("ddgs.ddgs").DDGS
    return real
