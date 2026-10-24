"""Images shown with an answer, fetched safely and served from disk.

The interface is not allowed to load images from arbitrary hosts — its
content-security policy lists a handful — and widening that to every https
host would let any page a search turned up make the renderer contact any
server. So images are fetched here instead, through the same URL policy the
web gateway uses (scheme allow-list, DNS pinned to public addresses, every
redirect re-checked), capped in size, confirmed to be an actual image by its
leading bytes, and written to a local cache the interface reads as a
``file:`` URL. The renderer never contacts the image's host at all.

SVG is refused: it is a document format that can carry script, and a raster
thumbnail is all an answer needs.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import os
import time
from typing import Dict, List, Optional
import syrix_obs

_MAX_BYTES = 3_000_000
_MIN_BYTES = 2_000          # anything smaller is a tracking pixel or an icon
_KEEP_FILES = 400
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
}


def _directory() -> str:
    try:
        import syrix_paths
        base = syrix_paths.cache_dir()
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".syrix", "cache")
    path = os.path.join(base, "media")
    os.makedirs(path, exist_ok=True)
    return path


def sniff(data: bytes) -> str:
    """The file extension an image's leading bytes declare, or ""."""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "avif"
    return ""


def file_url(path: str) -> str:
    return "file:///" + path.replace("\\", "/")


def _fetch(url: str, timeout_s: float) -> bytes:
    """The image bytes, or b"" when policy, size or transport refuse."""
    from syrix_web import http_fetch  # noqa: WPS433 — kept plumbing, imported late
    got = http_fetch.fetch_public(url, timeout_s=timeout_s, max_bytes=_MAX_BYTES,
                                  headers=_HEADERS, max_redirects=4)
    if not getattr(got, "ok", False) or not got.body:
        return b""
    return bytes(got.body)


def localize(url: str, *, timeout_s: float = 4.0) -> Optional[str]:
    """A ``file:`` URL for the image at ``url``, or None."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]
    folder = _directory()
    for ext in ("jpg", "png", "gif", "webp", "avif"):
        existing = os.path.join(folder, f"{key}.{ext}")
        if os.path.exists(existing):
            return file_url(existing)
    try:
        data = _fetch(url, timeout_s)
    except Exception:
        return None
    ext = sniff(data)
    if not ext or len(data) < _MIN_BYTES:
        return None
    path = os.path.join(folder, f"{key}.{ext}")
    try:
        with open(path + ".part", "wb") as fh:
            fh.write(data)
        os.replace(path + ".part", path)
    except OSError:
        return None
    return file_url(path)


def localize_many(urls: List[str], *, want: int, deadline_s: float) -> Dict[str, str]:
    """Fetch several at once; the first ``want`` that succeed in time."""
    out: Dict[str, str] = {}
    if not urls or want <= 0:
        return out
    ends = time.monotonic() + deadline_s
    pool = cf.ThreadPoolExecutor(max_workers=min(8, len(urls)), thread_name_prefix="v3-img")
    try:
        futures = {pool.submit(localize, u, timeout_s=max(1.0, deadline_s - 0.2)): u for u in urls}
        try:
            for future in cf.as_completed(futures, timeout=max(0.1, ends - time.monotonic())):
                try:
                    local = future.result()
                except Exception:
                    local = None
                if local:
                    out[futures[future]] = local
                    if len(out) >= want:
                        break
        except cf.TimeoutError as exc:
            syrix_obs.report("v3.media.cache.localize_many", exc, level="debug")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def prune() -> None:
    """Keep the cache to the newest few hundred files."""
    try:
        folder = _directory()
        names = [os.path.join(folder, n) for n in os.listdir(folder)]
        if len(names) <= _KEEP_FILES:
            return
        names.sort(key=lambda p: os.path.getmtime(p))
        for path in names[:len(names) - _KEEP_FILES]:
            try:
                os.remove(path)
            except OSError as exc:
                syrix_obs.report("v3.media.cache.prune", exc, level="debug")
    except OSError as exc:
        syrix_obs.report("v3.media.cache.prune", exc, level="debug")
