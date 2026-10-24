"""Central redaction for logs, audits, and HUD-facing error strings (no secrets in files)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Pattern

# Bearer / API-ish headers
_RE_BEARER: Pattern[str] = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+)([\w\-._~+/]+=*)",
    re.MULTILINE,
)
_RE_API_KEY_ASSIGN: Pattern[str] = re.compile(
    r"(?i)\b([A-Z0-9_]*API[_-]?KEY|SECRET|TOKEN|PASSWORD|BEARER)\s*=\s*([^\s&\"']+)",
)
_RE_GMAIL_APP: Pattern[str] = re.compile(
    r'(?i)("gmail_app_password"\s*:\s*")([^"]{4,})(")',
)
_RE_LONG_B64: Pattern[str] = re.compile(r"\b[A-Za-z0-9+/]{120,}={0,2}\b")
# Groq / OpenAI-style tokens often copied into logs by mistake
_RE_GROQ_KEY: Pattern[str] = re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")
_RE_OPENAI_SK: Pattern[str] = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9\-]{20,}\b")
_RE_URL_SECRET: Pattern[str] = re.compile(
    r"(?i)([?&](?:password|token|secret|api_key|access_token)=)([^&\s#'\"]{1,256})"
)
# JSON-formatted key-value pairs containing secrets (e.g. "finnhub_api_key": "abc123")
_RE_JSON_SECRET: Pattern[str] = re.compile(
    r'(?i)"([a-z0-9_]*(?:api[_-]?key|api[_-]?secret|access[_-]?key|secret[_-]?key|token|password|bearer|auth[_-]?key)[a-z0-9_]*)"\s*:\s*"([^"]{4,})"'
)


def redact_text(text: str, *, redact_file_paths: bool = False, root_hint: str = "") -> str:
    if not text:
        return ""
    s = str(text)
    s = _RE_BEARER.sub(r"\1[REDACTED]", s)
    s = _RE_API_KEY_ASSIGN.sub(lambda m: f"{m.group(1)}=[REDACTED]", s)
    s = _RE_GMAIL_APP.sub(r"\1[REDACTED]\3", s)
    s = _RE_LONG_B64.sub("[REDACTED_B64]", s)
    s = _RE_GROQ_KEY.sub("[REDACTED]", s)
    s = _RE_OPENAI_SK.sub("[REDACTED]", s)
    s = _RE_URL_SECRET.sub(r"\1[REDACTED]", s)
    s = _RE_JSON_SECRET.sub(lambda m: f'"{m.group(1)}": "[REDACTED]"', s)
    if redact_file_paths and root_hint:
        rh = root_hint.replace("\\", "/").rstrip("/")
        if rh and len(rh) > 2:
            s = re.sub(re.escape(rh) + r"[/\\][^\s\"']{2,}", "[PATH]", s, flags=re.I)
    return s


def redact_for_log(cfg: Dict[str, Any], message: str) -> str:
    paths = bool(cfg.get("log_redact_file_paths", False))
    root = ""
    try:
        import os

        root = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        pass
    return redact_text(message, redact_file_paths=paths, root_hint=root)
