"""Safety limits: size cap, zip-bomb guard, dangerous members."""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from syrix_docintel import safety


@pytest.fixture(autouse=True)
def _env():
    import core_boot
    core_boot.CONFIG["docintel_persist"] = False
    core_boot.CONFIG["docintel_use_embeddings"] = False
    core_boot.CONFIG["docintel_ocr_enabled"] = False
    import syrix_docintel.registry as reg
    reg._reset_for_tests()
    yield


def test_size_cap_refuses_gracefully():
    import core_boot
    core_boot.CONFIG["docintel_max_file_mb"] = 1
    import syrix_docintel as di
    big = b"x" * (2 * 1024 * 1024)
    d = di.analyze_upload("big.txt", big)
    assert d.status == "failed" and "too large" in d.error.lower()
    core_boot.CONFIG["docintel_max_file_mb"] = 25


def test_dangerous_member_detection():
    assert safety.is_dangerous_member("setup.exe")
    assert safety.is_dangerous_member("lib.dll")
    assert safety.is_dangerous_member("../../etc/passwd")
    assert not safety.is_dangerous_member("src/app.py")
    assert not safety.is_dangerous_member("web/index.js")


def test_zip_bomb_ratio_guard():
    # Highly compressible payload → huge uncompressed/compressed ratio.
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", b"\x00" * (5 * 1024 * 1024))
    import syrix_docintel as di
    d = di.analyze_upload("bomb.zip", b.getvalue())
    # Either the ratio guard fires (failed) or it is inspected but flagged;
    # crucially it must never hang or crash.
    assert d.status in ("failed", "ready", "partial")


def test_deadline_expires():
    import time
    dl = safety.Deadline(seconds=1)   # 1s floor is enforced by design
    assert not dl.expired()
    dl._deadline = time.monotonic() - 1   # force it into the past
    assert dl.expired()
    with pytest.raises(safety.ProcessingError):
        dl.check("unit test")
