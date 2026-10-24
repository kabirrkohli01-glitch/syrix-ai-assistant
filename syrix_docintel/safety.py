"""Resource + safety limits for document processing.

Everything here is config-driven (``core_boot.CONFIG``) with conservative
defaults so an oversized or hostile file degrades to a clean refusal instead
of an OOM, a hang, or a zip-bomb blow-up. Pure stdlib; never raises on a
config miss.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


def _cfg() -> Dict[str, Any]:
    try:
        import core_boot  # type: ignore
        c = getattr(core_boot, "CONFIG", None)
        if isinstance(c, dict):
            return c
    except Exception:
        return {}
    return {}


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(_cfg().get(key, default))
    except Exception:
        return default


@dataclass
class Limits:
    max_file_bytes: int
    max_pages: int
    max_archive_entries: int
    max_archive_depth: int
    process_timeout_s: int
    # Guards that are not user-facing knobs:
    zip_bomb_ratio: int = 200        # refuse if uncompressed/compressed > this
    zip_bomb_total_bytes: int = 1_500_000_000  # 1.5 GB uncompressed ceiling


def load_limits() -> Limits:
    return Limits(
        max_file_bytes=_cfg_int("docintel_max_file_mb", 25) * 1024 * 1024,
        max_pages=_cfg_int("docintel_max_pages", 300),
        max_archive_entries=_cfg_int("docintel_max_archive_entries", 2000),
        max_archive_depth=_cfg_int("docintel_max_archive_depth", 2),
        process_timeout_s=_cfg_int("docintel_process_timeout_s", 60),
    )


class ProcessingError(Exception):
    """Raised for a graceful, user-explainable refusal (size/bomb/timeout)."""


def check_size(nbytes: int, limits: Optional[Limits] = None) -> None:
    lim = limits or load_limits()
    if nbytes > lim.max_file_bytes:
        mb = lim.max_file_bytes / (1024 * 1024)
        raise ProcessingError(f"File is too large ({nbytes / 1e6:.1f} MB). Limit is {mb:.0f} MB.")


# Compiled/native binaries + installers we refuse to surface out of an archive.
# Note: docintel NEVER executes anything — this is only about not previewing
# opaque binaries and flagging them. Text source (.js/.ps1/.py/...) is safe to
# read and is intentionally NOT listed so codebases inspect fully.
_DANGEROUS_ARCHIVE_EXTS = {
    ".exe", ".dll", ".scr", ".com", ".msi", ".jar", ".apk", ".app",
    ".deb", ".rpm", ".sys", ".cpl", ".so", ".dylib", ".bin",
}


def is_dangerous_member(name: str) -> bool:
    n = str(name or "").lower()
    for ext in _DANGEROUS_ARCHIVE_EXTS:
        if n.endswith(ext):
            return True
    # Path-traversal / absolute members.
    if n.startswith("/") or n.startswith("\\") or ".." in n.replace("\\", "/").split("/"):
        return True
    return False


def check_zip_bomb(infolist, limits: Optional[Limits] = None) -> None:
    """Refuse a ZipInfo list whose expansion ratio or total size is hostile."""
    lim = limits or load_limits()
    total_uncomp = 0
    total_comp = 0
    for zi in infolist:
        total_uncomp += int(getattr(zi, "file_size", 0) or 0)
        total_comp += int(getattr(zi, "compress_size", 0) or 0)
    if total_uncomp > lim.zip_bomb_total_bytes:
        raise ProcessingError("Archive expands to an unsafe size (possible zip bomb).")
    if total_comp > 0 and (total_uncomp / total_comp) > lim.zip_bomb_ratio:
        raise ProcessingError("Archive compression ratio is unsafe (possible zip bomb).")


class Deadline:
    """Cheap wall-clock budget. Extractors poll ``expired()`` between pages."""

    def __init__(self, seconds: Optional[int] = None) -> None:
        secs = seconds if seconds is not None else load_limits().process_timeout_s
        self._deadline = time.monotonic() + max(1, int(secs))

    def expired(self) -> bool:
        return time.monotonic() > self._deadline

    def check(self, what: str = "processing") -> None:
        if self.expired():
            raise ProcessingError(f"Document {what} exceeded the time budget; returned a partial result.")
